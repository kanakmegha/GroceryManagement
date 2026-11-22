from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.discovery.models import DishVideoCache, YouTubeQuotaUsage
from app.modules.discovery.schemas import QuotaReservationResult, RefreshLeaseResult

DEFAULT_DAILY_QUOTA_BUDGET = 9000
DEFAULT_LEASE_TTL_SECONDS = 30
DEFAULT_COOLDOWN_SECONDS = 900
YOUTUBE_PACIFIC_TZ = ZoneInfo("America/Los_Angeles")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def current_youtube_quota_date(now: datetime | None = None) -> date:
    """Return the current YouTube API quota date (midnight America/Los_Angeles)."""
    ref = now or _utcnow()
    return ref.astimezone(YOUTUBE_PACIFIC_TZ).date()


def derive_quota_project_key(
    *,
    quota_project_id: str | None = None,
    api_key: str | None = None,
) -> str:
    """Derive a stable, non-secret quota project identifier (never stores raw API keys)."""
    if quota_project_id and quota_project_id.strip():
        return quota_project_id.strip()[:64]
    if api_key and api_key.strip():
        digest = hashlib.sha256(api_key.strip().encode("utf-8")).hexdigest()[:16]
        return f"key_sha256_{digest}"
    return "default_unconfigured"


async def _insert_on_conflict_do_nothing(
    db: AsyncSession,
    model: type[Any],
    values: dict[str, Any],
    index_elements: list[str],
) -> None:
    bind = db.get_bind()
    dialect_name = bind.dialect.name if bind is not None else "sqlite"
    if dialect_name == "postgresql":
        stmt = (
            postgresql.insert(model)
            .values(**values)
            .on_conflict_do_nothing(index_elements=index_elements)
        )
    else:
        stmt = (
            sqlite.insert(model)
            .values(**values)
            .on_conflict_do_nothing(index_elements=index_elements)
        )
    await db.execute(stmt)


class VideoRefreshCoordinator:
    """
    Database-backed distributed lease coordinator for dish video cache refreshes.

    Session ownership contract:
    - Accepts the application's existing `async_sessionmaker[AsyncSession]` factory
      (e.g., `app.state.session_maker`).
    - Every method opens, commits, and reliably closes its own short-lived
      `AsyncSession` so coordination commits never terminate a caller's
      request-scoped transaction or clear transaction-local PostgreSQL RLS GUCs
      (`app.current_household_id`, `app.current_user_id`).
    - No database transaction is held open across external HTTP calls.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        lease_ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
        cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
    ) -> None:
        self._session_factory = session_factory
        self.lease_ttl_seconds = lease_ttl_seconds
        self.cooldown_seconds = cooldown_seconds
        self._local_locks: dict[tuple[UUID, str], asyncio.Lock] = {}

    def _get_local_lock(self, dish_id: UUID, query_hash: str) -> asyncio.Lock:
        key = (dish_id, query_hash)
        lock = self._local_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._local_locks[key] = lock
        return lock

    async def try_claim_refresh_lease(
        self,
        *,
        dish_id: UUID,
        query_hash: str,
        query_text: str,
        now: datetime | None = None,
    ) -> RefreshLeaseResult:
        """
        Check cache/cooldown and atomically claim the distributed DB refresh lease
        using a dedicated short-lived session owned by this coordinator.
        """
        async with self._get_local_lock(dish_id, query_hash):
            ref_now = now or _utcnow()

            async with self._session_factory() as db:
                existing = (
                    await db.execute(
                        select(DishVideoCache).where(
                            DishVideoCache.dish_id == dish_id,
                            DishVideoCache.query_hash == query_hash,
                        )
                    )
                ).scalar_one_or_none()

                if existing is not None:
                    exp = _ensure_utc(existing.expires_at)
                    if exp is not None and exp > ref_now:
                        return RefreshLeaseResult(
                            acquired=False,
                            cache_hit=True,
                            in_cooldown=False,
                            lock_token=None,
                            video_record_ids=list(existing.video_record_ids),
                        )
                    cd = _ensure_utc(existing.cooldown_until)
                    if cd is not None and cd > ref_now:
                        return RefreshLeaseResult(
                            acquired=False,
                            cache_hit=False,
                            in_cooldown=True,
                            lock_token=None,
                            video_record_ids=list(existing.video_record_ids),
                        )

                await _insert_on_conflict_do_nothing(
                    db,
                    DishVideoCache,
                    {
                        "id": uuid4(),
                        "dish_id": dish_id,
                        "query_hash": query_hash,
                        "query_text": query_text,
                        "video_record_ids": [],
                        "consecutive_failures": 0,
                    },
                    index_elements=["dish_id", "query_hash"],
                )

                token = uuid4()
                lease_expires = ref_now + timedelta(seconds=self.lease_ttl_seconds)

                claim_stmt = (
                    update(DishVideoCache)
                    .where(
                        DishVideoCache.dish_id == dish_id,
                        DishVideoCache.query_hash == query_hash,
                        or_(
                            DishVideoCache.expires_at.is_(None),
                            DishVideoCache.expires_at <= ref_now,
                        ),
                        or_(
                            DishVideoCache.cooldown_until.is_(None),
                            DishVideoCache.cooldown_until <= ref_now,
                        ),
                        or_(
                            DishVideoCache.refresh_locked_until.is_(None),
                            DishVideoCache.refresh_locked_until <= ref_now,
                        ),
                    )
                    .values(
                        refresh_lock_token=token,
                        refresh_locked_until=lease_expires,
                        last_attempt_at=ref_now,
                    )
                    .execution_options(synchronize_session=False)
                )
                result = await db.execute(claim_stmt)
                await db.commit()

                if result.rowcount == 1:
                    return RefreshLeaseResult(
                        acquired=True,
                        cache_hit=False,
                        in_cooldown=False,
                        lock_token=token,
                        video_record_ids=[],
                    )

                refreshed = (
                    await db.execute(
                        select(DishVideoCache).where(
                            DishVideoCache.dish_id == dish_id,
                            DishVideoCache.query_hash == query_hash,
                        )
                    )
                ).scalar_one_or_none()
                if refreshed is not None:
                    exp = _ensure_utc(refreshed.expires_at)
                    if exp is not None and exp > ref_now:
                        return RefreshLeaseResult(
                            acquired=False,
                            cache_hit=True,
                            in_cooldown=False,
                            lock_token=None,
                            video_record_ids=list(refreshed.video_record_ids),
                        )
                return RefreshLeaseResult(
                    acquired=False,
                    cache_hit=False,
                    in_cooldown=False,
                    lock_token=None,
                    video_record_ids=list(refreshed.video_record_ids) if refreshed else [],
                )

    async def complete_refresh_lease(
        self,
        *,
        dish_id: UUID,
        query_hash: str,
        lock_token: UUID,
        video_record_ids: list[str],
        cache_ttl_days: int = 7,
        now: datetime | None = None,
    ) -> bool:
        """
        Populate cached video IDs and release the lease if the caller still owns it,
        using a dedicated short-lived session owned by this coordinator.
        """
        ref_now = now or _utcnow()
        async with self._session_factory() as db:
            stmt = (
                update(DishVideoCache)
                .where(
                    DishVideoCache.dish_id == dish_id,
                    DishVideoCache.query_hash == query_hash,
                    DishVideoCache.refresh_lock_token == lock_token,
                )
                .values(
                    video_record_ids=list(video_record_ids),
                    fetched_at=ref_now,
                    expires_at=ref_now + timedelta(days=cache_ttl_days),
                    cooldown_until=None,
                    consecutive_failures=0,
                    refresh_lock_token=None,
                    refresh_locked_until=None,
                )
                .execution_options(synchronize_session=False)
            )
            res = await db.execute(stmt)
            await db.commit()
            return res.rowcount == 1

    async def release_refresh_lease_on_failure(
        self,
        *,
        dish_id: UUID,
        query_hash: str,
        lock_token: UUID,
        apply_cooldown: bool = True,
        now: datetime | None = None,
    ) -> bool:
        """
        Release the lease safely on failure and optionally apply retry cooldown,
        using a dedicated short-lived session owned by this coordinator.
        """
        ref_now = now or _utcnow()
        cooldown_until = (
            ref_now + timedelta(seconds=self.cooldown_seconds) if apply_cooldown else None
        )
        async with self._session_factory() as db:
            stmt = (
                update(DishVideoCache)
                .where(
                    DishVideoCache.dish_id == dish_id,
                    DishVideoCache.query_hash == query_hash,
                    DishVideoCache.refresh_lock_token == lock_token,
                )
                .values(
                    refresh_lock_token=None,
                    refresh_locked_until=None,
                    cooldown_until=cooldown_until,
                    consecutive_failures=DishVideoCache.consecutive_failures + (
                        1 if apply_cooldown else 0
                    ),
                )
                .execution_options(synchronize_session=False)
            )
            res = await db.execute(stmt)
            await db.commit()
            return res.rowcount == 1


class YouTubeQuotaGuard:
    """
    Atomic reserve-before-call daily quota tracker for YouTube API calls.

    Session ownership contract:
    - Accepts the application's existing `async_sessionmaker[AsyncSession]` factory.
    - Each `reserve_quota` call opens, commits, and closes its own short-lived
      session so quota accounting commits never interfere with a caller's
      household-scoped RLS transaction.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        daily_budget: int = DEFAULT_DAILY_QUOTA_BUDGET,
    ) -> None:
        self._session_factory = session_factory
        self.daily_budget = daily_budget

    async def reserve_quota(
        self,
        *,
        quota_project_key: str,
        cost: int,
        search_calls: int = 1,
        video_detail_calls: int = 0,
        quota_date: date | None = None,
        now: datetime | None = None,
    ) -> QuotaReservationResult:
        """
        Atomically reserve quota units before an external YouTube API call using a
        dedicated short-lived session owned by this guard.

        Conservative reserve-and-burn policy: reserved units are never refunded on
        downstream HTTP failures so concurrent requests cannot exceed daily_budget.
        """
        if cost <= 0:
            raise ValueError("Quota cost must be positive.")

        ref_now = now or _utcnow()
        target_date = quota_date or current_youtube_quota_date(ref_now)

        async with self._session_factory() as db:
            await _insert_on_conflict_do_nothing(
                db,
                YouTubeQuotaUsage,
                {
                    "id": uuid4(),
                    "quota_date": target_date,
                    "quota_project_key": quota_project_key,
                    "units_used": 0,
                    "search_calls": 0,
                    "video_detail_calls": 0,
                    "updated_at": ref_now,
                },
                index_elements=["quota_date", "quota_project_key"],
            )

            stmt = (
                update(YouTubeQuotaUsage)
                .where(
                    YouTubeQuotaUsage.quota_date == target_date,
                    YouTubeQuotaUsage.quota_project_key == quota_project_key,
                    (YouTubeQuotaUsage.units_used + cost) <= self.daily_budget,
                )
                .values(
                    units_used=YouTubeQuotaUsage.units_used + cost,
                    search_calls=YouTubeQuotaUsage.search_calls + search_calls,
                    video_detail_calls=YouTubeQuotaUsage.video_detail_calls + video_detail_calls,
                    updated_at=ref_now,
                )
                .execution_options(synchronize_session=False)
            )
            res = await db.execute(stmt)
            await db.commit()

            row = (
                await db.execute(
                    select(YouTubeQuotaUsage).where(
                        YouTubeQuotaUsage.quota_date == target_date,
                        YouTubeQuotaUsage.quota_project_key == quota_project_key,
                    )
                )
            ).scalar_one()

            return QuotaReservationResult(
                reserved=res.rowcount == 1,
                quota_date=target_date,
                quota_project_key=quota_project_key,
                units_used=int(row.units_used),
                daily_budget=self.daily_budget,
            )


def _ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt
