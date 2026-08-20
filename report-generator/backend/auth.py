"""
Authentication helpers — Supabase auth + anonymous cookie management.

Provides:
- JWT verification for authenticated users
- Signed httpOnly cookie for anonymous users
- Unified get_current_user() that returns either type
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from supabase import Client, create_client

from config import config

_supabase_client: Optional[Client] = None


def get_supabase() -> Client:
    """Return a singleton Supabase client using the service-role key."""
    global _supabase_client
    if _supabase_client is None:
        _supabase_client = create_client(
            config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY
        )
    return _supabase_client


# ---------------------------------------------------------------------------
# Cookie helpers
# ---------------------------------------------------------------------------

def _signer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(config.COOKIE_SECRET)


ANONYMOUS_COOKIE = "genrep_anon_id"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 1 year


def sign_anonymous_cookie(anonymous_id: str) -> str:
    return _signer().dumps(anonymous_id)


def verify_anonymous_cookie(value: str) -> Optional[str]:
    try:
        return _signer().loads(value, max_age=COOKIE_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


# ---------------------------------------------------------------------------
# JWT verification
# ---------------------------------------------------------------------------

def verify_jwt(token: str) -> Optional[dict]:
    """Verify a Supabase access token and return the user dict, or None."""
    try:
        sb = get_supabase()
        response = sb.auth.get_user(token)
        if response and response.user:
            return {
                "id": response.user.id,
                "email": response.user.email,
            }
    except Exception:  # noqa: BLE001
        pass
    return None


# ---------------------------------------------------------------------------
# Unified user resolution
# ---------------------------------------------------------------------------

async def get_current_user(request: Request, response: Response) -> dict:
    """
    Resolve the current user from the request.

    Returns a dict with:
      type: "authenticated" | "anonymous"
      auth_user_id: str | None
      anonymous_id: str | None
      profile: dict (from user_profiles table)
    """
    # 1. Try Authorization header (Bearer token)
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        user = verify_jwt(token)
        if user:
            from models import get_or_create_profile_by_auth
            profile = get_or_create_profile_by_auth(user["id"], user.get("email"))
            return {
                "type": "authenticated",
                "auth_user_id": user["id"],
                "anonymous_id": None,
                "profile": profile,
            }

    # 2. Try anonymous cookie
    cookie_value = request.cookies.get(ANONYMOUS_COOKIE)
    if cookie_value:
        anon_id = verify_anonymous_cookie(cookie_value)
        if anon_id:
            from models import get_or_create_profile_by_anonymous
            profile = get_or_create_profile_by_anonymous(anon_id)
            return {
                "type": "anonymous",
                "auth_user_id": None,
                "anonymous_id": anon_id,
                "profile": profile,
            }

    # 3. New anonymous user — create UUID, set signed cookie
    anon_id = str(uuid.uuid4())
    signed = sign_anonymous_cookie(anon_id)
    response.set_cookie(
        key=ANONYMOUS_COOKIE,
        value=signed,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        secure=False,  # Set True in production with HTTPS
        samesite="lax",
    )
    from models import get_or_create_profile_by_anonymous
    profile = get_or_create_profile_by_anonymous(anon_id)
    return {
        "type": "anonymous",
        "auth_user_id": None,
        "anonymous_id": anon_id,
        "profile": profile,
    }


# ---------------------------------------------------------------------------
# Auth actions (called from API routes)
# ---------------------------------------------------------------------------

def auth_sign_up(email: str, password: str) -> dict:
    """Sign up a new user via Supabase Auth."""
    sb = get_supabase()
    result = sb.auth.sign_up({"email": email, "password": password})
    return {
        "user": {"id": result.user.id, "email": result.user.email} if result.user else None,
        "session": {
            "access_token": result.session.access_token,
            "refresh_token": result.session.refresh_token,
        } if result.session else None,
    }


def auth_sign_in(email: str, password: str) -> dict:
    """Sign in via Supabase Auth."""
    sb = get_supabase()
    result = sb.auth.sign_in_with_password({"email": email, "password": password})
    return {
        "user": {"id": result.user.id, "email": result.user.email} if result.user else None,
        "session": {
            "access_token": result.session.access_token,
            "refresh_token": result.session.refresh_token,
        } if result.session else None,
    }


def auth_magic_link(email: str) -> dict:
    """Send a magic link via Supabase Auth."""
    sb = get_supabase()
    sb.auth.sign_in_with_otp({"email": email})
    return {"message": "Check your email for the login link"}


def auth_refresh_token(refresh_token: str) -> dict:
    """Refresh an access token."""
    sb = get_supabase()
    result = sb.auth.refresh_session(refresh_token)
    return {
        "user": {"id": result.user.id, "email": result.user.email} if result.user else None,
        "session": {
            "access_token": result.session.access_token,
            "refresh_token": result.session.refresh_token,
        } if result.session else None,
    }
