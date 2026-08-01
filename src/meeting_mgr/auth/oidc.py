"""authlib OIDC relying-party client. Meeting-MGR is never an identity
provider — see design spec §6."""

from authlib.integrations.starlette_client import OAuth

from meeting_mgr.config import get_settings


def build_oauth() -> OAuth:
    oauth = OAuth()
    settings = get_settings()
    oauth.register(
        name="oidc",
        server_metadata_url=settings.oidc_discovery_url,
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret,
        client_kwargs={"scope": "openid email"},
    )
    return oauth
