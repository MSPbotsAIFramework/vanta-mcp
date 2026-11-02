# vanta-mcp

Vanta security/compliance MCP server for Claude — exposes a tenant's live Vanta
posture (security controls, control test results, compliance documents, vendor
risk, personnel including offboarding status, monitored-device compliance, and
open issues) as read-only MCP tools, calling Vanta's own "Manage Vanta API".

**Tech stack:** Python 3.12 + uv + the official `mcp` SDK's FastMCP (Starlette/uvicorn)

Related requirement: PRD-21248 ([Security] Vanta). See
`docs/tickets/PRD-21248.md` for the full product and technical design.

**Out of scope (v1):** any write operation (changing a control's owner, uploading a
document, closing an issue), Vanta Gov tenants, and webhooks/alerting — see
`docs/tickets/PRD-21248.md` § Non-goals and § Decisions made by DEV01 for why each
is deferred rather than refused.

<div align="center">

## MSPbots Connector

**The MCP Gateway built for MSPs: visible, controlled, and your data stays where it belongs.**

[**Get started at connect.mspbots.ai →**](https://connect.mspbots.ai)

</div>

---

MSPbots Connector gives your AI clients one MCP URL to reach your entire MSP stack (RMM, PSA, documentation, security, finance), and gives you control over who can call which tool and a record of everything that was called.

**Hosted service: [connect.mspbots.ai](https://connect.mspbots.ai)** · Free to use.

### Why Connector

![MSPbots Connector](https://stgoss.mspbots.ai/files/mcp/mspbots-mcp.png)

| | |
|---|---|
| **Visible** | Every tool call is logged: who, which vendor, when. Usage dashboard, CSV export, SIEM streaming. |
| **Controlled** | Role-based, per-tool allowlist. Deny by default. |
| **Protected** | Credentials encrypted per record. AI clients never see your vendor passwords or tokens. |
| **MSP-native** | Built for the MSP stack only: RMM, PSA, documentation, security, finance. |

### Quick start

1. **Sign up** at [connect.mspbots.ai](https://connect.mspbots.ai).
2. **Connect your vendors.** Pick from 150+ connectors and authorize each one on the vendor's own page, or bring your own API keys. No extra vendor accounts needed.
3. **Set access.** Create roles and decide which tools each role may call. Nothing is allowed until you allow it.
4. **Add your MCP URL to your AI client.** Copy the URL from your Connector dashboard into Claude, ChatGPT, Cursor, VS Code, Gemini, Codex, or any MCP-capable client.
5. **Ask your AI to do real work**, and watch every call appear in the audit log.

### Features

**Unified gateway**
- One MCP URL for all your AI clients
- 150+ connectors across the MSP stack
- Custom MCP servers supported, free
- Authorization happens on each vendor's own page; the AI never sees passwords or tokens
- Bring your own credentials: use the vendor API keys you already have
- Multiple accounts per app; connect once, reuse across AI clients
- Tool search: finds the right action for the task
- One sign-in; vendor credentials are injected behind the scenes and OAuth tokens refresh automatically

**Visibility**
- Audit log for every tool call (who, which vendor, when)
- Usage dashboard: call volume, top vendors, error rate, per-user activity
- Export audit logs to CSV, or stream to Loki, Graylog, or LogScale (SIEM)
- Optional recording of tool arguments and result summaries (off by default, enabled per organization)

**Control**
- Per-role, per-tool allowlist, deny by default
- owner / admin / member roles
- SCIM provisioning with Entra ID, Okta, JumpCloud, and Google Workspace

**Security**
- Credentials encrypted one record at a time (AES-256-GCM, per-record key)
- AI clients authenticate with OAuth 2.1 + PKCE
- Vendor responses are scanned for prompt-injection patterns and returned as structured data. This is one layer of defense, not a guarantee against every attack.
- Static egress IP for customer firewall and API allowlisting

### Pricing

**Free.**

Links: [connect.mspbots.ai](https://connect.mspbots.ai) · [mspbots.ai](https://mspbots.ai)

---

## Authentication headers

This service is **stateless**: it never stores, caches, or logs a tenant's Vanta
credentials. Every request must carry two headers — Vanta's "Manage Vanta API" uses
OAuth2 `client_credentials`, not a single static API key:

| Header | Type | Required | Description | Example |
|---|---|---|---|---|
| `X-Vanta-Client-Id` | string | Yes | OAuth2 client id of a Vanta API application registered in the tenant's Vanta account | `client_abc123` |
| `X-Vanta-Client-Secret` | password | Yes | OAuth2 client secret paired with the above client id | `secret_xyz789` |

Missing either header on a `/mcp` request returns `401` with a `required_headers`
list, and the service never calls Vanta. There is no environment-variable
fallback anywhere in this service (SOP §3.1). This service exchanges the pair for
a short-lived Bearer access token on **every** tool call (never cached across
calls, per the platform's request-isolation rule) — see § Known Limitations for
the resulting rate-limit tradeoff.

**Get a client id/secret:** as the tenant's Vanta admin, go to Settings → API
→ create an application with scope `vanta-api.all:read`, which issues a client id
and client secret. See
[Vanta's Manage Vanta API docs](https://developer.vanta.com/) for the full
walkthrough.

## Quick Start

```bash
cd vanta-mcp
uv sync
MCP_HTTP_PORT=8080 uv run vanta-mcp
curl http://localhost:8080/health
```

This service only runs as an HTTP server (`POST /mcp`, Streamable HTTP) — there is no
stdio transport and no single-tenant "env mode"; every tool call carries its own
credential headers (see Authentication headers), so there is nothing a shared
environment variable could usefully hold.

## Tool List

| Tool | Description | Parameters |
|---|---|---|
| `vanta_get_controls` | List or get security controls | `id` (string, optional), `framework` (string, optional), `limit` (int, optional, default 25, hard cap 100), `cursor` (string, optional) |
| `vanta_get_tests` | List or get automated control test results | `id`, `status`, `framework`, `control_id`, `owner_id`, `category` (all optional), `limit`, `cursor` |
| `vanta_get_documents` | List or get compliance documents (policies/evidence) | `id`, `framework`, `status` (all optional), `limit`, `cursor` |
| `vanta_get_vendors` | List or get third-party vendor risk records | `id`, `name`, `status` (all optional), `limit`, `cursor` |
| `vanta_get_people` | List or get personnel records, including offboarding status | `id`, `employment_status` (enum `UPCOMING\|CURRENT\|ON_LEAVE\|INACTIVE\|FORMER`), `email_or_name` (all optional), `limit`, `cursor` |
| `vanta_get_monitored_computers` | List or get monitored-device compliance records | `id`, `compliance_status` (all optional), `limit`, `cursor` |
| `vanta_get_issues` | List or get open compliance issues/findings | `id`, `search`, `status`, `severity`, `source`, `owner_id`, `created_after`/`created_before`/`due_after`/`due_before`, `order_by`, `order_direction` (all optional), `limit`, `cursor` |

All seven tools are **read-only**; there is no write/delete tool in v1 (see Known
Limitations). Each tool lists when called with no `id`, or fetches a single record
when called with `id`; `limit` defaults to 25 and is always clamped to Vanta's own
ceiling of 100.

## Test Examples

### tools/list
```bash
curl -X POST http://localhost:8080/mcp \
  -H "X-Vanta-Client-Id: <client id>" \
  -H "X-Vanta-Client-Secret: <client secret>" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","method":"tools/list","id":1}'
```

### tools/call — list tests needing attention
```bash
curl -X POST http://localhost:8080/mcp \
  -H "X-Vanta-Client-Id: <client id>" \
  -H "X-Vanta-Client-Secret: <client secret>" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{
    "jsonrpc": "2.0",
    "method": "tools/call",
    "id": 2,
    "params": {
      "name": "vanta_get_tests",
      "arguments": {"status": "NEEDS_ATTENTION"}
    }
  }'
```

### tools/call — offboarding check (former personnel)
```bash
curl -X POST http://localhost:8080/mcp \
  -H "X-Vanta-Client-Id: <client id>" \
  -H "X-Vanta-Client-Secret: <client secret>" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{
    "jsonrpc": "2.0",
    "method": "tools/call",
    "id": 3,
    "params": {
      "name": "vanta_get_people",
      "arguments": {"employment_status": "FORMER"}
    }
  }'
```

### Missing headers → 401
```bash
curl -i -X POST http://localhost:8080/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","method":"tools/list","id":1}'
# HTTP/1.1 401 Unauthorized
# {"error":"Missing credentials","message":"Missing required header(s): X-Vanta-Client-Id, X-Vanta-Client-Secret","required_headers":["X-Vanta-Client-Id","X-Vanta-Client-Secret"]}
```

## API Reference

- [Vanta developer documentation / Manage Vanta API](https://developer.vanta.com/) — this
  service is a clean-room implementation against the vendor's own public docs, not a
  fork or port of any Vanta-published library.

## Known Limitations

- **No write tools in v1** — the ticket asked for "live access", not changes; see
  `docs/tickets/PRD-21248.md` § Decisions made by DEV01 for the reasoning and how to
  request a write capability as a fast follow.
- **No Vanta Gov support** — only `api.vanta.com` (commercial tenants) is supported; no
  MSPbots tenant on Vanta Gov is known (see § Decisions made by DEV01, OQ2).
- **Token-endpoint rate limit (5 req/min, shared across all of this OAuth client's API
  traffic)** — because access tokens are never cached across requests (the platform's
  per-request isolation rule), a tenant driving more than ~5 tool calls/minute on the
  same Vanta client credentials will start seeing `rate_limited` errors from this
  service. This is a vendor-imposed ceiling, not something this connector's design can
  raise; see `docs/tickets/PRD-21248.md` § Non-functional.
- **No test Vanta account available at build time** — each tool's live happy path
  against real tenant data, and a real 404/429 round trip, are marked "needs a real
  vendor account" in the self-test report rather than executed.
