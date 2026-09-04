import logging
from typing import Optional

try:
    from supabase.storage.utils import StorageException
except ImportError:
    from supabase import StorageException

from worker.config import settings
from worker.services.supabase import supabase_client

logger = logging.getLogger("worker_storage")


class WorkerStorageService:
    def __init__(self) -> None:
        self.bucket = settings.STORAGE_BUCKET

    def download_file(self, path: str) -> bytes:
        try:
            return supabase_client.storage.from_(self.bucket).download(path)
        except StorageException as exc:
            logger.error("Worker storage download failed for %s: %s", path, exc)
            raise

    def upload_file(
        self,
        path: str,
        file_bytes: bytes,
        content_type: str = "video/mp4",
        upsert: bool = True,
    ) -> str:
        try:
            res = supabase_client.storage.from_(self.bucket).upload(
                path=path,
                file=file_bytes,
                file_options={"content-type": content_type, "upsert": str(upsert).lower()},
            )
        except StorageException as exc:
            logger.error("Worker storage upload failed for %s: %s", path, exc)
            raise
        if getattr(res, "status_code", 200) >= 300:
            raise RuntimeError(f"Worker storage upload non-2xx: {getattr(res, 'status_code', '?')}")
        return path

    def delete_file(self, path: str) -> None:
        try:
            supabase_client.storage.from_(self.bucket).remove([path])
        except StorageException as exc:
            logger.warning("Worker storage delete failed for %s: %s", path, exc)


worker_storage = WorkerStorageService()
