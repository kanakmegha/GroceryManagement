"""Controlled, read-only, household-authorized backend tools for the AI Chef."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.cooking.models import CookingPlan
from app.modules.inventory.models import CanonicalItem, InventoryLot


class _GetPantryItemsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str | None = Field(default=None, max_length=64)


class _GetExpiringItemsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    days_ahead: int = Field(default=3, ge=1, le=30)


class _EmptyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _GetRecentCookingHistoryArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=8, ge=1, le=20)


CHEF_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_pantry_items",
            "description": (
                "List active, non-expired pantry items currently in this household's inventory."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": (
                            "Optional food category filter (e.g. dairy, vegetable, pulse)."
                        ),
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_expiring_items",
            "description": (
                "List household pantry items expiring within the next N days, ordered "
                "soonest first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "days_ahead": {
                        "type": "integer",
                        "description": "Number of days ahead to check (1-30, default 3).",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_available_equipment",
            "description": "Get kitchen equipment constraints specified for this cooking session.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_cooking_history",
            "description": (
                "Get recently planned or completed recipes for this household to avoid repetition."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": (
                            "Maximum number of recent recipes to return (1-20, default 8)."
                        ),
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_household_food_preferences",
            "description": (
                "Get dietary constraints, servings, meal type, and prep-time preferences "
                "for this request."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


class ReadOnlyChefToolset:
    """Executes allowlisted read-only queries strictly scoped to a single household."""

    def __init__(
        self,
        db: AsyncSession,
        household_id: UUID,
        *,
        equipment: list[str] | None = None,
        dietary: str | None = None,
        servings: int = 2,
        meal_type: str | None = None,
        max_prep_minutes: int | None = None,
    ) -> None:
        self._db = db
        self._household_id = household_id
        self._equipment = list(equipment or [])
        self._dietary = dietary
        self._servings = servings
        self._meal_type = meal_type
        self._max_prep_minutes = max_prep_minutes

    async def execute_tool(
        self, name: str, raw_arguments: str | dict[str, Any] | None
    ) -> dict[str, Any]:
        """Validate arguments and dispatch to an allowlisted read-only tool."""
        if isinstance(raw_arguments, str):
            try:
                parsed = json.loads(raw_arguments) if raw_arguments.strip() else {}
            except json.JSONDecodeError:
                return {"error": "invalid_json_arguments"}
        elif isinstance(raw_arguments, dict):
            parsed = raw_arguments
        else:
            parsed = {}

        if not isinstance(parsed, dict):
            return {"error": "arguments_must_be_object"}

        try:
            if name == "get_pantry_items":
                args = _GetPantryItemsArgs.model_validate(parsed)
                return await self._get_pantry_items(category=args.category)
            if name == "get_expiring_items":
                args = _GetExpiringItemsArgs.model_validate(parsed)
                return await self._get_expiring_items(days_ahead=args.days_ahead)
            if name == "get_available_equipment":
                _EmptyArgs.model_validate(parsed)
                return {"equipment": self._equipment}
            if name == "get_recent_cooking_history":
                args = _GetRecentCookingHistoryArgs.model_validate(parsed)
                return await self._get_recent_cooking_history(limit=args.limit)
            if name == "get_household_food_preferences":
                _EmptyArgs.model_validate(parsed)
                from app.modules.recipes.preferences import compute_household_preference_profile

                learned = await compute_household_preference_profile(self._db, self._household_id)
                return {
                    "dietary": self._dietary,
                    "servings": self._servings,
                    "meal_type": self._meal_type,
                    "max_prep_minutes": self._max_prep_minutes,
                    "learned_preferences": learned,
                }
        except ValidationError:
            return {"error": "invalid_tool_arguments"}

        return {"error": "unknown_or_disallowed_tool"}

    async def _get_pantry_items(self, *, category: str | None = None) -> dict[str, Any]:
        today = datetime.now(UTC).date()
        expiring_cutoff = today + timedelta(days=3)
        stmt = (
            select(InventoryLot, CanonicalItem)
            .join(CanonicalItem, CanonicalItem.id == InventoryLot.canonical_item_id)
            .where(
                InventoryLot.household_id == self._household_id,
                InventoryLot.lot_status == "active",
                InventoryLot.remaining_qty > Decimal("0"),
                or_(InventoryLot.expires_on.is_(None), InventoryLot.expires_on >= today),
            )
            .order_by(InventoryLot.expires_on.asc().nulls_last(), InventoryLot.purchased_at.asc())
        )
        if category:
            stmt = stmt.where(CanonicalItem.category.ilike(category.strip()))
        rows = (await self._db.execute(stmt)).all()

        aggregated: dict[UUID, dict[str, Any]] = {}
        for lot, item in rows:
            expiring_soon = lot.expires_on is not None and lot.expires_on <= expiring_cutoff
            entry = aggregated.get(item.id)
            if entry is None:
                aggregated[item.id] = {
                    "canonical_item_id": str(item.id),
                    "name": item.name,
                    "category": item.category,
                    "qty": float(lot.remaining_qty),
                    "base_unit": lot.base_unit,
                    "expiring_soon": expiring_soon,
                    "earliest_expires_on": lot.expires_on.isoformat() if lot.expires_on else None,
                }
            else:
                entry["qty"] += float(lot.remaining_qty)
                entry["expiring_soon"] = entry["expiring_soon"] or expiring_soon
        return {"items": list(aggregated.values())}

    async def _get_expiring_items(self, *, days_ahead: int = 3) -> dict[str, Any]:
        today = datetime.now(UTC).date()
        cutoff = today + timedelta(days=days_ahead)
        stmt = (
            select(InventoryLot, CanonicalItem)
            .join(CanonicalItem, CanonicalItem.id == InventoryLot.canonical_item_id)
            .where(
                InventoryLot.household_id == self._household_id,
                InventoryLot.lot_status == "active",
                InventoryLot.remaining_qty > Decimal("0"),
                InventoryLot.expires_on.is_not(None),
                InventoryLot.expires_on >= today,
                InventoryLot.expires_on <= cutoff,
            )
            .order_by(InventoryLot.expires_on.asc())
        )
        rows = (await self._db.execute(stmt)).all()
        return {
            "days_ahead": days_ahead,
            "items": [
                {
                    "canonical_item_id": str(item.id),
                    "name": item.name,
                    "qty": float(lot.remaining_qty),
                    "base_unit": lot.base_unit,
                    "expires_on": lot.expires_on.isoformat() if lot.expires_on else None,
                }
                for lot, item in rows
            ],
        }

    async def _get_recent_cooking_history(self, *, limit: int = 8) -> dict[str, Any]:
        stmt = (
            select(CookingPlan.recipe_title, CookingPlan.status, CookingPlan.started_at)
            .where(CookingPlan.household_id == self._household_id)
            .order_by(CookingPlan.started_at.desc())
            .limit(limit)
        )
        rows = (await self._db.execute(stmt)).all()
        return {
            "recent_recipes": [
                {
                    "title": str(title),
                    "status": str(status),
                    "started_at": started_at.isoformat() if started_at else None,
                }
                for title, status, started_at in rows
                if title
            ]
        }
