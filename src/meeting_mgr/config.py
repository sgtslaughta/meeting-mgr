from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://postgres:test@localhost:55432/meeting_mgr_test"
    # Least-privilege role RLS policies actually apply to (superuser/owner
    # bypasses RLS by default). Used by get_org_session/get_readonly_org_session.
    database_url_app: str = (
        "postgresql+psycopg://meeting_app:meeting_app@localhost:55432/meeting_mgr_test"
    )
    redis_url: str = "redis://localhost:56379/0"
    s3_endpoint: str = "http://localhost:59000"
    s3_access_key: str = "test"
    s3_secret_key: str = "testtest"
    s3_bucket: str = "recordings"
    asr_base_url: str = "http://localhost:58080/v1"
    asr_api_key: str = "unused"
    asr_model: str = "whisper-1"
    llm_base_url: str = "http://localhost:58080/v1"
    llm_api_key: str = "unused"
    llm_model: str = "local-model"
    diarizer_url: str = "http://localhost:58081"
    # Signs the session cookie. This default is a deliberately unsafe
    # placeholder — production deployments MUST override it (see admin guide).
    session_secret: str = "INSECURE-DEV-SESSION-SECRET-DO-NOT-USE-IN-PRODUCTION"
    # Comma-separated source IPs of trusted mTLS-terminating reverse proxies.
    # Empty (default) means mTLS is not in use: the identity header is always
    # stripped, regardless of source. Public env var name: MTLS_PROXY_ALLOWLIST.
    mtls_proxy_allowlist_raw: str = ""
    # OIDC relying-party config. Defaults are deliberately invalid
    # placeholders — production deployments MUST override them.
    oidc_discovery_url: str = "https://example-idp.invalid/.well-known/openid-configuration"
    oidc_client_id: str = "unset"
    oidc_client_secret: str = "unset"

    @property
    def mtls_proxy_allowlist(self) -> frozenset[str]:
        return frozenset(x.strip() for x in self.mtls_proxy_allowlist_raw.split(",") if x.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
