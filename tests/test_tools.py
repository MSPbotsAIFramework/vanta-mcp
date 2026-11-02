"""tools/list snapshot + per-tool filter/pagination/projection tests
(PRD-21248 TC-VAN-001..009, TC-VAN-012).

The Vanta API itself is never called: `client.build_client` is monkeypatched
to return a `FakeVantaClient` that records the (resource, resource_id,
params) each tool passed in and returns a scripted raw upstream envelope, so
the real pagination/field-projection code in client.py and each tools/*.py
module runs against a known input.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from vanta_mcp import client
from vanta_mcp.config import Settings
from vanta_mcp.middleware import _vanta_credentials_var
from vanta_mcp.server import create_mcp_server

EXPECTED_TOOLS = {
    "vanta_get_controls",
    "vanta_get_tests",
    "vanta_get_documents",
    "vanta_get_vendors",
    "vanta_get_people",
    "vanta_get_monitored_computers",
    "vanta_get_issues",
}


def _mcp():
    return create_mcp_server(Settings())


def _tool_fn(mcp, name: str):
    return mcp._tool_manager.get_tool(name).fn


class FakeVantaClient:
    def __init__(self, raw_by_resource: dict[str, Any]):
        self._raw_by_resource = raw_by_resource
        self.calls: list[tuple[str, str | None, dict | None]] = []

    async def get(self, resource, resource_id=None, params=None):
        self.calls.append((resource, resource_id, params))
        raw = self._raw_by_resource[resource]
        if resource_id is not None and resource_id not in raw.get("by_id", {}):
            from vanta_mcp.errors import ToolError

            raise ToolError("not_found", f"No {resource} found for id={resource_id}")
        if resource_id is not None:
            return {"results": raw["by_id"][resource_id]}
        return raw["list"]


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch):
    """Install a FakeVantaClient and drive it through the real contextvar ->
    server.client_factory -> client.build_client wiring (only the network
    boundary, client.build_client, is faked)."""
    holder: dict[str, FakeVantaClient] = {}

    async def _build_client(credentials, settings):
        assert credentials == ("fake-id", "fake-secret")
        return holder["client"]

    def install(raw_by_resource: dict[str, Any]) -> FakeVantaClient:
        fake = FakeVantaClient(raw_by_resource)
        holder["client"] = fake
        return fake

    monkeypatch.setattr(client, "build_client", _build_client)
    token = _vanta_credentials_var.set(("fake-id", "fake-secret"))
    yield install
    _vanta_credentials_var.reset(token)


# --- tools/list snapshot (SOP §2.2/§2.6/§10 minimal set) --------------------


def test_tools_list_snapshot():
    import asyncio

    mcp = _mcp()
    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}

    assert names == EXPECTED_TOOLS
    assert len(EXPECTED_TOOLS) == 7
    for tool in tools:
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is True
        assert tool.description, f"{tool.name} has no description"
        assert len(tool.description) <= 500, f"{tool.name} description > 500 chars"
        first_line = tool.description.splitlines()[0]
        assert len(first_line) <= 100, f"{tool.name} first description line > 100 chars"


# --- TC-VAN-001 (AC1) --------------------------------------------------------


@pytest.mark.asyncio
async def test_list_controls_returns_mapped_fields_and_pagination(fake_client):
    fake = fake_client(
        {
            "controls": {
                "list": {
                    "results": {
                        "data": [
                            {
                                "id": "c1",
                                "name": "Control 1",
                                "description": "desc",
                                "source": "POLICY",
                                "domains": ["SECURITY"],
                                "owner": {
                                    "id": "u1",
                                    "displayName": "Alice",
                                    "emailAddress": "alice@example.com",
                                },
                                "creationDate": "2026-01-01T00:00:00Z",
                                "modificationDate": "2026-01-02T00:00:00Z",
                                "customFields": {"drop": "me"},
                                "implementationDetails": "drop me too",
                            }
                        ],
                        "pageInfo": {"hasNextPage": True, "endCursor": "cursor-1"},
                    }
                },
                "by_id": {},
            }
        }
    )
    mcp = _mcp()
    fn = _tool_fn(mcp, "vanta_get_controls")

    result = json.loads(await fn())

    assert result["has_more"] is True
    assert result["next_cursor"] == "cursor-1"
    assert len(result["data"]) == 1
    control = result["data"][0]
    assert control["id"] == "c1"
    assert control["owner"] == {
        "id": "u1",
        "displayName": "Alice",
        "emailAddress": "alice@example.com",
    }
    # Curated fields only -- internal/unused upstream fields are dropped.
    assert "customFields" not in control
    assert "implementationDetails" not in control
    resource, resource_id, params = fake.calls[0]
    assert resource == "controls"
    assert resource_id is None
    assert params["pageSize"] == 25  # DEFAULT_LIMIT


# --- TC-VAN-002 (AC2) --------------------------------------------------------


@pytest.mark.asyncio
async def test_get_control_by_known_id_returns_single_object(fake_client):
    fake_client(
        {
            "controls": {
                "list": {"results": {"data": [], "pageInfo": {}}},
                "by_id": {"c1": {"id": "c1", "name": "Control 1", "source": "POLICY"}},
            }
        }
    )
    mcp = _mcp()
    fn = _tool_fn(mcp, "vanta_get_controls")

    result = json.loads(await fn(id="c1"))

    assert result["data"]["id"] == "c1"
    assert result["data"]["name"] == "Control 1"


@pytest.mark.asyncio
async def test_get_control_by_unknown_id_returns_not_found(fake_client):
    fake_client({"controls": {"list": {"results": {"data": [], "pageInfo": {}}}, "by_id": {}}})
    mcp = _mcp()
    fn = _tool_fn(mcp, "vanta_get_controls")

    result = json.loads(await fn(id="does-not-exist"))

    assert result["error"]["code"] == "not_found"
    assert result["error"]["retryable"] is False


# --- TC-VAN-003 (AC3) --------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tests_status_filter_maps_to_statusFilter(fake_client):
    fake = fake_client(
        {
            "tests": {
                "list": {
                    "results": {
                        "data": [
                            {
                                "id": "t1",
                                "name": "Test 1",
                                "status": "NEEDS_ATTENTION",
                                "description": "d",
                                "failureDescription": "fail",
                                "remediationDescription": "fix",
                                "lastTestRunDate": "2026-01-01T00:00:00Z",
                                "latestFlipDate": "2026-01-01T00:00:00Z",
                                "category": "ACCESS_CONTROL",
                                "owner": {"id": "u1", "displayName": "Alice"},
                            }
                        ],
                        "pageInfo": {},
                    }
                },
                "by_id": {},
            }
        }
    )
    mcp = _mcp()
    fn = _tool_fn(mcp, "vanta_get_tests")

    result = json.loads(await fn(status="NEEDS_ATTENTION"))

    assert len(result["data"]) == 1
    assert result["data"][0]["status"] == "NEEDS_ATTENTION"
    _, _, params = fake.calls[0]
    assert params["statusFilter"] == "NEEDS_ATTENTION"


# --- TC-VAN-004 (AC4) --------------------------------------------------------


@pytest.mark.asyncio
async def test_list_documents_includes_upload_status(fake_client):
    fake_client(
        {
            "documents": {
                "list": {
                    "results": {
                        "data": [
                            {
                                "id": "d1",
                                "title": "SOC 2 Policy",
                                "description": "d",
                                "category": "POLICY",
                                "uploadStatus": "Needs document",
                                "uploadStatusDate": "2026-01-01T00:00:00Z",
                                "isSensitive": False,
                                "url": "https://app.vanta.com/documents/d1",
                                "ownerId": "u1",
                            }
                        ],
                        "pageInfo": {},
                    }
                },
                "by_id": {},
            }
        }
    )
    mcp = _mcp()
    fn = _tool_fn(mcp, "vanta_get_documents")

    result = json.loads(await fn())

    assert result["data"][0]["uploadStatus"] == "Needs document"


# --- TC-VAN-005 (AC5) --------------------------------------------------------


@pytest.mark.asyncio
async def test_list_vendors_includes_risk_and_review_fields(fake_client):
    fake_client(
        {
            "vendors": {
                "list": {
                    "results": {
                        "data": [
                            {
                                "id": "v1",
                                "name": "Acme Vendor",
                                "status": "ACTIVE",
                                "inherentRiskLevel": "HIGH",
                                "residualRiskLevel": "MEDIUM",
                                "websiteUrl": "https://acme.example.com",
                                "lastSecurityReviewCompletionDate": "2026-01-01T00:00:00Z",
                                "nextSecurityReviewDueDate": "2027-01-01T00:00:00Z",
                                "latestDecision": "APPROVED",
                                "category": "SAAS",
                            }
                        ],
                        "pageInfo": {},
                    }
                },
                "by_id": {},
            }
        }
    )
    mcp = _mcp()
    fn = _tool_fn(mcp, "vanta_get_vendors")

    result = json.loads(await fn())

    vendor = result["data"][0]
    assert vendor["status"] == "ACTIVE"
    assert vendor["inherentRiskLevel"] == "HIGH"
    assert vendor["residualRiskLevel"] == "MEDIUM"
    assert vendor["nextSecurityReviewDueDate"] == "2027-01-01T00:00:00Z"


# --- TC-VAN-006 (AC6) --------------------------------------------------------


@pytest.mark.asyncio
async def test_list_people_employment_status_filter(fake_client):
    fake = fake_client(
        {
            "people": {
                "list": {
                    "results": {
                        "data": [
                            {
                                "id": "p1",
                                "userId": "u1",
                                "emailAddress": "former@example.com",
                                "name": {"first": "Jo", "last": "Doe", "display": "Jo Doe"},
                                "employment": {
                                    "status": "FORMER",
                                    "startDate": "2020-01-01",
                                    "jobTitle": "Engineer",
                                    "endDate": "2026-01-01",
                                },
                                "leaveInfo": None,
                            }
                        ],
                        "pageInfo": {},
                    }
                },
                "by_id": {},
            }
        }
    )
    mcp = _mcp()
    fn = _tool_fn(mcp, "vanta_get_people")

    result = json.loads(await fn(employment_status="FORMER"))

    assert result["data"][0]["employment"]["status"] == "FORMER"
    _, _, params = fake.calls[0]
    assert params["employmentStatus"] == "FORMER"


# --- TC-VAN-007 (AC7) --------------------------------------------------------


@pytest.mark.asyncio
async def test_get_person_by_known_id_returns_single_object(fake_client):
    fake_client(
        {
            "people": {
                "list": {"results": {"data": [], "pageInfo": {}}},
                "by_id": {"p1": {"id": "p1", "userId": "u1", "emailAddress": "a@example.com"}},
            }
        }
    )
    mcp = _mcp()
    fn = _tool_fn(mcp, "vanta_get_people")

    result = json.loads(await fn(id="p1"))

    assert result["data"]["id"] == "p1"
    assert result["data"]["emailAddress"] == "a@example.com"


# --- TC-VAN-008 (AC8) --------------------------------------------------------


@pytest.mark.asyncio
async def test_list_monitored_computers_compliance_status_filter(fake_client):
    fake = fake_client(
        {
            "monitored-computers": {
                "list": {
                    "results": {
                        "data": [
                            {
                                "id": "m1",
                                "serialNumber": "SN123",
                                "lastCheckDate": "2026-01-01T00:00:00Z",
                                "screenlockOutcome": "PASS",
                                "diskEncryptionOutcome": "FAIL",
                                "passwordManagerOutcome": "PASS",
                                "antivirusOutcome": "PASS",
                                "operatingSystem": {"type": "macOS", "version": "15.0"},
                                "owner": {"id": "u1", "displayName": "Alice"},
                            }
                        ],
                        "pageInfo": {},
                    }
                },
                "by_id": {},
            }
        }
    )
    mcp = _mcp()
    fn = _tool_fn(mcp, "vanta_get_monitored_computers")

    result = json.loads(await fn(compliance_status="HD_NOT_ENCRYPTED"))

    assert result["data"][0]["diskEncryptionOutcome"] == "FAIL"
    _, _, params = fake.calls[0]
    assert params["complianceStatusFilterMatchesAny"] == "HD_NOT_ENCRYPTED"


# --- TC-VAN-009 (AC9) --------------------------------------------------------


@pytest.mark.asyncio
async def test_list_issues_severity_and_date_range_filters(fake_client):
    fake = fake_client(
        {
            "issues": {
                "list": {
                    "results": {
                        "data": [
                            {
                                "id": "i1",
                                "readableIssueId": "VAN-1",
                                "severity": "CRITICAL",
                                "status": "OPEN",
                                "source": {"sourceType": "TEST"},
                                "createdDate": "2026-01-01T00:00:00Z",
                                "detectedDate": "2026-01-01T00:00:00Z",
                                "dueDate": "2026-02-01T00:00:00Z",
                                "lastModifiedDate": "2026-01-02T00:00:00Z",
                                "correctiveAction": "Patch it",
                                "rootCause": "Misconfiguration",
                                "mappedControlIds": ["c1"],
                                "owners": [{"id": "u1", "displayName": "Alice"}],
                            }
                        ],
                        "pageInfo": {},
                    }
                },
                "by_id": {},
            }
        }
    )
    mcp = _mcp()
    fn = _tool_fn(mcp, "vanta_get_issues")

    result = json.loads(
        await fn(severity="CRITICAL", due_after="2026-01-01", due_before="2026-03-01")
    )

    issue = result["data"][0]
    assert issue["severity"] == "CRITICAL"
    assert issue["dueDate"] == "2026-02-01T00:00:00Z"
    assert issue["correctiveAction"] == "Patch it"
    _, _, params = fake.calls[0]
    assert params["severityMatchesAny"] == "CRITICAL"
    assert params["dueAfter"] == "2026-01-01"
    assert params["dueBefore"] == "2026-03-01"


# --- TC-VAN-012 (AC12) -------------------------------------------------------


@pytest.mark.asyncio
async def test_limit_above_vendor_ceiling_is_clamped_to_100(fake_client):
    fake = fake_client(
        {"controls": {"list": {"results": {"data": [], "pageInfo": {}}}, "by_id": {}}}
    )
    mcp = _mcp()
    fn = _tool_fn(mcp, "vanta_get_controls")

    await fn(limit=500)

    _, _, params = fake.calls[0]
    assert params["pageSize"] == 100


def test_clamp_limit_defaults_and_bounds():
    assert client.clamp_limit(None) == 25
    assert client.clamp_limit(500) == 100
    assert client.clamp_limit(0) == 1
    assert client.clamp_limit(-5) == 1
    assert client.clamp_limit(50) == 50


@pytest.mark.asyncio
async def test_next_cursor_round_trip_and_final_page_has_more_false(fake_client):
    fake = fake_client(
        {
            "controls": {
                "list": {
                    "results": {
                        "data": [{"id": "c2", "name": "Control 2"}],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                },
                "by_id": {},
            }
        }
    )
    mcp = _mcp()
    fn = _tool_fn(mcp, "vanta_get_controls")

    result = json.loads(await fn(cursor="cursor-1"))

    assert result["has_more"] is False
    assert result["next_cursor"] is None
    _, _, params = fake.calls[0]
    assert params["pageCursor"] == "cursor-1"
