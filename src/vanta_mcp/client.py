"""Shared HTTP client for Vanta's "Manage Vanta API" (api.vanta.com).

One module-level connection pool for the process lifetime (SOP §5 connection
reuse). No credentials are ever stored on it — the Bearer token is passed
per-call via headers, so sharing the pool across tenants/requests is safe;
request isolation is enforced by the per-request ContextVar in middleware.py,
not by anything in this module.
"""

import asyncio
from typing import Any

import httpx

from .auth import fetch_access_token
from .config import Settings
from .errors import ToolError
from .logging_utils import log_structured

_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)
_MAX_RETRIES = 2
_MAX_BACKOFF_SECONDS = 10.0
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

DEFAULT_LIMIT = 25
MAX_LIMIT = 100

_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=_TIMEOUT)
    return _http_client


def _retry_delay(resp: httpx.Response, attempt: int) -> float:
    retry_after = resp.headers.get("Retry-After")
    if retry_after:
        try:
            return min(float(retry_after), _MAX_BACKOFF_SECONDS)
        except ValueError:
            pass
    return min(2**attempt, _MAX_BACKOFF_SECONDS)


def clamp_limit(limit: int | None) -> int:
    """Clamp a caller-supplied limit to Vanta's own ceiling (AC12/TC-VAN-012).

    A limit above 100 is clamped rather than rejected; a missing limit falls
    back to the service's own default of 25.
    """
    if limit is None:
        return DEFAULT_LIMIT
    return max(1, min(int(limit), MAX_LIMIT))


class VantaClient:
    """Wraps one resource area's `GET /<resource>` and `GET /<resource>/{id}`
    calls against api.vanta.com, using an already-fetched Bearer token.
    """

    def __init__(self, access_token: str, base_url: str):
        self._token = access_token
        self._base_url = base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }

    async def get(
        self,
        resource: str,
        resource_id: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict:
        """GET /<resource> (list mode) or /<resource>/{id} (get mode).

        Returns the parsed JSON body. Raises `ToolError` for every non-2xx
        response per the Error mapping table (Tech design § Data & API
        contract); retries `429`/`5xx` up to `_MAX_RETRIES` times respecting
        `Retry-After`.
        """
        client = _get_http_client()
        path = f"{self._base_url}/{resource}"
        if resource_id:
            path = f"{path}/{resource_id}"
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}

        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = await client.get(path, headers=self._headers(), params=clean_params)
            except httpx.RequestError as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(min(2**attempt, _MAX_BACKOFF_SECONDS))
                    continue
                log_structured(
                    "upstream_error", tool=resource, path=path, status=None, code="upstream_error"
                )
                raise ToolError(
                    "upstream_error", f"{resource} request unreachable: {exc}"
                ) from exc

            if resp.status_code in _RETRYABLE_STATUS and attempt < _MAX_RETRIES:
                await asyncio.sleep(_retry_delay(resp, attempt))
                continue

            self._raise_for_status(resource, resource_id, path, resp)
            try:
                return resp.json()
            except ValueError as exc:
                log_structured(
                    "upstream_error",
                    tool=resource,
                    path=path,
                    status=resp.status_code,
                    code="upstream_error",
                )
                raise ToolError(
                    "upstream_error", f"{resource}: response was not valid JSON"
                ) from exc

        if last_exc:
            raise ToolError("upstream_error", f"{resource} request failed: {last_exc}")
        raise ToolError("upstream_error", f"{resource} request failed with no response")

    def _raise_for_status(
        self, resource: str, resource_id: str | None, path: str, resp: httpx.Response
    ) -> None:
        if resp.status_code < 400:
            return
        if resp.status_code == 404:
            code, message = "not_found", f"No {resource} found for id={resource_id}"
        elif resp.status_code in (401, 403):
            code, message = "unauthorized", f"Vanta rejected the request for {resource}"
        elif resp.status_code in (400, 422):
            code, message = "invalid_argument", f"Invalid parameters for {resource}"
        elif resp.status_code == 429:
            code, message = "rate_limited", f"Vanta rate limit exceeded for {resource}"
        elif resp.status_code >= 500:
            code, message = "upstream_error", f"{resource} upstream error {resp.status_code}"
        else:
            code, message = "upstream_error", f"{resource} unexpected status {resp.status_code}"
        # SOP §6: structured log line on every upstream error -- tool/resource
        # name, upstream path, status code, our mapped code. Never the
        # response body or any credential/header value.
        log_structured(
            "upstream_error", tool=resource, path=path, status=resp.status_code, code=code
        )
        raise ToolError(code, message)


async def build_client(credentials: tuple[str, str] | None, settings: Settings) -> VantaClient:
    """Exchange the current request's credentials for a token and build a
    `VantaClient`. `credentials` is `None` when the middleware let a request
    through without ever being bound to a request (defensive only — the
    middleware already returns 401 before any tool runs in the normal path).
    """
    if credentials is None:
        raise ToolError("not_configured", "No Vanta credentials bound to this request")
    client_id, client_secret = credentials
    token = await fetch_access_token(client_id, client_secret, settings)
    return VantaClient(token, settings.vanta_base_url)


def normalize_list_envelope(raw: dict) -> dict:
    """Normalize Vanta's upstream list envelope into this service's
    `{"data": [...], "has_more": bool, "next_cursor": str | None}` shape
    (Tech design § Data & API contract, SOP §2.5 token economy)."""
    results = raw.get("results", raw)
    data = results.get("data", []) if isinstance(results, dict) else results
    page_info = results.get("pageInfo", {}) if isinstance(results, dict) else {}
    return {
        "data": data,
        "has_more": bool((page_info or {}).get("hasNextPage", False)),
        "next_cursor": (page_info or {}).get("endCursor"),
    }


def normalize_get_envelope(raw: dict) -> dict:
    """Normalize Vanta's upstream single-resource envelope into this
    service's `{"data": {...}}` shape."""
    results = raw.get("results", raw)
    return {"data": results}


def project_fields(record: dict, fields: list[str]) -> dict:
    """Select a curated subset of a Vanta object's top-level fields (SOP §2.5
    token economy, Tech design § Option analysis: Response field selection).
    Nested objects (e.g. `owner`, `name`) are passed through whole — the
    curation happens at the top-level-field list, not by pruning inside them.
    """
    return {field: record.get(field) for field in fields}
