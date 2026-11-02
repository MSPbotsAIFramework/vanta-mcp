"""Vanta compliance documents (tools/documents.py)."""

import json
from collections.abc import Callable

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .. import client
from ..config import Settings
from ..errors import ToolError

FIELDS = [
    "id",
    "title",
    "description",
    "category",
    "uploadStatus",
    "uploadStatusDate",
    "isSensitive",
    "url",
    "ownerId",
]


def register(
    mcp: FastMCP,
    client_factory: Callable[[], tuple[str, str] | None],
    settings: Settings,
) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def vanta_get_documents(
        id: str | None = None,
        framework: str | None = None,
        status: str | None = None,
        limit: int = client.DEFAULT_LIMIT,
        cursor: str | None = None,
    ) -> str:
        """List or get compliance documents (policies/evidence).

        Without id: lists documents, optionally filtered by framework or
        upload status. With id: returns that single document, including its
        uploadStatus (e.g. "Needs document", "OK"), or a not_found error."""
        try:
            vanta = await client.build_client(client_factory(), settings)
            if id:
                raw = await vanta.get("documents", resource_id=id)
                envelope = client.normalize_get_envelope(raw)
                result = {"data": client.project_fields(envelope["data"], FIELDS)}
            else:
                params = {
                    "frameworkMatchesAny": framework,
                    "statusMatchesAny": status,
                    "pageSize": client.clamp_limit(limit),
                    "pageCursor": cursor,
                }
                raw = await vanta.get("documents", params=params)
                envelope = client.normalize_list_envelope(raw)
                result = {
                    "data": [client.project_fields(r, FIELDS) for r in envelope["data"]],
                    "has_more": envelope["has_more"],
                    "next_cursor": envelope["next_cursor"],
                }
            return json.dumps(result, separators=(",", ":"), ensure_ascii=False)
        except ToolError as exc:
            return exc.to_json()
