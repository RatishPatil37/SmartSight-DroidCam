-- ==========================================================
-- SMARTSIGHT 10X CLOUDCAM SUPABASE SCHEMA MIGRATION
-- Paste this entire block into Supabase SQL Editor and click RUN
-- ==========================================================

-- 1. Create Telemetry Table for Device Health & Vitals
CREATE TABLE IF NOT EXISTS public.device_telemetry (
    device_id TEXT PRIMARY KEY,
    last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    battery_level INT DEFAULT 100,
    cpu_temp NUMERIC(5, 2),
    cpu_usage NUMERIC(5, 2),
    ram_usage NUMERIC(5, 2),
    tailscale_ip TEXT,
    droidcam_status TEXT DEFAULT 'offline',
    is_online BOOLEAN DEFAULT false
);

-- 2. Create Commands Table (Queue for Remote Triggers from UI)
CREATE TABLE IF NOT EXISTS public.device_commands (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id TEXT NOT NULL,
    command_type TEXT NOT NULL CHECK (command_type IN ('capture_photo', 'capture_video', 'ai_inspect', 'audio_beacon', 'ping')),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
    payload JSONB DEFAULT '{}'::jsonb,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    executed_at TIMESTAMPTZ
);

-- 3. Create Captures Table (Photo/Video/AI Vault)
CREATE TABLE IF NOT EXISTS public.captures (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id TEXT NOT NULL,
    media_url TEXT NOT NULL,
    thumbnail_url TEXT,
    media_type TEXT NOT NULL CHECK (media_type IN ('photo', 'video')),
    file_size_kb INT,
    ai_caption TEXT,
    ai_tags TEXT[],
    captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    synced_from_offline BOOLEAN DEFAULT false
);

-- 4. Enable Supabase Realtime Replication for Instant WebSocket Push
ALTER PUBLICATION supabase_realtime ADD TABLE public.device_telemetry;
ALTER PUBLICATION supabase_realtime ADD TABLE public.device_commands;
ALTER PUBLICATION supabase_realtime ADD TABLE public.captures;

-- 5. Row Level Security (RLS) - Permissive for Prototype
ALTER TABLE public.device_telemetry ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.device_commands ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.captures ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Allow public read on telemetry" ON public.device_telemetry;
CREATE POLICY "Allow public read on telemetry" ON public.device_telemetry FOR SELECT USING (true);

DROP POLICY IF EXISTS "Allow public update on telemetry" ON public.device_telemetry;
CREATE POLICY "Allow public update on telemetry" ON public.device_telemetry FOR ALL USING (true);

DROP POLICY IF EXISTS "Allow public read/insert on commands" ON public.device_commands;
CREATE POLICY "Allow public read/insert on commands" ON public.device_commands FOR ALL USING (true);

DROP POLICY IF EXISTS "Allow public read/insert on captures" ON public.captures;
CREATE POLICY "Allow public read/insert on captures" ON public.captures FOR ALL USING (true);

-- 6. Storage Bucket Configuration (Run in Storage UI or via Storage API)
-- Bucket Name: smartsight-media (Set to Public)
