"""Optional website_event_uid link on event documents (migration 010).

The website SQL `events` table is the source of truth for events; a bot event
document points at one via its stable `Event.uid`. See
146.school/docs/events-people-data-integration.md, «Контракт по событиям».

The migration only declares the field. Binding a real value is a separate,
per-environment ops step — `dev/set_website_event_uid.py` — because staging
must never inherit production's website UID.
"""

import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.migrations import MIGRATION_REGISTRY, add_website_event_uid


REPO = Path(__file__).resolve().parents[1]


def _load_ops_script():
    spec = importlib.util.spec_from_file_location(
        "set_website_event_uid", REPO / "dev" / "set_website_event_uid.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestMigrationRegistration:
    def test_migration_is_registered_once_and_last(self):
        names = [m.name for m in MIGRATION_REGISTRY]
        assert names.count("010_add_website_event_uid") == 1
        assert names[-1] == "010_add_website_event_uid"

    def test_migration_names_stay_unique(self):
        names = [m.name for m in MIGRATION_REGISTRY]
        assert len(names) == len(set(names))


class TestAddWebsiteEventUid:
    def _app(self, modified=3, matched=3):
        app = MagicMock()
        app.events_col.update_many = AsyncMock(
            return_value=MagicMock(modified_count=modified, matched_count=matched)
        )
        return app

    @pytest.mark.asyncio
    async def test_declares_the_field_as_none_where_absent(self):
        app = self._app()
        await add_website_event_uid(app)
        app.events_col.update_many.assert_awaited_once_with(
            {"website_event_uid": {"$exists": False}},
            {"$set": {"website_event_uid": None}},
        )

    @pytest.mark.asyncio
    async def test_never_overwrites_an_existing_link(self):
        """The filter, not a later guard, is what makes a re-run safe."""
        app = self._app()
        await add_website_event_uid(app)
        query, _update = app.events_col.update_many.await_args.args
        assert query == {"website_event_uid": {"$exists": False}}

    @pytest.mark.asyncio
    async def test_asserts_no_particular_mapping(self):
        app = self._app()
        await add_website_event_uid(app)
        _query, update = app.events_col.update_many.await_args.args
        assert update == {"$set": {"website_event_uid": None}}

    @pytest.mark.asyncio
    async def test_is_a_no_op_on_a_second_run(self):
        app = self._app(modified=0, matched=0)
        await add_website_event_uid(app)  # must not raise
        app.events_col.update_many.assert_awaited_once()


class TestOpsScriptArguments:
    def setup_method(self):
        self.module = _load_ops_script()

    def test_requires_exactly_one_of_uid_or_unset(self):
        with pytest.raises(SystemExit):
            self.module.parse_args(["--bot-event-id", "abc"])
        with pytest.raises(SystemExit):
            self.module.parse_args(
                [
                    "--bot-event-id",
                    "abc",
                    "--unset",
                    "--website-event-uid",
                    "event-1@146.school",
                ]
            )

    def test_accepts_a_set_invocation(self):
        args = self.module.parse_args(
            [
                "--bot-event-id",
                "6a599a17a37724d81b7eadc3",
                "--website-event-uid",
                "event-1@146.school",
            ]
        )
        assert args.website_event_uid == "event-1@146.school"
        assert args.unset is False

    def test_writes_nothing_without_apply(self):
        """Dry run is the default: an ops script that touches prod on a typo
        is worse than one that needs a second invocation."""
        args = self.module.parse_args(
            [
                "--bot-event-id",
                "6a599a17a37724d81b7eadc3",
                "--website-event-uid",
                "event-1@146.school",
            ]
        )
        assert args.apply is False
