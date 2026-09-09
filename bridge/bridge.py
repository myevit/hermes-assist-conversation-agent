#!/usr/bin/env python3
"""Narrow local HTTP bridge from Home Assistant Assist to the TARS Home API.

The bridge deliberately does not import or execute Hermes.  It only forwards an
already-authenticated Assist request to the running Hermes Gateway API's
``tars-home`` profile, which keeps the household agent's tool restrictions and
persistent conversation state in one authoritative process.

Endpoints:
* ``GET /health``
* ``POST /api/chat`` with a bridge bearer key

Required runtime secrets are injected from 1Password at service start; neither
keys nor their values belong in this repository or Home Assistant configuration.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib import error, parse, request

HOST = os.environ.get("HERMES_ASSIST_HOST", "127.0.0.1")
PORT = int(os.environ.get("HERMES_ASSIST_PORT", "8765"))
BRIDGE_KEY_FILE = Path(os.environ.get("HERMES_ASSIST_BRIDGE_KEY_FILE", "./hermes-assist-bridge.key"))
HERMES_API_URL = os.environ.get("HERMES_API_URL", "http://127.0.0.1:8642")
HERMES_API_KEY = os.environ.get("HERMES_API_KEY", "").strip()
DEFAULT_TIMEOUT_SECONDS = int(os.environ.get("HERMES_ASSIST_TIMEOUT", "90"))
TEST_MODE = os.environ.get("HERMES_ASSIST_TEST_MODE", "0").lower() in {"1", "true", "yes"}
MAX_REQUEST_BYTES = 1_000_000
MAX_RESPONSE_BYTES = 1_000_000
MAX_TEXT_CHARS = 6_000
MAX_CONVERSATION_ID_LENGTH = 96
PROFILE_NAME = "tars-home"


class HermesBridgeError(RuntimeError):
    """An upstream failure that is safe to show as a generic local error."""

    def __init__(self, message: str, *, status: int = 502):
        super().__init__(message)
        self.status = status

    def safe_message(self) -> str:
        if self.status == 504:
            return "TARS took too long to answer."
        if self.status == 401:
            return "TARS bridge authentication is not configured."
        return "TARS is temporarily unavailable."


@dataclass(frozen=True)
class HermesReply:
    text: str
    session_id: str | None = None


def _is_loopback_url(value: str) -> bool:
    parsed = parse.urlparse(value)
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "::1", "localhost"}


def normalise_conversation_id(value: str) -> str:
    """Return a stable, bounded identifier accepted by the Gateway's conversation store."""
    raw = str(value).strip()
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-._")
    if not cleaned:
        raise ValueError("conversation_id is required")
    if len(cleaned) <= MAX_CONVERSATION_ID_LENGTH:
        return cleaned
    digest = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:16]
    prefix_len = MAX_CONVERSATION_ID_LENGTH - len(digest) - 1
    return f"{cleaned[:prefix_len]}-{digest}"


def extract_output_text(payload: dict[str, Any]) -> str:
    """Extract the final speech text from Hermes' OpenAI-compatible Responses shape."""
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    output = payload.get("output")
    if isinstance(output, list):
        parts: list[str] = []
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if isinstance(part, dict) and part.get("type") == "output_text":
                    text = part.get("text")
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())
        if parts:
            return "\n".join(parts)
    raise HermesBridgeError("Gateway response contained no usable text")


class HermesApiClient:
    """Loopback-only client for the dedicated TARS Home Gateway profile."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        conversation_prefix: str = "ha",
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not _is_loopback_url(base_url):
            raise ValueError("Hermes API URL must be an http loopback address")
        if not api_key:
            raise ValueError("Hermes API key is not configured")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.conversation_prefix = normalise_conversation_id(conversation_prefix)
        self.timeout = timeout

    def ask(self, text: str, conversation_id: str) -> HermesReply:
        conversation = f"{self.conversation_prefix}-{normalise_conversation_id(conversation_id)}"
        payload = json.dumps({
            "input": text,
            "conversation": conversation,
            "store": True,
            "truncation": "auto",
        }).encode("utf-8")
        req = request.Request(
            f"{self.base_url}/p/{PROFILE_NAME}/v1/responses",
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "X-Hermes-Session-Id": conversation,
            },
        )
        try:
            with request.urlopen(req, timeout=self.timeout) as response:  # nosec B310: URL is validated loopback
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                status = response.status
                session_id = response.headers.get("X-Hermes-Session-Id")
            if len(raw) > MAX_RESPONSE_BYTES:
                raise HermesBridgeError("Gateway response exceeds bridge limit")
        except error.HTTPError as exc:
            raise HermesBridgeError("Gateway rejected bridge request", status=exc.code) from exc
        except TimeoutError as exc:
            raise HermesBridgeError("Gateway timed out", status=504) from exc
        except error.URLError as exc:
            raise HermesBridgeError("Gateway is unreachable") from exc
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HermesBridgeError("Gateway returned invalid JSON", status=status) from exc
        if not 200 <= status < 300:
            raise HermesBridgeError("Gateway request failed", status=status)
        return HermesReply(extract_output_text(data), session_id)

    @staticmethod
    def safe_error(exc: Exception) -> str:
        """Never emit request URLs, payloads, or bearer material to HA or logs."""
        return exc.safe_message() if isinstance(exc, HermesBridgeError) else "TARS is temporarily unavailable."


def load_bridge_key() -> str:
    """Read the inbound bridge key without ever printing it."""
    try:
        return BRIDGE_KEY_FILE.expanduser().read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return os.environ.get("HERMES_ASSIST_BRIDGE_KEY", "").strip()


def bridge_auth_required() -> bool:
    """Channel authentication is intentionally deferred only under explicit test mode."""
    return not TEST_MODE


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.end_headers()
    handler.wfile.write(body)


class Handler(BaseHTTPRequestHandler):
    server_version = "TARSHomeAssistBridge/2.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        # Access-only logging; never record request headers, bodies, query strings, or upstream errors.
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), fmt % args))

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        """Avoid BaseHTTPRequestHandler's default request-line logging (includes query data)."""
        safe_path = self.path.split("?", 1)[0]
        self.log_message('"%s %s" %s %s', self.command, safe_path, str(code), str(size))

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            json_response(self, 200, {
                "ok": bool(HERMES_API_KEY) and (TEST_MODE or bool(load_bridge_key())),
                "service": "tars-home-assist-bridge",
                "profile": PROFILE_NAME,
                "test_mode": TEST_MODE,
                "time": time.time(),
            })
            return
        json_response(self, 404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/chat":
            json_response(self, 404, {"error": "not_found"})
            return
        if bridge_auth_required():
            expected = load_bridge_key()
            supplied = self.headers.get("Authorization", "")
            if not expected or not hmac.compare_digest(supplied, f"Bearer {expected}"):
                json_response(self, 401, {"error": "unauthorized"})
                return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > MAX_REQUEST_BYTES:
                raise ValueError("invalid request length")
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            json_response(self, 400, {"error": "bad_json"})
            return
        text = str(data.get("text", "")).strip()
        if not text:
            json_response(self, 400, {"error": "missing_text"})
            return
        text = text[:MAX_TEXT_CHARS]
        try:
            conversation_id = normalise_conversation_id(str(data.get("conversation_id") or ""))
        except ValueError:
            json_response(self, 400, {"error": "missing_conversation_id"})
            return
        try:
            reply = HermesApiClient(HERMES_API_URL, HERMES_API_KEY).ask(text, conversation_id)
        except (ValueError, HermesBridgeError) as exc:
            safe = HermesApiClient.safe_error(exc)
            status = exc.status if isinstance(exc, HermesBridgeError) and exc.status == 504 else 502
            json_response(self, status, {"error": "tars_unavailable", "reply": safe})
            return
        json_response(self, 200, {"reply": reply.text, "conversation_id": conversation_id})


def main() -> None:
    if HOST not in {"127.0.0.1", "::1", "localhost"} and not TEST_MODE:
        raise SystemExit("HERMES_ASSIST_HOST must be loopback-only outside explicit test mode")
    if not HERMES_API_KEY:
        raise SystemExit("HERMES_API_KEY must be injected from 1Password")
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"TARS Home Assist Bridge listening on http://{HOST}:{PORT}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
