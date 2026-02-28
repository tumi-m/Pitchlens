"""
Pitchlens — Python FastAPI Backend
Entry point for the AI analytics engine.
"""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from app.routers import matches

# ── Logging ───────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("pitchlens")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Pitchlens API starting up…")
    yield
    logger.info("Pitchlens API shutting down.")


# ── App factory ───────────────────────────────────────────────────────────
def create_app() -> FastAPI:
    app = FastAPI(
        title="Pitchlens AI Engine",
        description="GPU-accelerated five-a-side soccer analytics API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if os.getenv("ENV") != "production" else None,
        redoc_url="/redoc" if os.getenv("ENV") != "production" else None,
    )

    # ── Middleware ─────────────────────────────────────────────────────────
    allowed_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

    # ── Global exception handler ───────────────────────────────────────────
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.exception(f"Unhandled exception on {request.method} {request.url}: {exc}")
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error. The pitch is temporarily unavailable."},
        )

    # ── Routers ────────────────────────────────────────────────────────────
    app.include_router(matches.router, prefix="/api/v1", tags=["matches"])

    # Root redirect to health
    @app.get("/")
    async def root():
        return {"service": "pitchlens-api", "status": "operational", "version": "0.1.0"}

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8080")),
        workers=1,  # single worker — GPU not fork-safe
        log_level="info",
        reload=os.getenv("ENV") == "development",
    )
