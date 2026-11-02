"""Vanta OAuth2 client_credentials token exchange.

Called fresh on every tool call — the access token is never cached across
requests (SOP §3.4, Tech design § Option analysis: Credential model). Vanta
tokens live 3600s, but caching one tenant's token on this process would risk
leaking it into another tenant's concurrent request; the per-call exchange
cost is accepted in exchange for that isolation guarantee.
"""

import asyncio

import httpx

from .config import Settings
from .errors import ToolError
from .logging_utils import log_structured

_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)
_MAX_RETRIES = 2
_MAX_BACKOFF_SECONDS = 10.0

_token_http_client: httpx.AsyncClient | None = None


def _get_token_http_client() -> httpx.AsyncClient:
    global _token_http_client
    if _token_http_client is None:
        _token_http_client = httpx.AsyncClient(timeout=_TIMEOUT)
    return _token_http_client


def _retry_delay(resp: httpx.Response, attempt: int) -> float:
    retry_after = resp.headers.get("Retry-After")
    if retry_after:
        try:
            return min(float(retry_after), _MAX_BACKOFF_SECONDS)
        except ValueError:
            pass
    return min(2**attempt, _MAX_BACKOFF_SECONDS)


def _log_and_raise(
    token_url: str, status: int | None, code: str, message: str, *, cause: Exception | None = None
) -> None:
    """One structured log line (SOP §6) then raise. Never logs the client
    id/secret or the raw response body -- only the endpoint, status, and our
    own mapped error `code`."""
    log_structured("upstream_error", tool="oauth_token", path=token_url, status=status, code=code)
    raise ToolError(code, message) from cause


async def fetch_access_token(client_id: str, client_secret: str, settings: Settings) -> str:
    """Exchange (client_id, client_secret) for a short-lived Bearer token.

    Vanta's token endpoint is limited to 5 requests/minute shared across all
    of that OAuth client's API usage (Tech design § Non-functional). A 429
    here is retried up to `_MAX_RETRIES` times respecting `Retry-After`,
    then surfaced as a structured `rate_limited` error (AC13/TC-VAN-013).
    """
    client = _get_token_http_client()
    url = settings.vanta_token_url

    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = await client.post(
                url,
                json={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "scope": settings.vanta_oauth_scope,
                },
            )
        except httpx.RequestError as exc:
            _log_and_raise(
                url, None, "upstream_error", f"Token endpoint unreachable: {exc}", cause=exc
            )

        if resp.status_code == 429:
            if attempt < _MAX_RETRIES:
                await asyncio.sleep(_retry_delay(resp, attempt))
                continue
            _log_and_raise(
                url,
                resp.status_code,
                "rate_limited",
                "Vanta token endpoint rate limit exceeded",
            )

        if resp.status_code in (400, 401, 403):
            _log_and_raise(
                url, resp.status_code, "unauthorized", "Vanta rejected the client id/secret"
            )

        if resp.status_code >= 500:
            _log_and_raise(
                url,
                resp.status_code,
                "upstream_error",
                f"Token endpoint returned {resp.status_code}",
            )

        if resp.status_code >= 400:
            _log_and_raise(
                url,
                resp.status_code,
                "invalid_argument",
                f"Token endpoint returned {resp.status_code}",
            )

        try:
            body = resp.json()
        except ValueError as exc:
            _log_and_raise(
                url,
                resp.status_code,
                "upstream_error",
                "Token endpoint response was not valid JSON",
                cause=exc,
            )

        token = body.get("access_token")
        if not token:
            _log_and_raise(
                url,
                resp.status_code,
                "upstream_error",
                "Token endpoint response missing access_token",
            )
        return token

    # Unreachable in practice (loop always returns or raises above), but keeps
    # type checkers happy and guards against future edits.
    raise ToolError("rate_limited", "Vanta token endpoint rate limit exceeded")
