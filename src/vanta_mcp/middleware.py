import contextvars

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import Settings
from .logging_utils import bind_request_id, log_structured, new_request_id, reset_request_id

# Per-request credential contextvar (SOP §3.3). Set by GatewayCredentialsMiddleware
# before the MCP handler runs and reset in `finally`. asyncio copies the context
# per task, so concurrent requests from different tenants never observe each
# other's value here — this is the mechanism AC14/TC-VAN-014 depends on.
_vanta_credentials_var: contextvars.ContextVar[tuple[str, str] | None] = contextvars.ContextVar(
    "vanta_credentials", default=None
)


def get_vanta_credentials() -> tuple[str, str] | None:
    """Return the current request's (client_id, client_secret), or None."""
    return _vanta_credentials_var.get()


class GatewayCredentialsMiddleware:
    """ASGI middleware: reads the two Vanta OAuth2 headers and stores them in
    a ContextVar.

    Missing either header on an /mcp request -> 401 listing both required
    header names (SOP §3.2). No fallback to an environment variable exists
    anywhere in this service (SOP §3.1) — a request missing a header simply
    has no credential available.
    """

    def __init__(self, app: ASGIApp, settings: Settings):
        self.app = app
        self.settings = settings
        self._id_header = settings.client_id_header.lower()
        self._secret_header = settings.client_secret_header.lower()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not path.startswith("/mcp"):
            await self.app(scope, receive, send)
            return

        # One request-id per request (SOP §6), bound before any handler or
        # downstream HTTP call can run, so every log line this request
        # produces -- here or in client.py/auth.py -- can be correlated.
        request_id_token = bind_request_id(new_request_id())
        try:
            request = Request(scope)
            # Header lookup is case-insensitive in Starlette.
            client_id = request.headers.get(self._id_header)
            client_secret = request.headers.get(self._secret_header)

            if not client_id or not client_secret:
                log_structured(
                    "missing_credentials", tool="-", path=path, status=401, code="unauthorized"
                )
                response = JSONResponse(
                    {
                        "error": "Missing credentials",
                        "message": (
                            f"Missing required header(s): {self.settings.client_id_header}, "
                            f"{self.settings.client_secret_header}"
                        ),
                        "required_headers": [
                            self.settings.client_id_header,
                            self.settings.client_secret_header,
                        ],
                    },
                    status_code=401,
                )
                await response(scope, receive, send)
                return

            creds_token = _vanta_credentials_var.set((client_id, client_secret))
            try:
                await self.app(scope, receive, send)
            finally:
                _vanta_credentials_var.reset(creds_token)
        finally:
            reset_request_id(request_id_token)
