"""
Database query functions for user_profiles, usage_log, and pending_entitlements.

Uses Supabase Python client with service-role key (bypasses RLS).
"""

from datetime import datetime, timezone
from typing import Optional

from supabase import Client

from auth import get_supabase


def _sb() -> Client:
    return get_supabase()


# ---------------------------------------------------------------------------
# Profile lookups
# ---------------------------------------------------------------------------

def get_profile_by_auth_id(auth_user_id: str) -> Optional[dict]:
    result = _sb().table("user_profiles").select("*").eq("auth_user_id", auth_user_id).execute()
    return result.data[0] if result.data else None


def get_profile_by_anonymous(anonymous_id: str) -> Optional[dict]:
    result = _sb().table("user_profiles").select("*").eq("anonymous_id", anonymous_id).execute()
    return result.data[0] if result.data else None


def get_profile_by_email(email: str) -> Optional[dict]:
    result = _sb().table("user_profiles").select("*").eq("email", email).execute()
    return result.data[0] if result.data else None


# ---------------------------------------------------------------------------
# Profile creation
# ---------------------------------------------------------------------------

def create_profile(
    email: Optional[str] = None,
    auth_user_id: Optional[str] = None,
    anonymous_id: Optional[str] = None,
    tier: str = "free",
) -> dict:
    limits = _tier_limits(tier)
    row = {
        "email": email,
        "auth_user_id": auth_user_id,
        "anonymous_id": anonymous_id,
        "tier": tier,
        "reports_used": 0,
        "reports_limit": limits["reports_limit"],
        "page_limit": limits["page_limit"],
    }
    result = _sb().table("user_profiles").insert(row).execute()
    return result.data[0]


def _tier_limits(tier: str) -> dict:
    if tier == "pro":
        return {"reports_limit": 999999, "page_limit": 50}
    if tier == "free":
        return {"reports_limit": 3, "page_limit": 10}
    # anonymous
    return {"reports_limit": 1, "page_limit": 2}


# ---------------------------------------------------------------------------
# Get or create (used by auth.get_current_user)
# ---------------------------------------------------------------------------

def get_or_create_profile_by_auth(auth_user_id: str, email: Optional[str] = None) -> dict:
    profile = get_profile_by_auth_id(auth_user_id)
    if profile:
        return profile
    # Check pending entitlements before creating
    profile = create_profile(email=email, auth_user_id=auth_user_id, tier="free")
    _claim_pending_entitlement(email, profile["id"])
    # Re-fetch to get upgraded tier if applicable
    return get_profile_by_auth_id(auth_user_id) or profile


def get_or_create_profile_by_anonymous(anonymous_id: str) -> dict:
    profile = get_profile_by_anonymous(anonymous_id)
    if profile:
        return profile
    return create_profile(anonymous_id=anonymous_id, tier="anonymous")


# ---------------------------------------------------------------------------
# Usage tracking
# ---------------------------------------------------------------------------

def can_generate(profile: dict) -> bool:
    return profile["reports_used"] < profile["reports_limit"]


def increment_usage(profile_id: str) -> dict:
    row = _sb().table("user_profiles").select("reports_used").eq("id", profile_id).execute()
    if row.data:
        new_count = row.data[0]["reports_used"] + 1
        _sb().table("user_profiles").update({"reports_used": new_count}).eq("id", profile_id).execute()
        return {"reports_used": new_count}
    return {"reports_used": 0}


def log_usage(
    user_id: Optional[str] = None,
    anonymous_id: Optional[str] = None,
    sale_id: Optional[str] = None,
) -> None:
    row = {
        "user_id": user_id,
        "anonymous_id": anonymous_id,
        "sale_id": sale_id,
    }
    _sb().table("usage_log").insert(row).execute()


def get_daily_usage_count(profile_id: str) -> int:
    """Count reports generated today for this profile."""
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    result = (
        _sb()
        .table("usage_log")
        .select("id", count="exact")
        .eq("user_id", profile_id)
        .gte("created_at", today)
        .execute()
    )
    return result.count or 0


# ---------------------------------------------------------------------------
# Tier upgrade
# ---------------------------------------------------------------------------

def upgrade_to_pro(profile_id: str) -> dict:
    _sb().table("user_profiles").update({
        "tier": "pro",
        "reports_limit": 999999,
        "page_limit": 50,
    }).eq("id", profile_id).execute()
    result = _sb().table("user_profiles").select("*").eq("id", profile_id).execute()
    return result.data[0] if result.data else {"tier": "pro"}


# ---------------------------------------------------------------------------
# Pending entitlements (Gumroad webhook before signup)
# ---------------------------------------------------------------------------

def create_pending_entitlement(email: str) -> None:
    _sb().table("pending_entitlements").upsert(
        {"email": email}, on_conflict="email"
    ).execute()


def _claim_pending_entitlement(email: Optional[str], profile_id: str) -> bool:
    """Check if there's a pending entitlement for this email and apply it."""
    if not email:
        return False
    result = (
        _sb()
        .table("pending_entitlements")
        .select("*")
        .eq("email", email)
        .execute()
    )
    if result.data:
        upgrade_to_pro(profile_id)
        _sb().table("pending_entitlements").delete().eq("email", email).execute()
        return True
    return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_page_limit(tier: str) -> int:
    return _tier_limits(tier)["page_limit"]
