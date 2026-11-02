"""Vanta security controls (tools/controls.py).

Tool naming convention: vanta_<action>_<resource>.
"""

import json
from collections.abc import Callable

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .. import client
from ..config import Settings
from ..errors import ToolError

FIELDS = [
    "id",
    "name",
    "description",
    "source",
    "domains",
    "owner",
    "creationDate",
    "modificationDate",
]


def register(
    mcp: FastMCP,
    client_factory: Callable[[], tuple[str, str] | None],
    settings: Settings,
) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def vanta_get_controls(
        id: str | None = None,
        framework: str | None = None,
        limit: int = client.DEFAULT_LIMIT,
        cursor: str | None = None,
    ) -> str:
        """List or get security controls.

        Without id: lists controls, optionally filtered by framework,
        paginated via limit/cursor. With id: returns that single control, or
        a not_found error if it doesn't exist in this tenant."""
        try:
            vanta = await client.build_client(client_factory(), settings)
            if id:
                raw = await vanta.get("controls", resource_id=id)
                envelope = client.normalize_get_envelope(raw)
                result = {"data": client.project_fields(envelope["data"], FIELDS)}
            else:
                params = {
                    "frameworkMatchesAny": framework,
                    "pageSize": client.clamp_limit(limit),
                    "pageCursor": cursor,
                }
                raw = await vanta.get("controls", params=params)
                envelope = client.normalize_list_envelope(raw)
                result = {
                    "data": [client.project_fields(r, FIELDS) for r in envelope["data"]],
                    "has_more": envelope["has_more"],
                    "next_cursor": envelope["next_cursor"],
                }
            return json.dumps(result, separators=(",", ":"), ensure_ascii=False)
        except ToolError as exc:
            return exc.to_json()
