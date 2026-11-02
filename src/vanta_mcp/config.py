from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Non-credential configuration only.

    Credentials are never read from environment variables or config files —
    they arrive per request in the X-Vanta-Client-Id / X-Vanta-Client-Secret
    headers (see middleware.py). `extra="ignore"` is required so the runtime
    environment can inject unrelated variables without crashing the service
    (SOP §1.2).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    mcp_http_host: str = "0.0.0.0"
    mcp_http_port: int = 8080

    # Commercial Vanta only (api.vanta.com). Vanta Gov (api.vanta-gov.com) is
    # out of scope for v1 — see docs/tickets/PRD-21248.md § OQ2. Not exposed
    # as a per-request header: it is a deployment-wide choice, not a
    # per-tenant credential.
    vanta_base_url: str = "https://api.vanta.com"
    vanta_token_url: str = "https://api.vanta.com/oauth/token"

    # Hardcoded for v1 — no write tool exists to use a write scope with (see
    # Tech design § Option analysis: Scope).
    vanta_oauth_scope: str = "vanta-api.all:read"

    # Header names carrying the tenant's Vanta OAuth2 client credentials.
    client_id_header: str = "X-Vanta-Client-Id"
    client_secret_header: str = "X-Vanta-Client-Secret"


def get_settings() -> Settings:
    return Settings()
