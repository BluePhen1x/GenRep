-- ============================================================
-- GenRep Supabase Schema
-- Run this in: Supabase Dashboard > SQL Editor
-- ============================================================

-- Custom enum for user tiers
CREATE TYPE user_tier AS ENUM ('anonymous', 'free', 'pro');

-- ============================================================
-- user_profiles
-- ============================================================
CREATE TABLE IF NOT EXISTS user_profiles (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email         TEXT UNIQUE,
    auth_user_id  TEXT UNIQUE,
    anonymous_id  TEXT UNIQUE,
    tier          user_tier NOT NULL DEFAULT 'anonymous',
    reports_used  INTEGER NOT NULL DEFAULT 0,
    reports_limit INTEGER NOT NULL DEFAULT 1,
    page_limit    INTEGER NOT NULL DEFAULT 2,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Index for lookups
CREATE INDEX IF NOT EXISTS idx_user_profiles_email ON user_profiles (email);
CREATE INDEX IF NOT EXISTS idx_user_profiles_auth  ON user_profiles (auth_user_id);
CREATE INDEX IF NOT EXISTS idx_user_profiles_anon  ON user_profiles (anonymous_id);

-- ============================================================
-- usage_log
-- ============================================================
CREATE TABLE IF NOT EXISTS usage_log (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      TEXT,
    anonymous_id TEXT,
    sale_id      TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_usage_log_user ON usage_log (user_id);
CREATE INDEX IF NOT EXISTS idx_usage_log_date ON usage_log (created_at);

-- ============================================================
-- pending_entitlements  (Gumroad purchase before account creation)
-- ============================================================
CREATE TABLE IF NOT EXISTS pending_entitlements (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email      TEXT UNIQUE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_pending_ent_email ON pending_entitlements (email);

-- ============================================================
-- Auto-update updated_at on user_profiles
-- ============================================================
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_user_profiles_updated_at ON user_profiles;
CREATE TRIGGER trg_user_profiles_updated_at
    BEFORE UPDATE ON user_profiles
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();

-- ============================================================
-- Row Level Security (RLS) — disabled, backend uses service-role key
-- ============================================================
ALTER TABLE user_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE usage_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE pending_entitlements ENABLE ROW LEVEL SECURITY;

-- Allow service-role full access (backend bypasses RLS via service_role key)
-- No public policies = anonymous API calls are blocked by default.
