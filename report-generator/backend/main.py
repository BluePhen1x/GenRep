"""
FastAPI app - the web interface for the GenRep report generator.

Routes browser requests to Celery, which drives the real GenRep
multi-agent engine. Also serves the frontend and generated PDFs.
"""

import time
import uuid
from pathlib import Path
from typing import Optional

from celery.result import AsyncResult
from fastapi import Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from auth import auth_magic_link, auth_refresh_token, auth_sign_in, auth_sign_up, get_current_user
from config import config
from models import (
    can_generate,
    get_daily_usage_count,
    get_page_limit,
    increment_usage,
    log_usage,
)
from openmanus_wrapper import wrapper
from rate_limit import get_rate_limiter, parse_rate_limit
from tasks import generate_report
from webhooks import router as webhook_router

app = FastAPI(
    title="Report Generator (Powered by GenRep)",
    description="Multi-agent report generation using the GenRep engine",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Gumroad webhook router
app.include_router(webhook_router)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class GenerateRequest(BaseModel):
    prompt: str
    max_pages: Optional[int] = None


class AuthRequest(BaseModel):
    email: str
    password: str


class MagicLinkRequest(BaseModel):
    email: str


class RefreshRequest(BaseModel):
    refresh_token: str


# ---------------------------------------------------------------------------
# In-memory job store (job_id -> (task_id, created_at))
# ---------------------------------------------------------------------------

job_store: dict[str, tuple[str, float]] = {}

FRONTEND_PATH = Path(__file__).resolve().parent.parent / "frontend"


def _prune_jobs():
    now = time.time()
    stale = [k for k, (_, t) in job_store.items() if now - t > config.JOB_TTL]
    for k in stale:
        job_store.pop(k, None)


# ---------------------------------------------------------------------------
# Routes - Static / Health
# ---------------------------------------------------------------------------

@app.get("/")
async def root():
    """Serve the main page."""
    index_path = FRONTEND_PATH / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return HTMLResponse(
        "<h1>Report Generator</h1><p>Frontend not built. Run "
        "`uvicorn main:app --reload` from the backend folder.</p>"
    )


@app.get("/health")
async def health():
    """Health check."""
    return {
        "status": "ok",
        "openmanus": wrapper.get_status(),
        "config": {
            "temp_dir": str(config.TEMP_DIR),
            "redis_url": config.REDIS_URL,
            "openmanus_path": str(config.OPENMANUS_PATH),
        },
    }


@app.get("/openmanus-status")
async def openmanus_status():
    """Get detailed GenRep status."""
    return wrapper.get_status()


@app.get("/api/config")
async def get_public_config():
    """Return public config needed by the frontend (no secrets)."""
    return {
        "supabase_url": config.SUPABASE_URL,
        "supabase_anon_key": config.SUPABASE_ANON_KEY,
        "gumroad_checkout_url": config.GUMROAD_CHECKOUT_URL,
    }


# ---------------------------------------------------------------------------
# Routes - Report Generation (with tier enforcement)
# ---------------------------------------------------------------------------

@app.post("/generate")
async def generate(request: GenerateRequest, user: dict = Depends(get_current_user)):
    """Start report generation using the GenRep multi-agent system."""
    _prune_jobs()

    if not request.prompt or len(request.prompt.strip()) < 10:
        return JSONResponse(
            status_code=400,
            content={"error": "Prompt must be at least 10 characters"},
        )

    profile = user["profile"]

    # --- Rate limit check ---
    limiter = get_rate_limiter()
    if user["type"] == "anonymous":
        limit_spec = config.RATE_LIMIT_ANONYMOUS_GENERATE
        rate_key = f"ratelimit:anon:{user['anonymous_id']}:generate"
    else:
        limit_spec = config.RATE_LIMIT_GENERATE
        rate_key = f"ratelimit:user:{user['auth_user_id']}:generate"

    limit_count, limit_window = parse_rate_limit(limit_spec)
    if not limiter.is_allowed(rate_key, limit_count, limit_window):
        return JSONResponse(
            status_code=429,
            content={
                "error": "Rate limit exceeded. Please wait before generating another report.",
                "tier": profile["tier"],
                "retry_after_seconds": limit_window,
            },
        )

    # --- Usage limit check ---
    if not can_generate(profile):
        return JSONResponse(
            status_code=403,
            content={
                "error": "Report limit reached for your current tier.",
                "tier": profile["tier"],
                "reports_used": profile["reports_used"],
                "reports_limit": profile["reports_limit"],
                "upgrade_url": config.GUMROAD_CHECKOUT_URL,
            },
        )

    # --- Page limit enforcement ---
    tier_page_limit = get_page_limit(profile["tier"])
    page_limit = tier_page_limit
    if request.max_pages is not None:
        page_limit = min(request.max_pages, tier_page_limit)

    # --- Dispatch to Celery ---
    job_id = str(uuid.uuid4())

    if not config.SKIP_OPENMANUS_CHECK:
        status = wrapper.get_status()
        if status["status"] != "ready":
            return JSONResponse(
                status_code=503,
                content={"error": f"GenRep not ready: {status['message']}"},
            )
    else:
        status = {"status": "skipped (SKIP_OPENMANUS_CHECK=true)"}

    task = generate_report.delay(request.prompt, job_id, page_limit=page_limit)
    job_store[job_id] = (task.id, time.time())

    # --- Increment usage ---
    increment_usage(profile["id"])
    log_usage(
        user_id=user.get("auth_user_id"),
        anonymous_id=user.get("anonymous_id"),
    )

    return {
        "job_id": job_id,
        "task_id": task.id,
        "message": "Report generation started",
        "status_url": f"/status/{job_id}",
        "openmanus_status": status,
        "tier": profile["tier"],
        "page_limit": page_limit,
    }


@app.get("/status/{job_id}")
async def get_status(job_id: str):
    """Get task status."""
    entry = job_store.get(job_id)
    if not entry:
        return JSONResponse(status_code=404, content={"error": "Job not found"})

    task_id, _ = entry
    task_result = AsyncResult(task_id)

    if task_result.state == "PENDING":
        return {
            "status": "pending",
            "progress": 0,
            "message": "Waiting for GenRep to start...",
        }
    if task_result.state == "FAILURE":
        info = task_result.info
        if isinstance(info, dict):
            err = str(info.get("error") or task_result.result or "Unknown error")
        else:
            err = str(info) or str(task_result.result) or "Unknown error"
        return {
            "status": "failed",
            "progress": 0,
            "message": f"Error: {err}",
            "error": err,
        }
    if task_result.state == "SUCCESS":
        result = task_result.result or {}
        return {
            "status": "complete",
            "progress": 100,
            "message": "Report complete! Download your PDF.",
            "download_url": result.get("download_url", ""),
            "result": result.get("result", ""),
        }

    # Running state - read progress from the task meta
    info = task_result.info or {}
    return {
        "status": task_result.state.lower(),
        "progress": info.get("progress", 0),
        "message": info.get("message", "Processing..."),
    }


@app.get("/download/{job_id}")
async def download(job_id: str):
    """Download the generated report PDF."""
    entry = job_store.get(job_id)
    if not entry:
        return JSONResponse(status_code=404, content={"error": "Job not found"})

    task_id, _ = entry
    task_result = AsyncResult(task_id)
    if task_result.state != "SUCCESS":
        return JSONResponse(
            status_code=404, content={"error": "Report not ready yet"}
        )

    pdf_path = config.TEMP_DIR / f"report_{job_id}.pdf"
    if pdf_path.exists():
        return FileResponse(
            pdf_path,
            media_type="application/pdf",
            filename=f"report_{job_id}.pdf",
        )

    return JSONResponse(status_code=404, content={"error": "PDF file not found"})


# ---------------------------------------------------------------------------
# Routes - Auth
# ---------------------------------------------------------------------------

@app.post("/api/auth/signup")
async def signup(request: AuthRequest):
    """Create a new account."""
    try:
        result = auth_sign_up(request.email, request.password)
        if not result.get("user"):
            return JSONResponse(status_code=400, content={"error": "Signup failed"})
        return {
            "user": result["user"],
            "access_token": result["session"]["access_token"],
            "refresh_token": result["session"]["refresh_token"],
        }
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=400, content={"error": str(e)})


@app.post("/api/auth/signin")
async def signin(request: AuthRequest):
    """Sign in with email and password."""
    try:
        result = auth_sign_in(request.email, request.password)
        if not result.get("user"):
            return JSONResponse(status_code=401, content={"error": "Invalid credentials"})
        return {
            "user": result["user"],
            "access_token": result["session"]["access_token"],
            "refresh_token": result["session"]["refresh_token"],
        }
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=401, content={"error": str(e)})


@app.post("/api/auth/magic-link")
async def magic_link(request: MagicLinkRequest):
    """Send a magic login link."""
    try:
        result = auth_magic_link(request.email)
        return result
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=400, content={"error": str(e)})


@app.post("/api/auth/refresh")
async def refresh_token(request: RefreshRequest):
    """Refresh an access token."""
    try:
        result = auth_refresh_token(request.refresh_token)
        if not result.get("session"):
            return JSONResponse(status_code=401, content={"error": "Refresh failed"})
        return {
            "user": result.get("user"),
            "access_token": result["session"]["access_token"],
            "refresh_token": result["session"]["refresh_token"],
        }
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=401, content={"error": str(e)})


@app.get("/api/auth/session")
async def get_session(user: dict = Depends(get_current_user)):
    """Get current user session info."""
    profile = user["profile"]
    return {
        "type": user["type"],
        "tier": profile["tier"],
        "reports_used": profile["reports_used"],
        "reports_limit": profile["reports_limit"],
        "page_limit": get_page_limit(profile["tier"]),
        "email": profile.get("email"),
        "daily_usage": get_daily_usage_count(profile["id"]),
    }


@app.post("/api/auth/signout")
async def signout(response: Response):
    """Sign out (clear anonymous cookie)."""
    response.delete_cookie(key="genrep_anon_id")
    return {"message": "Signed out"}


# ---------------------------------------------------------------------------
# Static assets
# ---------------------------------------------------------------------------

_static_dir = FRONTEND_PATH
if _static_dir.exists():
    app.mount("/assets", StaticFiles(directory=_static_dir), name="assets")
