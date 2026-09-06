import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.routers import reels, videos
from app.services.logger import get_logger

logger = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown hooks. Pings Supabase on boot to fail fast if mis-configured."""
    logger.info("Starting up — validating Supabase connectivity…")
    try:
        # Lazy import so import errors don't kill startup before logging
        from app.services.supabase import supabase

        supabase.table("videos").select("id").limit(1).execute()
        logger.info("Supabase reachable.")
    except Exception as exc:  # noqa: BLE001
        logger.error("Supabase connectivity check FAILED: %s", exc)
        # We don't raise — the app should still start so /health responds and logs show the issue.

    # Pre-warm JWKS cache so the first authenticated request doesn't pay the HTTP fetch.
    try:
        from app.services.jwt_verifier import _get_jwks_client

        _get_jwks_client()
        logger.info("JWKS pre-warmed.")
    except Exception as exc:  # noqa: BLE001
        logger.error("JWKS pre-warm FAILED: %s", exc)

    yield
    logger.info("Shutting down.")


app = FastAPI(
    title="AI Reels Generator API",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — explicit allow-list from env, never "*"
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.frontend_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Worker-Secret"],
    max_age=600,
)

# Routers
app.include_router(videos.router, prefix="/api/videos", tags=["Videos"])
app.include_router(reels.router, prefix="/api/reels", tags=["Reels Generation"])


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "reels-generator-api"}


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Last-resort handler — never leak tracebacks to clients."""
    rid = uuid.uuid4().hex[:12]
    logger.exception("Unhandled error rid=%s path=%s", rid, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error", "request_id": rid},
    )
