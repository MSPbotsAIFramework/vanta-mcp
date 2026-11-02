"""Vanta control test results (tools/tests.py)."""

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
    "status",
    "description",
    "failureDescription",
    "remediationDescription",
    "lastTestRunDate",
    "latestFlipDate",
    "category",
    "owner",
]


def register(
    mcp: FastMCP,
    client_factory: Callable[[], tuple[str, str] | None],
    settings: Settings,
) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def vanta_get_tests(
        id: str | None = None,
        status: str | None = None,
        framework: str | None = None,
        control_id: str | None = None,
        owner_id: str | None = None,
        category: str | None = None,
        limit: int = client.DEFAULT_LIMIT,
        cursor: str | None = None,
    ) -> str:
        """List or get automated control test results.

        Without id: lists tests, optionally filtered by status (e.g.
        NEEDS_ATTENTION), framework, control_id, owner_id, or category. With
        id: returns that single test, or a not_found error."""
        try:
            vanta = await client.build_client(client_factory(), settings)
            if id:
                raw = await vanta.get("tests", resource_id=id)
                envelope = client.normalize_get_envelope(raw)
                result = {"data": client.project_fields(envelope["data"], FIELDS)}
            else:
                params = {
                    "statusFilter": status,
                    "frameworkFilter": framework,
                    "controlFilter": control_id,
                    "ownerFilter": owner_id,
                    "categoryFilter": category,
                    "pageSize": client.clamp_limit(limit),
                    "pageCursor": cursor,
                }
                raw = await vanta.get("tests", params=params)
                envelope = client.normalize_list_envelope(raw)
                result = {
                    "data": [client.project_fields(r, FIELDS) for r in envelope["data"]],
                    "has_more": envelope["has_more"],
                    "next_cursor": envelope["next_cursor"],
                }
            return json.dumps(result, separators=(",", ":"), ensure_ascii=False)
        except ToolError as exc:
            return exc.to_json()
