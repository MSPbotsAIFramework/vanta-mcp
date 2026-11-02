"""Vanta monitored-device compliance signals (tools/monitored_computers.py)."""

import json
from collections.abc import Callable

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .. import client
from ..config import Settings
from ..errors import ToolError

FIELDS = [
    "id",
    "serialNumber",
    "lastCheckDate",
    "screenlockOutcome",
    "diskEncryptionOutcome",
    "passwordManagerOutcome",
    "antivirusOutcome",
    "operatingSystem",
    "owner",
]


def register(
    mcp: FastMCP,
    client_factory: Callable[[], tuple[str, str] | None],
    settings: Settings,
) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def vanta_get_monitored_computers(
        id: str | None = None,
        compliance_status: str | None = None,
        limit: int = client.DEFAULT_LIMIT,
        cursor: str | None = None,
    ) -> str:
        """List or get monitored-device compliance records.

        Without id: lists devices, optionally filtered by compliance_status
        (e.g. HD_NOT_ENCRYPTED, AV_NOT_INSTALLED, SCREENLOCK_NOT_CONFIGURED,
        PWM_NOT_INSTALLED, LAST_CHECK_OVER_14_DAYS). With id: returns that
        single device, or a not_found error."""
        try:
            vanta = await client.build_client(client_factory(), settings)
            if id:
                raw = await vanta.get("monitored-computers", resource_id=id)
                envelope = client.normalize_get_envelope(raw)
                result = {"data": client.project_fields(envelope["data"], FIELDS)}
            else:
                params = {
                    "complianceStatusFilterMatchesAny": compliance_status,
                    "pageSize": client.clamp_limit(limit),
                    "pageCursor": cursor,
                }
                raw = await vanta.get("monitored-computers", params=params)
                envelope = client.normalize_list_envelope(raw)
                result = {
                    "data": [client.project_fields(r, FIELDS) for r in envelope["data"]],
                    "has_more": envelope["has_more"],
                    "next_cursor": envelope["next_cursor"],
                }
            return json.dumps(result, separators=(",", ":"), ensure_ascii=False)
        except ToolError as exc:
            return exc.to_json()
