"""Standalone mock of 146.school event-payment internal API.

Run:
  uv run python -m dev.mock_website_event_payments.server
  uv run python -m dev.mock_website_event_payments.server --port 8766

Env (optional):
  MOCK_EVENT_PAYMENTS_TOKEN
  MOCK_EVENT_PAYMENTS_WEBSITE_EVENT_ID
  MOCK_EVENT_PAYMENTS_WEBSITE_EVENT_UID
  MOCK_EVENT_PAYMENTS_BOT_EVENT_ID

Bot settings for a local loopback smoke (bridge still off by default elsewhere):
  EVENT_PAYMENTS_BRIDGE_ENABLED=true
  EVENT_PAYMENTS_WEBSITE_API_BASE_URL=http://127.0.0.1:8765
  EVENT_PAYMENTS_WEBSITE_API_TOKEN=<same token>
  EVENT_PAYMENTS_WEBSITE_EVENT_ID=1
  EVENT_PAYMENTS_WEBSITE_EVENT_UID=aug1-2026-perm
  EVENT_PAYMENTS_BOT_EVENT_ID=6a599a17a37724d81b7eadc3

Loopback HTTP is accepted only for 127.0.0.1/localhost — production stays HTTPS.
Do not point a live/dev Coolify bot at this process.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlparse

from dev.mock_website_event_payments.service import (
    MockConfig,
    MockWebsiteError,
    MockWebsiteEventPaymentService,
)


CREATE_PATH = "/api/internal/event-payment-intents"
ACTION_RE = re.compile(
    r"^/api/internal/event-payment-intents/(?P<source>[^/]+)/(?P<action>confirm|revoke)$"
)


def _build_service_from_env() -> MockWebsiteEventPaymentService:
    return MockWebsiteEventPaymentService(
        MockConfig(
            website_event_id=int(
                os.environ.get("MOCK_EVENT_PAYMENTS_WEBSITE_EVENT_ID", "1")
            ),
            website_event_uid=os.environ.get(
                "MOCK_EVENT_PAYMENTS_WEBSITE_EVENT_UID", "aug1-2026-perm"
            ),
            bot_event_id=os.environ.get(
                "MOCK_EVENT_PAYMENTS_BOT_EVENT_ID", "6a599a17a37724d81b7eadc3"
            ),
            api_token=os.environ.get(
                "MOCK_EVENT_PAYMENTS_TOKEN", "test-dedicated-token"
            ),
        )
    )


def make_handler(service: MockWebsiteEventPaymentService):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            # Keep stdout quiet unless something interesting happens.
            if args and str(args[0]).startswith(("4", "5")):
                super().log_message(format, *args)

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                value = json.loads(raw.decode() or "{}")
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise MockWebsiteError(422, "invalid json") from exc
            if not isinstance(value, dict):
                raise MockWebsiteError(422, "json object required")
            return value

        def _write_json(self, status: int, body: dict[str, Any]) -> None:
            payload = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802
            if urlparse(self.path).path == "/healthz":
                self._write_json(200, {"ok": True, "intents": len(service.intents)})
                return
            self._write_json(404, {"detail": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            try:
                service.authorize(self.headers.get("Authorization"))
                body = self._read_json()
                if path == CREATE_PATH:
                    response = service.create_or_replay(body)
                    self._write_json(200, response)
                    return
                match = ACTION_RE.match(path)
                if not match:
                    self._write_json(404, {"detail": "not found"})
                    return
                source = unquote(match.group("source"))
                action = match.group("action")
                if action == "confirm":
                    response = service.confirm(
                        source,
                        paid_amount=body.get("paid_amount"),
                        evidence_reference=str(body.get("evidence_reference") or ""),
                    )
                else:
                    response = service.revoke(
                        source,
                        transition_kind=str(body.get("transition_kind") or ""),
                        reason=str(body.get("reason") or ""),
                        audit_reference=str(body.get("audit_reference") or ""),
                        occurred_at=str(body.get("occurred_at") or ""),
                    )
                self._write_json(200, response)
            except MockWebsiteError as exc:
                self._write_json(exc.status_code, {"detail": exc.detail})

    return Handler


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    service = _build_service_from_env()
    httpd = ThreadingHTTPServer((args.host, args.port), make_handler(service))
    print(
        f"mock website event-payments on http://{args.host}:{args.port}\n"
        f"  token={service.config.api_token}\n"
        f"  mapping={service.config.website_event_id}/"
        f"{service.config.website_event_uid}/{service.config.bot_event_id}\n"
        f"  health: GET /healthz"
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
