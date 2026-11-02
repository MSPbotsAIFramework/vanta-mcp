from collections.abc import Callable

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .config import Settings
from .middleware import get_vanta_credentials

SERVICE_INSTRUCTIONS = (
    "Vanta: read a tenant's security/compliance posture. 7 read-only tools, "
    "one per resource area: vanta_get_controls, vanta_get_tests, "
    "vanta_get_documents, vanta_get_vendors, vanta_get_people, "
    "vanta_get_monitored_computers, vanta_get_issues. Each tool lists "
    "(filters + pagination) when called without an id, or fetches a single "
    "record when called with one. Typical flow: list with a status/severity "
    "filter to find what needs attention, then get by id for full detail. "
    "Offboarding checks use vanta_get_people with employment_status=FORMER. "
    "No write tools exist in this service."
)


def create_mcp_server(settings: Settings) -> FastMCP:
    """Build the FastMCP server instance and register all Vanta tools.

    `stateless_http=True` is required (not just "no session stickiness") --
    the MCP Python SDK's default session manager rejects any request that
    doesn't start with an `initialize` handshake (SOP §1.3). DNS-rebinding
    protection is disabled because this service is never exposed directly to
    the internet; it only ever receives proxied calls from the Gateway
    (SOP §7).
    """
    mcp = FastMCP(
        name="vanta-mcp",
        instructions=SERVICE_INSTRUCTIONS,
        stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    client_factory: Callable[[], tuple[str, str] | None] = get_vanta_credentials

    from .tools import (
        controls,
        documents,
        issues,
        monitored_computers,
        people,
        tests,
        vendors,
    )

    controls.register(mcp, client_factory, settings)
    tests.register(mcp, client_factory, settings)
    documents.register(mcp, client_factory, settings)
    vendors.register(mcp, client_factory, settings)
    people.register(mcp, client_factory, settings)
    monitored_computers.register(mcp, client_factory, settings)
    issues.register(mcp, client_factory, settings)

    return mcp
