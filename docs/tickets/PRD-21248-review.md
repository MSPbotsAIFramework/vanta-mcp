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

Fixed in `42625e2` (code) + `04be08e` (regression tests), synced with `main @ f056d60`
(unchanged since round 1 -- no re-merge needed).

1. [must-fix, `client.py:121` / `auth.py:41`] Added `src/vanta_mcp/logging_utils.py`: a
   per-request id (`ContextVar`, bound by `GatewayCredentialsMiddleware` before any
   downstream call so client/auth logging can be correlated with it) and
   `log_structured(event, **fields)`, which emits one compact JSON line via stdlib
   `logging` (`logger = logging.getLogger("vanta_mcp")`) carrying only the tool/resource
   name, the upstream path, the HTTP status, and our mapped `code` -- never a credential
   header value, the bearer token, or a response body.

   - `client.py`: `_raise_for_status` now takes `path` and logs once (refactored the
     status-code `if/elif` chain to compute `(code, message)` first, so the log call
     isn't duplicated per branch -- same shape, same error codes/messages as before, just
     DRY'd). The two other raise sites inside `get()` (connection failure after retries
     exhausted, non-JSON response body) log too, since those are also upstream-error
     paths with no prior log line.
   - `auth.py`: `fetch_access_token`'s six error branches (network error, 429-after-retry,
     400/401/403, 5xx, other 4xx, invalid JSON, missing `access_token`) now go through one
     `_log_and_raise(token_url, status, code, message)` helper instead of repeating the
     log call inline.
   - `middleware.py`: the 401-on-missing-header path named in the finding now logs too
     (`event=missing_credentials`), and a request id is bound for the life of every
     `/mcp` request before the credentials check runs, so a request that fails there still
     gets a correlatable id.

   Verified against the real Vanta OAuth endpoint (no mocks, placeholder credentials):
   `tools/call vanta_get_controls` with bogus `X-Vanta-Client-Id`/`X-Vanta-Client-Secret`
   produced `api.vanta.com/oauth/token` returning `401`, and the service logged
   `{"event":"upstream_error","request_id":"b124f9904275","tool":"oauth_token","path":"https://api.vanta.com/oauth/token","status":401,"code":"unauthorized"}`
   to stderr -- no credential value or token in the line. Also verified the
   `missing_credentials` line on a request with no headers at all (bare process and the
   built container, see below).

   Added `tests/test_logging.py` (TC-VAN-016..018 in `.ac.json`, `"source": "dev"` since
   they weren't part of the original AC set): one per named error path
   (`_raise_for_status`, `fetch_access_token`, the middleware's missing-header 401), each
   asserting both that a structured log line was emitted *and* that the secret value used
   in that call (bearer token / client secret) is absent from the rendered log text --
   a test that only checked "logging was called" would not catch a leak. Confirmed red
   against the pre-fix (round 1, `c207f65`) `client.py`/`auth.py`/`middleware.py` (swapped
   in via `git show c207f65:<path>`, all 3 new tests fail), green again after restoring
   the fix, with no other test's behavior changed.

2. [suggestion, `client.py:73-119`] Not addressed in this round -- it was filed as
   non-blocking (no size guard on `VantaClient.get`'s list responses). Current field
   curation and the `limit<=100` hard cap keep responses well under SOP §14's 20,000-char
   guidance in practice; leaving it as a fast-follow candidate per the original note
   rather than adding speculative truncation logic nothing has asked for.

#### Re-run gates (rework, 2026-11-02)

| Gate | Command | Result |
|---|---|---|
| Unit tests | `uv run pytest -q` | 23 passed, 0 failed (the original 20 unchanged + 3 new regression tests for this fix, TC-VAN-016..018) |
| Lint | `uv run ruff check .` | All checks passed |
| Chinese-text scan | `grep -rnP '[\x{4e00}-\x{9fff}]' --exclude-dir=.git --exclude-dir=.venv .` | No hits in any file this round touched (`src/vanta_mcp/{auth,client,middleware,logging_utils}.py`); pre-existing Chinese-language SOP quotes inside this review doc's own round-1 finding text are untouched content from round 1, not introduced by this fix |
| Container build | `docker build --platform linux/amd64 -t vanta-mcp:prd21248-rework .` | Built successfully, `linux/amd64` confirmed via `docker inspect` |
| Container smoke test | `docker run`, then `curl http://localhost:18081/health`, `POST /mcp` with no headers, `POST /mcp tools/list` with placeholder headers | `/health` -> `200 {"status":"ok"}`; no-headers -> `401` with the structured `missing_credentials` log line; `tools/list` -> `200` with the same 7-tool list as round 1 |
| Bare-process smoke test | `uv run python -m vanta_mcp`, same three requests | Same results as the container; additionally called `tools/call vanta_get_controls` with placeholder credentials to force a real upstream 401 from `api.vanta.com/oauth/token` and confirm the new `upstream_error` log line (see above) |
| Secret scan | `gitleaks` | **Not run this round** -- `gitleaks` binary is not installed in this sandbox (round 1 ran it on a different host). Manually checked `git diff` for this commit against common token/secret patterns (`ghs_`, `ghp_`, `glpat-`, `AKIA`, inline `secret=...` literals) -- no hits. The new code never stores or logs a credential value (that is the fix's own invariant), so there is nothing for a secret scanner to newly flag in it. |

No regressions: the original 20 unit tests are unchanged and still pass, same 7-tool list,
same error-mapping behavior and messages -- only a log line was added ahead of each
existing raise. Cases TC-VAN-001 through TC-VAN-014 are unaffected (none of them assert on
logging); TC-VAN-015 remains blocked on a real Vanta account, unchanged from round 1;
TC-VAN-016..018 are new (this fix's own regression coverage).

