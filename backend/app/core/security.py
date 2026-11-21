import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError, PyJWK

from app.core.config import Settings
from app.core.errors import AppError

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class ClerkClaims:
    sub: str
    email: str | None
    display_name: str
    azp: str | None
    raw: dict[str, Any]


class JWKSCache:
    """Thread/async-safe JWKS cache with TTL and rate-limited kid-miss refresh."""

    def __init__(
        self,
        jwks_url: str,
        ttl_seconds: int = 3600,
        refresh_cooldown_seconds: int = 30,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._jwks_url = jwks_url
        self._ttl_seconds = ttl_seconds
        self._refresh_cooldown_seconds = refresh_cooldown_seconds
        self._http_client = http_client
        self._lock = asyncio.Lock()
        self._keys: dict[str, PyJWK] = {}
        self._fetched_at: float | None = None
        self._last_refresh_attempt_at: float | None = None

    async def get_key(self, kid: str) -> PyJWK | None:
        if not self._jwks_url:
            return None

        async with self._lock:
            now = time.monotonic()
            cache_valid = (
                self._fetched_at is not None
                and (now - self._fetched_at) < self._ttl_seconds
            )
            if cache_valid and kid in self._keys:
                return self._keys[kid]

            can_refresh = (
                self._last_refresh_attempt_at is None
                or (now - self._last_refresh_attempt_at) >= self._refresh_cooldown_seconds
            )
            if (not cache_valid or can_refresh) and (
                can_refresh or self._fetched_at is None
            ):
                await self._refresh_locked(now)

            return self._keys.get(kid)

    async def _refresh_locked(self, now: float) -> None:
        self._last_refresh_attempt_at = now
        try:
            if self._http_client is not None:
                response = await self._http_client.get(self._jwks_url, timeout=5.0)
            else:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    response = await client.get(self._jwks_url)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AppError(
                status_code=401,
                code="UNAUTHORIZED",
                message="Unable to verify authentication token.",
            ) from exc

        raw_keys = payload.get("keys")
        if not isinstance(raw_keys, list):
            raise AppError(
                status_code=401,
                code="UNAUTHORIZED",
                message="Invalid JWKS payload.",
            )

        parsed_keys: dict[str, PyJWK] = {}
        for key_data in raw_keys:
            if isinstance(key_data, dict) and isinstance(key_data.get("kid"), str):
                try:
                    parsed_keys[key_data["kid"]] = PyJWK.from_dict(key_data)
                except Exception:
                    continue

        self._keys = parsed_keys
        self._fetched_at = time.monotonic()


def _extract_display_name(payload: dict[str, Any], email: str | None) -> str:
    for key in ("name", "display_name", "username"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()[:120]

    first = payload.get("first_name")
    last = payload.get("last_name")
    parts = [p.strip() for p in (first, last) if isinstance(p, str) and p.strip()]
    if parts:
        return " ".join(parts)[:120]

    if email and "@" in email:
        return email.split("@", 1)[0][:120]

    return "User"


async def verify_clerk_token(
    token: str,
    jwks_cache: JWKSCache,
    settings: Settings,
) -> ClerkClaims:
    if not settings.CLERK_ISSUER:
        raise AppError(
            status_code=401,
            code="UNAUTHORIZED",
            message="Authentication issuer is not configured.",
        )

    try:
        header = jwt.get_unverified_header(token)
    except InvalidTokenError as exc:
        raise AppError(
            status_code=401,
            code="UNAUTHORIZED",
            message="Invalid authentication token header.",
        ) from exc

    if header.get("alg") != "RS256":
        raise AppError(
            status_code=401,
            code="UNAUTHORIZED",
            message="Unsupported token signing algorithm.",
        )

    if "jwk" in header or "jku" in header:
        raise AppError(
            status_code=401,
            code="UNAUTHORIZED",
            message="Embedded JWK headers are not permitted.",
        )

    kid = header.get("kid")
    if not isinstance(kid, str) or not kid.strip():
        raise AppError(
            status_code=401,
            code="UNAUTHORIZED",
            message="Missing token key identifier.",
        )

    signing_key = await jwks_cache.get_key(kid)
    if signing_key is None:
        raise AppError(
            status_code=401,
            code="UNAUTHORIZED",
            message="Unknown token signing key.",
        )

    try:
        payload = jwt.decode(
            token,
            key=signing_key.key,
            algorithms=["RS256"],
            issuer=settings.CLERK_ISSUER,
            leeway=settings.JWT_LEEWAY_SECONDS,
            options={
                "require": ["exp", "sub", "iss"],
                "verify_aud": False,
            },
        )
    except InvalidTokenError as exc:
        raise AppError(
            status_code=401,
            code="UNAUTHORIZED",
            message="Invalid or expired authentication token.",
        ) from exc

    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub.strip():
        raise AppError(
            status_code=401,
            code="UNAUTHORIZED",
            message="Token subject is missing.",
        )

    azp = payload.get("azp")
    if settings.CLERK_AUTHORIZED_PARTIES and (
        not isinstance(azp, str) or azp not in settings.CLERK_AUTHORIZED_PARTIES
    ):
        raise AppError(
            status_code=401,
            code="UNAUTHORIZED",
            message="Token authorized party is not allowed.",
        )

    sts = payload.get("sts")
    if sts is not None and sts != "active":
        raise AppError(
            status_code=401,
            code="UNAUTHORIZED",
            message="Session is not active.",
        )

    raw_email = payload.get("email")
    email = raw_email.strip() if isinstance(raw_email, str) and raw_email.strip() else None
    display_name = _extract_display_name(payload, email)

    return ClerkClaims(
        sub=sub.strip(),
        email=email,
        display_name=display_name,
        azp=azp if isinstance(azp, str) else None,
        raw=payload,
    )


async def get_current_claims(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> ClerkClaims:
    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or not credentials.credentials
    ):
        raise AppError(
            status_code=401,
            code="UNAUTHORIZED",
            message="Missing or invalid Authorization header.",
        )

    jwks_cache: JWKSCache = request.app.state.jwks_cache
    settings: Settings = request.app.state.settings
    return await verify_clerk_token(credentials.credentials, jwks_cache, settings)
