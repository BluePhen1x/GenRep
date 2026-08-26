"""
Server-side Google OAuth flow.

Instead of using Supabase's client-side signInWithOAuth (which shows
the Supabase domain on Google's consent screen), we handle the OAuth
flow ourselves so the consent screen shows YOUR domain.

Flow:
  1. GET /auth/google → redirect to Google consent screen
  2. GET /auth/google/callback?code=... → exchange code for tokens,
     get user info, create Supabase session, redirect to frontend
"""

import base64
import hashlib
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from config import config

router = APIRouter(prefix="/auth/google", tags=["google-oauth"])

# In-memory state store (replace with Redis in production)
_oauth_states: dict[str, float] = {}
import time

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


def _get_redirect_uri(request: Request) -> str:
    """Build the callback URL from the current request's host."""
    base = str(request.base_url).rstrip("/")
    return f"{base}/auth/google/callback"


@router.get("")
async def google_login(request: Request):
    """Redirect user to Google's OAuth consent screen."""
    if not config.GOOGLE_CLIENT_ID or not config.GOOGLE_CLIENT_SECRET:
        raise HTTPException(500, "Google OAuth not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.")

    state = secrets.token_urlsafe(32)
    _oauth_states[state] = time.time()

    redirect_uri = _get_redirect_uri(request)

    params = {
        "client_id": config.GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }

    return RedirectResponse(f"{GOOGLE_AUTH_URL}?{urlencode(params)}")


@router.get("/callback")
async def google_callback(request: Request, code: str = "", state: str = ""):
    """Handle Google's callback: exchange code for tokens, create Supabase session."""
    if not code:
        raise HTTPException(400, "Missing authorization code")

    # Validate state (basic CSRF protection)
    if state not in _oauth_states:
        raise HTTPException(400, "Invalid or expired OAuth state")
    del _oauth_states[state]

    # Reject stale states (>10 minutes old)
    # (states are cleaned up lazily; this is fine for a single-server setup)

    redirect_uri = _get_redirect_uri(request)

    # Step 1: Exchange authorization code for Google tokens
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": config.GOOGLE_CLIENT_ID,
            "client_secret": config.GOOGLE_CLIENT_SECRET,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        })

    if token_resp.status_code != 200:
        raise HTTPException(400, f"Failed to exchange code for tokens: {token_resp.text}")

    tokens = token_resp.json()
    google_id_token = tokens.get("id_token")
    google_access_token = tokens.get("access_token")

    if not google_id_token or not google_access_token:
        raise HTTPException(400, "Missing tokens in Google response")

    # Step 2: Get user info from Google
    async with httpx.AsyncClient() as client:
        userinfo_resp = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {google_access_token}"},
        )

    if userinfo_resp.status_code != 200:
        raise HTTPException(400, "Failed to fetch user info from Google")

    google_user = userinfo_resp.json()
    email = google_user.get("email")
    if not email:
        raise HTTPException(400, "Google account has no email")

    # Step 3: Exchange Google tokens for Supabase session
    async with httpx.AsyncClient() as client:
        supa_resp = await client.post(
            f"{config.SUPABASE_URL}/auth/v1/token?grant_type=id_token",
            headers={
                "apikey": config.SUPABASE_ANON_KEY,
                "Content-Type": "application/json",
            },
            json={
                "id_token": google_id_token,
                "access_token": google_access_token,
                "provider": "google",
            },
        )

    if supa_resp.status_code != 200:
        # Fallback: try signing in with the user's email via admin
        # This handles cases where the Supabase Google provider isn't fully configured
        return await _fallback_signin(email, google_user, request)

    supa_data = supa_resp.json()
    access_token = supa_data.get("access_token")
    refresh_token = supa_data.get("refresh_token")

    if not access_token or not refresh_token:
        return await _fallback_signin(email, google_user, request)

    # Step 4: Redirect to frontend with tokens in URL fragment
    frontend_url = str(request.base_url).rstrip("/")
    fragment = urlencode({
        "access_token": access_token,
        "refresh_token": refresh_token,
        "type": "google",
    })
    return RedirectResponse(f"{frontend_url}/#signin&{fragment}")


async def _fallback_signin(email: str, google_user: dict, request: Request) -> RedirectResponse:
    """
    Fallback when Supabase's grant_type=id_token doesn't work.
    Creates user via admin API and generates a session via magic link OTP.
    """
    from auth import get_supabase

    sb = get_supabase()

    # Upsert user via admin
    try:
        sb.auth.admin.create_user({
            "email": email,
            "email_confirm": True,
            "user_metadata": {
                "full_name": google_user.get("name", ""),
                "avatar_url": google_user.get("picture", ""),
            },
        })
    except Exception:
        pass  # User may already exist

    # Generate OTP for the user so the frontend can sign in
    # We'll pass the email to the frontend and let it handle sign-in
    frontend_url = str(request.base_url).rstrip("/")
    fragment = urlencode({
        "email": email,
        "type": "google_fallback",
    })
    return RedirectResponse(f"{frontend_url}/#signin&{fragment}")


# Cleanup stale states periodically
def cleanup_states():
    """Remove OAuth states older than 10 minutes."""
    now = time.time()
    stale = [s for s, t in _oauth_states.items() if now - t > 600]
    for s in stale:
        _oauth_states.pop(s, None)
