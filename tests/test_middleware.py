"""Unit tests for GatewayCredentialsMiddleware and the OAuth2 token exchange
(PRD-21248 TC-VAN-010, TC-VAN-011, TC-VAN-013, TC-VAN-014).

Pure ASGI-level tests for the middleware (no HTTP server, no network);
`httpx.MockTransport` for the token-endpoint tests, so the real retry /
error-mapping code in auth.py runs against a scripted response sequence
instead of a real network call.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

import vanta_mcp.auth as auth
from vanta_mcp.config import Settings
from vanta_mcp.errors import ToolError
from vanta_mcp.middleware import GatewayCredentialsMiddleware, get_vanta_credentials


def _scope(path: str = "/mcp", headers: dict[str, str] | None = None) -> dict:
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return {"type": "http", "path": path, "method": "POST", "headers": raw_headers}


async def _empty_receive() -> dict:
    return {"type": "http.request", "body": b"", "more_body": False}


class _RecordingSend:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def __call__(self, message: dict) -> None:
        self.messages.append(message)

    @property
    def status(self) -> int | None:
        start = next((m for m in self.messages if m["type"] == "http.response.start"), None)
        return start["status"] if start else None

    @property
    def body(self) -> bytes:
        return b"".join(
            m.get("body", b"") for m in self.messages if m["type"] == "http.response.body"
        )


# --- TC-VAN-010 (AC10) -----------------------------------------------------


@pytest.mark.asyncio
async def test_missing_credential_headers_returns_401_with_header_names():
    inner_calls: list[str] = []

    async def inner_app(scope, receive, send):
        inner_calls.append("called")

    mw = GatewayCredentialsMiddleware(inner_app, Settings())
    send = _RecordingSend()

    await mw(_scope(headers={}), _empty_receive, send)

    assert send.status == 401
    body = json.loads(send.body)
    assert body["required_headers"] == ["X-Vanta-Client-Id", "X-Vanta-Client-Secret"]
    # The inner app -- and therefore any call to Vanta -- must never run.
    assert inner_calls == []


@pytest.mark.asyncio
async def test_one_header_missing_still_returns_401():
    mw = GatewayCredentialsMiddleware(lambda scope, receive, send: None, Settings())
    send = _RecordingSend()

    await mw(_scope(headers={"X-Vanta-Client-Id": "id-only"}), _empty_receive, send)

    assert send.status == 401
    body = json.loads(send.body)
    assert set(body["required_headers"]) == {"X-Vanta-Client-Id", "X-Vanta-Client-Secret"}


@pytest.mark.asyncio
async def test_non_mcp_path_passes_through_without_headers():
    """/health must never require the credential headers (SOP §1.1)."""
    inner_calls: list[str] = []

    async def inner_app(scope, receive, send):
        inner_calls.append("called")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    mw = GatewayCredentialsMiddleware(inner_app, Settings())
    send = _RecordingSend()

    await mw(_scope(path="/health", headers={}), _empty_receive, send)

    assert inner_calls == ["called"]
    assert send.status == 200


# --- TC-VAN-014 (AC14) -----------------------------------------------------


@pytest.mark.asyncio
async def test_interleaved_requests_do_not_leak_context_between_tenants():
    """Two concurrent requests with two different credential pairs must
    never observe each other's contextvar value (AC14's isolation guarantee
    -- enforced entirely by asyncio Task-local contextvars)."""
    observed: dict[str, tuple[str, str] | None] = {}

    async def inner_app(scope, receive, send):
        tenant = scope["_test_tenant"]
        # Yield control mid-request so the two tasks genuinely interleave
        # instead of running to completion back-to-back.
        await asyncio.sleep(0)
        observed[tenant] = get_vanta_credentials()
        await asyncio.sleep(0)
        # Re-read after a second yield to catch any cross-task leakage that
        # only shows up after the other task has also run.
        assert get_vanta_credentials() == observed[tenant]
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    mw = GatewayCredentialsMiddleware(inner_app, Settings())

    async def run_request(tenant: str, client_id: str, client_secret: str):
        scope = _scope(
            headers={"X-Vanta-Client-Id": client_id, "X-Vanta-Client-Secret": client_secret}
        )
        scope["_test_tenant"] = tenant
        await mw(scope, _empty_receive, _RecordingSend())

    await asyncio.gather(
        run_request("tenant-a", "id-a", "secret-a"),
        run_request("tenant-b", "id-b", "secret-b"),
    )

    assert observed == {
        "tenant-a": ("id-a", "secret-a"),
        "tenant-b": ("id-b", "secret-b"),
    }
    assert get_vanta_credentials() is None  # both reset, nothing leaks to the next request


# --- TC-VAN-011 (AC11, mocked upstream) ------------------------------------


@pytest.mark.asyncio
async def test_token_endpoint_rejects_credentials_maps_to_unauthorized(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/oauth/token"
        return httpx.Response(401, json={"error": "invalid_client"})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(auth, "_token_http_client", mock_client)

    with pytest.raises(ToolError) as exc_info:
        await auth.fetch_access_token("bad-id", "bad-secret", Settings())

    assert exc_info.value.code == "unauthorized"


# --- TC-VAN-013 (AC13, mocked upstream) ------------------------------------


@pytest.mark.asyncio
async def test_token_endpoint_429_exhausts_retry_then_rate_limited(monkeypatch):
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": "rate_limited"})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(auth, "_token_http_client", mock_client)
    # Speed up the test: real retry delays aren't the thing under test here.
    # Capture the *real* sleep first -- patching asyncio.sleep with something
    # that itself calls asyncio.sleep would recurse into the patched version.
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda *_args, **_kwargs: real_sleep(0))

    with pytest.raises(ToolError) as exc_info:
        await auth.fetch_access_token("id", "secret", Settings())

    assert exc_info.value.code == "rate_limited"
    # 1 initial attempt + 2 bounded retries = 3 calls total (SOP §5 bounded retry).
    assert calls["count"] == 3
