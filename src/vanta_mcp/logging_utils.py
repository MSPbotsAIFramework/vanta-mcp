"""Structured logging helpers (SOP §6, PRD-21248 review round 1 must-fix).

Every log line is a single compact JSON object carrying the current
request's id, so one request's lines can be correlated. Only the tool
(resource) name, the upstream path, status codes, and our own mapped
error `code` are logged -- never credentials, tokens, or response bodies.
"""

import contextvars
import json
import logging
import uuid

logger = logging.getLogger("vanta_mcp")

_request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "vanta_request_id", default=None
)


def new_request_id() -> str:
    """A short id to correlate every log line for one request."""
    return uuid.uuid4().hex[:12]


def bind_request_id(request_id: str) -> contextvars.Token:
    return _request_id_var.set(request_id)


def reset_request_id(token: contextvars.Token) -> None:
    _request_id_var.reset(token)


def get_request_id() -> str:
    return _request_id_var.get() or "-"


def log_structured(event: str, *, level: int = logging.WARNING, **fields: object) -> None:
    """Emit one structured log line.

    `fields` must never contain credentials, tokens, or raw upstream
    response bodies -- only identifiers such as tool/resource name,
    upstream path, status codes, and our own mapped error `code`.
    """
    payload = {"event": event, "request_id": get_request_id(), **fields}
    logger.log(level, json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
