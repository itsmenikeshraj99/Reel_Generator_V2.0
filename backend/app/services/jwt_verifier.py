"""JWT verification for Supabase ES256 tokens.

Supabase Auth issues JWTs signed with ES256 (asymmetric, P-256 curve). We verify
them locally using the public key fetched from Supabase's JWKS endpoint — no
network round-trip per request, thanks to PyJWKClient's in-memory cache.
"""
import time

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError

from app.config import settings


_jwks_client: PyJWKClient | None = None

# One extra attempt if fetching Supabase's public keys fails with a
# connection-level error. Observed intermittently in local dev (root cause
# not fully confirmed — see jwt_verifier fix notes), but every retried
# attempt in testing succeeded immediately, so this does not mask a
# genuinely bad signature or expired token — only a failed key fetch.
_JWKS_FETCH_RETRY_DELAY_SECONDS = 0.5


def _get_jwks_client() -> PyJWKClient:
    """Lazy module-level singleton. cache_keys=True keeps keys for `lifespan` seconds."""
    global _jwks_client
    if _jwks_client is None:
        uri = f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1/.well-known/jwks.json"
        _jwks_client = PyJWKClient(uri, cache_keys=True, lifespan=3600)
    return _jwks_client


def _get_signing_key_with_retry(client: PyJWKClient, token: str):
    """Fetch the signing key, retrying once on a connection-level failure only."""
    try:
        return client.get_signing_key_from_jwt(token).key
    except PyJWKClientConnectionError:
        time.sleep(_JWKS_FETCH_RETRY_DELAY_SECONDS)
        return client.get_signing_key_from_jwt(token).key


def verify_jwt(token: str) -> dict:
    """Verify an ES256 Supabase user JWT. Returns decoded claims or raises jwt.PyJWTError."""
    client = _get_jwks_client()
    signing_key = _get_signing_key_with_retry(client, token)
    return jwt.decode(
        token,
        signing_key,
        algorithms=["ES256"],
        audience="authenticated",
        options={"require": ["exp", "sub", "aud"]},
    )
