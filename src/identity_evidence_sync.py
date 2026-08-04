"""Periodic, idempotent registry-bot identity evidence sync to 146.school."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from loguru import logger
from pydantic import BaseModel, Field

from src.website_event_bridge import WebsiteBridgeError, WebsiteEventBridgeClient


class IdentityEvidenceItem(BaseModel):
    source_system: str = "club146_registry_bot"
    source_record_id: str = Field(min_length=8, max_length=120)
    event_external_id: str = Field(default="", max_length=120)
    event_name: str = Field(default="", max_length=255)
    full_name: str = Field(min_length=1, max_length=255)
    graduation_year: int | None = Field(default=None, ge=1900, le=2100)
    class_letter: str = Field(default="", max_length=8)
    participant_type: str = Field(default="", max_length=32)
    telegram_id: int | None = Field(default=None, ge=1)
    telegram_username: str = Field(default="", max_length=64)
    city: str = Field(default="", max_length=120)
    registration_status: str
    payment_status: str = Field(default="", max_length=24)
    source_created_at: datetime | None = None


def identity_evidence_sync_requested(settings: Any) -> bool:
    return getattr(settings, "identity_evidence_sync_enabled", False) is True


def _clean(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _source_created_at(value: Any) -> datetime | None:
    if isinstance(value, ObjectId):
        return value.generation_time.astimezone(timezone.utc)
    return None


def _item(registration: dict, event_names: dict[str, str], *, active: bool):
    record_id = registration.get("_id")
    full_name = _clean(registration.get("full_name"), 255)
    if not record_id or not full_name:
        return None
    year = registration.get("graduation_year")
    if isinstance(year, bool) or not isinstance(year, int) or not 1900 <= year <= 2100:
        year = None
    telegram_id = registration.get("user_id")
    if (
        isinstance(telegram_id, bool)
        or not isinstance(telegram_id, int)
        or telegram_id < 1
    ):
        telegram_id = None
    event_id = _clean(registration.get("event_id"), 120)
    return IdentityEvidenceItem(
        source_record_id=_clean(record_id, 120),
        event_external_id=event_id,
        event_name=_clean(event_names.get(event_id), 255),
        full_name=full_name,
        graduation_year=year,
        class_letter=_clean(registration.get("class_letter"), 8),
        participant_type=_clean(registration.get("graduate_type"), 32),
        telegram_id=telegram_id,
        telegram_username=_clean(registration.get("username"), 64).lstrip("@"),
        city=_clean(registration.get("target_city"), 120),
        registration_status="active" if active else "deleted",
        payment_status=_clean(registration.get("payment_status"), 24),
        source_created_at=_source_created_at(record_id),
    )


async def build_identity_evidence(app: Any) -> list[IdentityEvidenceItem]:
    events = await app.events_col.find({}).to_list(length=None)
    event_names = {
        str(event.get("_id")): _clean(event.get("name"), 255)
        for event in events
        if event.get("_id")
    }
    active = await app.collection.find({}).to_list(length=None)
    deleted = await app.deleted_users.find({}).to_list(length=None)
    items = [
        item
        for registration, is_active in (
            *((registration, True) for registration in active),
            *((registration, False) for registration in deleted),
        )
        if (item := _item(registration, event_names, active=is_active)) is not None
    ]
    return items


async def sync_identity_evidence_once(
    app: Any,
    *,
    client: WebsiteEventBridgeClient | None = None,
) -> int:
    if not identity_evidence_sync_requested(app.settings):
        return 0
    items = await build_identity_evidence(app)
    api = client or WebsiteEventBridgeClient(app.settings)
    synced = 0
    for start in range(0, len(items), 500):
        batch = items[start : start + 500]
        response = await api.sync_identity_evidence(
            [item.model_dump(mode="json") for item in batch]
        )
        if response.get("total") != len(batch):
            raise WebsiteBridgeError("identity_sync_count_mismatch")
        synced += len(batch)
    logger.info("Synced {} registry identity evidence rows to website", synced)
    return synced


async def run_identity_evidence_sync_loop(app: Any) -> None:
    interval = float(app.settings.identity_evidence_sync_interval_seconds)
    while True:
        try:
            await sync_identity_evidence_once(app)
        except WebsiteBridgeError as exc:
            logger.error("Identity evidence sync failed: {}", exc.code)
        except Exception:
            logger.exception("Identity evidence sync failed unexpectedly")
        await asyncio.sleep(interval)
