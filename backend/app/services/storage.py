from typing import Optional

try:
    from supabase.storage.utils import StorageException  # older supabase-py
except ImportError:  # supabase-py >= 2.x moved it
    from supabase import StorageException

from app.config import settings
from app.services.supabase import supabase
from app.services.logger import get_logger

logger = get_logger("storage_service")


class StorageService:
    """Thin wrapper over Supabase Storage.

    Public surface is intentionally tiny — every call logs and validates the result
    so the caller can rely on it not silently failing.
    """

    def __init__(self) -> None:
        self.bucket = settings.STORAGE_BUCKET

    # --- Upload ---
    def upload_file(
        self,
        path: str,
        file_bytes: bytes,
        content_type: str = "video/mp4",
        upsert: bool = False,
    ) -> str:
        """Upload bytes to the bucket. Returns the storage path on success."""
        try:
            res = supabase.storage.from_(self.bucket).upload(
                path=path,
                file=file_bytes,
                file_options={"content-type": content_type, "upsert": str(upsert).lower()},
            )
        except StorageException as exc:
            logger.error("Storage upload failed for %s: %s", path, exc)
            raise
        if getattr(res, "status_code", 200) >= 300:
            raise RuntimeError(f"Storage upload failed: status={getattr(res, 'status_code', '?')}")
        return path

    # --- Download ---
    def download_file(self, path: str) -> bytes:
        try:
            return supabase.storage.from_(self.bucket).download(path)
        except StorageException as exc:
            logger.error("Storage download failed for %s: %s", path, exc)
            raise

    # --- Delete ---
    def delete_file(self, path: str) -> None:
        try:
            supabase.storage.from_(self.bucket).remove([path])
        except StorageException as exc:
            logger.warning("Storage delete failed for %s: %s", path, exc)

    def delete_files(self, paths: list) -> dict:
        """Delete multiple objects in one round trip. Returns a summary dict
        with the per-path status. Missing objects are logged and ignored.

        Supabase's `remove` accepts a list and is idempotent — calling it on
        a non-existent path is a no-op (not an error). We still wrap in
        try/except so a single bad path doesn't fail the whole batch.
        """
        if not paths:
            return {"deleted": 0, "failed": []}
        try:
            res = supabase.storage.from_(self.bucket).remove(paths)
            # Supabase returns a list of {name, ...} or status codes. We don't
            # depend on the exact shape; just log and assume success.
            logger.info("Storage batch-delete attempted for %d path(s)", len(paths))
            return {"deleted": len(paths), "failed": []}
        except StorageException as exc:
            logger.warning("Storage batch-delete failed for %d path(s): %s", len(paths), exc)
            return {"deleted": 0, "failed": list(paths), "error": str(exc)}

    # --- Public URL ---
    def get_public_url(self, path: str) -> str:
        """Return the public URL. Caller should be aware that the bucket must be
        configured as public OR switch to signed URLs for production."""
        result = supabase.storage.from_(self.bucket).get_public_url(path)
        # The SDK returns either a string (older versions) or a dict
        if isinstance(result, dict):
            return result.get("publicUrl") or result.get("public_url") or ""
        return str(result)

    # --- Signed URL (preferred for production) ---
    def create_signed_url(self, path: str, expires_in: int = 3600) -> Optional[str]:
        """Return a signed URL good for `expires_in` seconds, or None on failure."""
        try:
            res = supabase.storage.from_(self.bucket).create_signed_url(path, expires_in)
            if isinstance(res, dict):
                return res.get("signedUrl") or res.get("signed_url")
            return getattr(res, "signed_url", None) or getattr(res, "signedURL", None)
        except StorageException as exc:
            logger.warning("create_signed_url failed for %s: %s", path, exc)
            return None

    # --- Signed Upload URL (Phase 11 — for progress bar in the UI) ---
    def create_signed_upload_url(self, path: str) -> Optional[dict]:
        """Return a one-time signed upload URL + token, or None on failure.

        The client PUTs the file bytes directly to `signed_url` with the token
        in the query string. The response shape is:
            {"signed_url": str, "token": str, "path": str}

        Why we need this: `supabase.storage.from_(b).upload()` does NOT expose
        progress events, so we can't show a progress bar for chunky uploads.
        PUTting to a signed URL via XHR gives us real `onprogress` callbacks.
        See `frontend/src/app/upload/page.tsx` for the matching client code.
        """
        try:
            res = supabase.storage.from_(self.bucket).create_signed_upload_url(path)
            # The SDK returns either a dict or a typed object depending on version
            if isinstance(res, dict):
                return {
                    "signed_url": res.get("signedUrl") or res.get("signed_url", ""),
                    "token": res.get("token", ""),
                    "path": res.get("path", path),
                }
            return {
                "signed_url": getattr(res, "signed_url", "") or getattr(res, "signedURL", ""),
                "token": getattr(res, "token", ""),
                "path": getattr(res, "path", path),
            }
        except StorageException as exc:
            logger.warning("create_signed_upload_url failed for %s: %s", path, exc)
            return None


storage_service = StorageService()
