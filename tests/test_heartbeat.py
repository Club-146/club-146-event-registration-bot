"""Heartbeat must tell the truth, and must never take the bot down."""

from __future__ import annotations

import pytest

from src import heartbeat


class _Me:
    username = "club146bot"


class _Bot:
    def __init__(self, fail: bool = False):
        self.fail = fail

    async def get_me(self):
        if self.fail:
            raise RuntimeError("telegram unreachable")
        return _Me()


class _DB:
    def __init__(self, fail: bool = False):
        self.fail = fail

    async def command(self, _cmd):
        if self.fail:
            raise RuntimeError("mongo down")
        return {"ok": 1}


@pytest.fixture
def hc_env(monkeypatch):
    monkeypatch.setenv("HEALTHCHECKS_BASE_URL", "https://hc.example/")
    monkeypatch.setenv("HEALTHCHECKS_PING_KEY", "pkey")
    monkeypatch.setenv("HEARTBEAT_SLUG", "club146-bot-test")


@pytest.fixture
def sent(monkeypatch):
    calls: list[str] = []

    async def _fake_send(url, timeout=10.0):
        calls.append(url)

    monkeypatch.setattr(heartbeat, "_send", _fake_send)
    return calls


def _patch_db(monkeypatch, db):
    import botspot

    monkeypatch.setattr(botspot, "get_database", lambda: db, raising=False)


@pytest.mark.asyncio
async def test_healthy_tick_pings_the_plain_url(hc_env, sent, monkeypatch):
    _patch_db(monkeypatch, _DB())
    await heartbeat.heartbeat_tick(_Bot())
    assert sent == ["https://hc.example/ping/pkey/club146-bot-test"]


@pytest.mark.asyncio
async def test_unreachable_telegram_reports_failure(hc_env, sent, monkeypatch):
    """The whole point: a live event loop must not look healthy on its own."""
    _patch_db(monkeypatch, _DB())
    await heartbeat.heartbeat_tick(_Bot(fail=True))
    assert sent == ["https://hc.example/ping/pkey/club146-bot-test/fail"]


@pytest.mark.asyncio
async def test_unreachable_database_reports_failure(hc_env, sent, monkeypatch):
    _patch_db(monkeypatch, _DB(fail=True))
    await heartbeat.heartbeat_tick(_Bot())
    assert sent == ["https://hc.example/ping/pkey/club146-bot-test/fail"]


@pytest.mark.asyncio
async def test_without_config_nothing_is_sent(monkeypatch, sent):
    monkeypatch.delenv("HEALTHCHECKS_BASE_URL", raising=False)
    monkeypatch.delenv("HEALTHCHECKS_PING_KEY", raising=False)
    _patch_db(monkeypatch, _DB())
    await heartbeat.heartbeat_tick(_Bot())
    assert sent == []


@pytest.mark.asyncio
async def test_ping_failure_does_not_propagate(hc_env, monkeypatch):
    """A dead healthchecks box must not raise into the bot's event loop."""
    _patch_db(monkeypatch, _DB())

    class _Boom:
        def __init__(self, *a, **kw): ...
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **kw):
            raise OSError("network is down")

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Boom)
    await heartbeat.heartbeat_tick(_Bot())  # must not raise


def test_trailing_slash_in_base_url_does_not_double_up(hc_env):
    base, key, slug = heartbeat._config()
    assert base == "https://hc.example"
    assert (key, slug) == ("pkey", "club146-bot-test")
