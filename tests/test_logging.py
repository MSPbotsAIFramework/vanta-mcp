"""Structured-logging regression tests (PRD-21248 review round 1 must-fix).

The review found that every upstream-error path (`client.py`'s
`_raise_for_status`, `auth.py`'s `fetch_access_token`, and `middleware.py`'s
missing-header 401) raised/returned a structured error without writing a
log line first. These tests prove: (1) a log line is actually emitted on
each of those paths, and (2) it never contains the credential value or
bearer token involved in the call -- a test that asserts shape only (e.g.
"logging.warning was called") would not have caught a leak; asserting the
secret value is *absent* from the rendered log text does.

Not part of the original `.ac.json` (the review finding predates it); added
here as dev-sourced regression coverage ("source": "dev"), same as the
precedent in PRD-21228's review-rework.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from vanta_mcp import auth, client
from vanta_mcp.config import Settings
from vanta_mcp.errors import ToolError
from vanta_mcp.middleware import GatewayCredentialsMiddleware


class _RecordingSend:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def __call__(self, message: dict) -> None:
        self.messages.append(message)

    @property
    def status(self) -> int | None:
        start = next((m for m in self.messages if m["type"] == "http.response.start"), None)
        return start["status"] if start else None


def _scope(path: str = "/mcp", headers: dict[str, str] | None = None) -> dict:
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return {"type": "http", "path": path, "method": "POST", "headers": raw_headers}


async def _empty_receive() -> dict:
    return {"type": "http.request", "body": b"", "more_body": False}


# --- client.py: VantaClient._raise_for_status -------------------------------


@pytest.mark.asyncio
async def test_upstream_error_is_logged_without_leaking_the_bearer_token(monkeypatch, caplog):
    secret_token = "sekret-bearer-token-should-never-be-logged"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {secret_token}"
        return httpx.Response(404, json={"error": "not found"})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(client, "_http_client", mock_client)

    vanta_client = client.VantaClient(secret_token, "https://api.vanta.com")

    with caplog.at_level(logging.WARNING, logger="vanta_mcp"):
        with pytest.raises(ToolError) as exc_info:
            await vanta_client.get("controls", resource_id="missing-id")

    assert exc_info.value.code == "not_found"
    assert len(caplog.records) == 1
    line = caplog.records[0].message
    payload = json.loads(line)
    assert payload["event"] == "upstream_error"
    assert payload["tool"] == "controls"
    assert payload["status"] == 404
    assert payload["code"] == "not_found"
    assert "path" in payload and "request_id" in payload
    # The one thing this fix must never do: put the secret in the log line.
    assert secret_token not in line


# --- auth.py: fetch_access_token --------------------------------------------


@pytest.mark.asyncio
async def test_token_endpoint_error_is_logged_without_leaking_the_client_secret(
    monkeypatch, caplog
):
    client_secret = "sekret-client-secret-should-never-be-logged"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_client"})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(auth, "_token_http_client", mock_client)

    with caplog.at_level(logging.WARNING, logger="vanta_mcp"):
        with pytest.raises(ToolError) as exc_info:
            await auth.fetch_access_token("some-client-id", client_secret, Settings())

    assert exc_info.value.code == "unauthorized"
    assert len(caplog.records) == 1
    line = caplog.records[0].message
    payload = json.loads(line)
    assert payload["event"] == "upstream_error"
    assert payload["tool"] == "oauth_token"
    assert payload["status"] == 401
    assert payload["code"] == "unauthorized"
    assert client_secret not in line


# --- middleware.py: missing-credentials 401 ---------------------------------


@pytest.mark.asyncio
async def test_missing_credentials_401_is_logged():
    mw = GatewayCredentialsMiddleware(lambda scope, receive, send: None, Settings())
    send = _RecordingSend()

    import logging as _logging

    _logger = _logging.getLogger("vanta_mcp")
    records: list[_logging.LogRecord] = []
    handler = _logging.Handler()
    handler.emit = records.append  # type: ignore[method-assign]
    _logger.addHandler(handler)
    try:
        await mw(_scope(headers={}), _empty_receive, send)
    finally:
        _logger.removeHandler(handler)

    assert send.status == 401
    assert len(records) == 1
    payload = json.loads(records[0].getMessage())
    assert payload["event"] == "missing_credentials"
    assert payload["status"] == 401
    assert payload["code"] == "unauthorized"
    assert payload["path"] == "/mcp"
