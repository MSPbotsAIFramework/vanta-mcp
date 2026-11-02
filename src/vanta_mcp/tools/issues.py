"""Vanta open compliance issues/findings (tools/issues.py)."""

import json
from collections.abc import Callable

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .. import client
from ..config import Settings
from ..errors import ToolError

FIELDS = [
    "id",
    "readableIssueId",
    "severity",
    "status",
    "source",
    "createdDate",
    "detectedDate",
    "dueDate",
    "lastModifiedDate",
    "correctiveAction",
    "rootCause",
    "mappedControlIds",
    "owners",
]


def register(
    mcp: FastMCP,
    client_factory: Callable[[], tuple[str, str] | None],
    settings: Settings,
) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def vanta_get_issues(
        id: str | None = None,
        search: str | None = None,
        status: str | None = None,
        severity: str | None = None,
        source: str | None = None,
        owner_id: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        due_after: str | None = None,
        due_before: str | None = None,
        order_by: str | None = None,
        order_direction: str | None = None,
        limit: int = client.DEFAULT_LIMIT,
        cursor: str | None = None,
    ) -> str:
        """List or get open compliance issues/findings.

        Without id: lists issues, filterable by severity
        (CRITICAL|HIGH|MEDIUM|LOW|NO_SEVERITY), status, source, owner_id, a
        free-text search, and created/due date ranges (ISO 8601). With id:
        returns that single issue, or a not_found error."""
        try:
            vanta = await client.build_client(client_factory(), settings)
            if id:
                raw = await vanta.get("issues", resource_id=id)
                envelope = client.normalize_get_envelope(raw)
                result = {"data": client.project_fields(envelope["data"], FIELDS)}
            else:
                params = {
                    "search": search,
                    "statusMatchesAny": status,
                    "severityMatchesAny": severity,
                    "sourceMatchesAny": source,
                    "ownerIdMatchesAny": owner_id,
                    "createdAfter": created_after,
                    "createdBefore": created_before,
                    "dueAfter": due_after,
                    "dueBefore": due_before,
                    "orderBy": order_by,
                    "orderDirection": order_direction,
                    "pageSize": client.clamp_limit(limit),
                    "pageCursor": cursor,
                }
                raw = await vanta.get("issues", params=params)
                envelope = client.normalize_list_envelope(raw)
                result = {
                    "data": [client.project_fields(r, FIELDS) for r in envelope["data"]],
                    "has_more": envelope["has_more"],
                    "next_cursor": envelope["next_cursor"],
                }
            return json.dumps(result, separators=(",", ":"), ensure_ascii=False)
        except ToolError as exc:
            return exc.to_json()
