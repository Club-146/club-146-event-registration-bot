from types import SimpleNamespace

import pytest
from bson import ObjectId

from src.identity_evidence_sync import (
    build_identity_evidence,
    sync_identity_evidence_once,
)
from src.website_event_bridge import WebsiteBridgeError


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    async def to_list(self, length=None):
        return self.rows


class Collection:
    def __init__(self, rows):
        self.rows = rows

    def find(self, query):
        assert query == {}
        return Cursor(self.rows)


class Client:
    def __init__(self):
        self.batches = []

    async def sync_identity_evidence(self, items):
        self.batches.append(items)
        return {"created": len(items), "updated": 0, "total": len(items)}


def app(*, enabled=True):
    event_id = ObjectId("6a599a17a37724d81b7eadc3")
    active_id = ObjectId("67ca89364841b89f8164ae1d")
    deleted_id = ObjectId("67e2108711c707f46cb8e161")
    return SimpleNamespace(
        settings=SimpleNamespace(identity_evidence_sync_enabled=enabled),
        events_col=Collection([{"_id": event_id, "name": "Летняя встреча"}]),
        collection=Collection([{
            "_id": active_id,
            "event_id": str(event_id),
            "full_name": "  Пушкарева   Диана Владимировна ",
            "graduation_year": 2009,
            "class_letter": "А",
            "graduate_type": "GRADUATE",
            "user_id": 123456,
            "username": "@diana_tg",
            "target_city": "Пермь",
            "payment_status": "confirmed",
        }]),
        deleted_users=Collection([{
            "_id": deleted_id,
            "full_name": "Хисамутдинов Айрат Альбертович",
            "graduation_year": 2012,
            "class_letter": "Б",
            "user_id": 654321,
            "username": "RaccoonTunTun",
        }]),
    )


@pytest.mark.asyncio
async def test_builds_active_and_deleted_evidence_without_linking_people():
    items = await build_identity_evidence(app())

    assert len(items) == 2
    active, deleted = items
    assert active.full_name == "Пушкарева Диана Владимировна"
    assert active.event_name == "Летняя встреча"
    assert active.telegram_username == "diana_tg"
    assert active.registration_status == "active"
    assert active.source_created_at is not None
    assert deleted.registration_status == "deleted"


@pytest.mark.asyncio
async def test_sync_is_idempotent_batch_contract_and_disabled_by_default():
    client = Client()
    assert await sync_identity_evidence_once(app(enabled=False), client=client) == 0
    assert client.batches == []

    assert await sync_identity_evidence_once(app(), client=client) == 2
    assert len(client.batches) == 1
    assert client.batches[0][0]["source_system"] == "club146_registry_bot"
    assert "person_id" not in client.batches[0][0]


@pytest.mark.asyncio
async def test_count_mismatch_fails_closed():
    class BadClient(Client):
        async def sync_identity_evidence(self, items):
            return {"total": 0}

    with pytest.raises(WebsiteBridgeError, match="identity_sync_count_mismatch"):
        await sync_identity_evidence_once(app(), client=BadClient())

