"""JWT verification for Supabase ES256 tokens.

Supabase Auth issues JWTs signed with ES256 (asymmetric, P-256 curve). We verify
them locally using the public key fetched from Supabase's JWKS endpoint — no
network round-trip per request, thanks to PyJWKClient's in-memory cache.
"""
import jwt
from jwt import PyJWKClient

from app.config import settings


_jwks_client: PyJWKClient | None = None


def _get_jwks_client() -> PyJWKClient:
    """Lazy module-level singleton. cache_keys=True keeps keys for `lifespan` seconds."""
    global _jwks_client
    if _jwks_client is None:
        uri = f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1/.well-known/jwks.json"
        _jwks_client = PyJWKClient(uri, cache_keys=True, lifespan=3600)
    return _jwks_client


def verify_jwt(token: str) -> dict:
    """Verify an ES256 Supabase user JWT. Returns decoded claims or raises jwt.PyJWTError."""
    client = _get_jwks_client()
    signing_key = client.get_signing_key_from_jwt(token).key
    return jwt.decode(
        token,
        signing_key,
        algorithms=["ES256"],
        audience="authenticated",
        options={"require": ["exp", "sub", "aud"]},
    )
