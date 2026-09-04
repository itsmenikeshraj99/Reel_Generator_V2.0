"""Supabase JWT verification for FastAPI dependencies.

The backend uses the service_role key for DB queries (which bypasses RLS), so we MUST
verify the caller's identity ourselves on every request. We do this by accepting
`Authorization: Bearer <jwt>` and asking Supabase to validate it via `auth.get_user()`.
"""
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from supabase import Client

from app.services.supabase import supabase


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
        # Supabase validates signature + expiry + audience
        result = supabase.auth.get_user(token)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if not result or not getattr(result, "user", None):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = result.user
    return CurrentUser(id=str(user.id), email=getattr(user, "email", None))


def require_user_match(path_user_id: str, current: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Optional guard — use when a path/body user_id is supplied and must match the token."""
    if path_user_id != current.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return current
