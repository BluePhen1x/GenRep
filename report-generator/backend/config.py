"""
Configuration - loads environment variables.

The LLM API keys live in OpenManus/config/config.toml, NOT here!
This only configures the web wrapper around the GenRep engine.
"""

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

# Load .env from the project root (report-generator/.env)
if load_dotenv is not None:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")


class Config:
    # OpenManus path (where the REAL multi-agent engine lives).
    # report-generator/ lives INSIDE the OpenManus repo, so the default
    # is the repo root itself. Override via OPENMANUS_PATH if needed.
    OPENMANUS_PATH = os.getenv(
        "OPENMANUS_PATH", str(Path(__file__).resolve().parent.parent.parent)
    )
    OPENMANUS_PATH = Path(OPENMANUS_PATH).resolve()

    # Redis for Celery (broker + result backend)
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # When Redis is unreachable (e.g. plain Windows installs without Redis),
    # Celery falls back to the built-in filesystem transport so the whole
    # system still works out of the box.
    ALLOW_BROKER_FALLBACK = os.getenv("ALLOW_BROKER_FALLBACK", "true").lower() in (
        "1",
        "true",
        "yes",
    )

    # Temporary file storage (downloaded PDFs land here)
    # MUST be outside OneDrive — file sync breaks Celery's filesystem transport
    TEMP_DIR = Path(os.getenv("REPORT_TEMP_DIR", str(Path.home() / "AppData" / "Local" / "GenRep" / "temp")))
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    # Report settings
    MAX_REPORT_PAGES = 60
    DEFAULT_SECTIONS = 8
    PDF_TITLE = "GenRep Generated Report"
    PDF_AUTHOR = "GenRep Multi-Agent System"

    # Celery
    CELERY_BROKER_URL = REDIS_URL
    CELERY_RESULT_BACKEND = REDIS_URL

    # Set to true to bypass the pre-flight GenRep readiness check in
    # /generate so the Celery pipeline can be tested without a fully
    # configured agent (Gemini key, Playwright, etc.).
    SKIP_OPENMANUS_CHECK = os.getenv("SKIP_OPENMANUS_CHECK", "false").lower() in (
        "1",
        "true",
        "yes",
    )

    # Timeouts (GenRep agent loop can legitimately run for minutes)
    AGENT_TIMEOUT = 1800  # 30 minutes hard limit
    RESEARCH_TIMEOUT = 300  # 5 minutes per research step

    # Frontend
    POLLING_INTERVAL = 2  # seconds

    # Keep in-memory job mapping fresh (seconds)
    JOB_TTL = 3600

    # ---- Supabase ----
    SUPABASE_URL = os.getenv("SUPABASE_URL", "")
    SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

    # ---- Gumroad ----
    GUMROAD_PRODUCT_ID = os.getenv("GUMROAD_PRODUCT_ID", "")
    GUMROAD_WEBHOOK_SECRET = os.getenv("GUMROAD_WEBHOOK_SECRET", "")
    GUMROAD_CHECKOUT_URL = os.getenv("GUMROAD_CHECKOUT_URL", "")

    # ---- Rate Limiting ----
    RATE_LIMIT_GENERATE = os.getenv("RATE_LIMIT_GENERATE", "5/minute")
    RATE_LIMIT_ANONYMOUS_GENERATE = os.getenv("RATE_LIMIT_ANONYMOUS_GENERATE", "1/hour")

    # ---- Cookie Signing ----
    COOKIE_SECRET = os.getenv("COOKIE_SECRET", "change-me-in-production-" + os.urandom(16).hex())

    # ---- CORS ----
    ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")


config = Config()