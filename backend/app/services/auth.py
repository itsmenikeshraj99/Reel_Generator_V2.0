"""Supabase JWT verification (ES256 via JWKS) for FastAPI dependencies.

The backend uses the service_role key for DB queries (bypasses RLS), so we MUST
verify the caller's identity ourselves on every request. We do this by accepting
`Authorization: Bearer <jwt>` and verifying the ES256 signature against the
public key fetched from Supabase's JWKS endpoint.
"""
import logging
from dataclasses import dataclass
from typing import Optional

import jwt
from fastapi import Depends, Header, HTTPException, status

from app.services.jwt_verifier import verify_jwt

logger = logging.getLogger("auth")


@dataclass
class CurrentUser:
    id: str
    email: Optional[str] = None


def get_current_user(authorization: Optional[str] = Header(None)) -> CurrentUser:
    """FastAPI dependency. Extracts and verifies the Bearer token.

    Raises 401 if missing/invalid. The returned `CurrentUser` is the only thing
    routers should trust for `user_id` — never read user_id from the request body.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Empty bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        claims = verify_jwt(token)
    except jwt.PyJWTError as exc:
        logger.warning("JWT verification failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    sub = claims.get("sub")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return CurrentUser(id=str(sub), email=claims.get("email"))


def require_user_match(path_user_id: str, current: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Optional guard — use when a path/body user_id is supplied and must match the token."""
    if path_user_id != current.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return current
