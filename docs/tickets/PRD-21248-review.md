# PRD-21248 · Code review

## Round 1 · 2026-11-02 · reviewed 8c9b12e (base f056d60)

Verdict: changes requested

### Findings

1. [must-fix] `src/vanta_mcp/client.py:121` (`VantaClient._raise_for_status`) and `src/vanta_mcp/auth.py:41` (`fetch_access_token`) — no logging anywhere in the service, contradicting the Tech design's own "Observability" commitment and SOP §6/§14.

   The Tech design (`docs/tickets/PRD-21248.md:191`) states: "**Observability**: structured log line on every upstream error (status, Vanta's own `message`, our mapped `code`) — never the credential values." A repo-wide search (`grep -rn "print(\|import logging\|logging\.\|getLogger" src/`) finds exactly one hit: the startup banner `print(...)` in `src/vanta_mcp/__main__.py:42`. There is no logging statement anywhere in `client.py`, `auth.py`, `middleware.py`, or any `tools/*.py` file. Every error path — `_raise_for_status` mapping 401/403/404/422/429/5xx (`client.py:121-136`), `fetch_access_token` mapping 400/401/403/429/5xx (`auth.py:41-92`), and the 401-on-missing-header path in `middleware.py:54-70` — raises/returns a structured error with nothing written to any log sink first.

   This isn't just an unmet "nice to have": SOP §6 ("日志与可观测性") requires structured logs with a request-id recording "工具名、入参摘要（脱敏）、你的 API 路径与状态码、耗时、错误 `code`" for exactly this kind of per-tenant, per-request HTTP service, and the §14 delivery checklist lists it under "日志与敏感信息" (not under the §2 "应当" advisory section). `references/mcp-track.md` §4.3 names "SOP §14 Checklist" explicitly as one of the review's must-fix criteria.

   Failure scenario: a tenant reports that `vanta_get_issues` is returning `rate_limited` or `upstream_error`. Since no test account exists yet and this service never writes a log line on any upstream failure, there is no way for an operator to see which upstream path, status code, or Vanta-returned message caused it without being able to reproduce the failure live against the real Vanta tenant — which may itself not be possible (data-dependent conditions, expired credentials, etc.). The gap is the exact one SOP §6 exists to prevent.

   What would fix it: add a structured log call (stdlib `logging`, JSON or key=value) in `VantaClient._raise_for_status` and in `fetch_access_token`'s error branches, each emitting at minimum the resource/tool name, the upstream path, the upstream status code, and the mapped `code` — never the credential headers or the raw token. A request-id (even a simple per-call `uuid4()` threaded through `client_factory`/`build_client`) would satisfy the "带 request-id" requirement; the design already has a natural per-request boundary (the `ContextVar`-scoped credential tuple) to hang it from.

2. [suggestion] `src/vanta_mcp/client.py:73-119` (`VantaClient.get`) — no size guard or truncation marker on list responses, per SOP §14's "单次返回 ≤ 20,000 字符" item (advisory, §2 "应当" section). Current field curation (`project_fields`) and the default `limit=25`/hard cap `limit=100` make this unlikely to bite in practice, but there's no explicit truncation or warning if a tenant's records are unusually large (e.g. long `description`/`correctiveAction` text on `issues`). Not blocking; worth a one-line note in a fast-follow if real tenant data turns out larger than expected.

### Fixes
(to be filled by the dev leg on rework)
