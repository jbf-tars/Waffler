-- ═══════════════════════════════════════════════════════════════════
-- Natter — Supabase Database Schema
-- Run this in the SQL Editor at supabase.com/dashboard
-- ═══════════════════════════════════════════════════════════════════

-- 1. Profiles table (extends auth.users)
CREATE TABLE public.profiles (
  id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  email TEXT,
  display_name TEXT,
  tier TEXT DEFAULT 'free' CHECK (tier IN ('free', 'pro', 'enterprise')),
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view own profile"
  ON public.profiles FOR SELECT
  USING (auth.uid() = id);

CREATE POLICY "Users can update own profile"
  ON public.profiles FOR UPDATE
  USING (auth.uid() = id);

-- 2. Usage tracking table (per-user, per-day)
CREATE TABLE public.usage (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE,
  date DATE DEFAULT CURRENT_DATE,
  transcription_count INT DEFAULT 0,
  total_audio_seconds FLOAT DEFAULT 0,
  UNIQUE(user_id, date)
);

ALTER TABLE public.usage ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view own usage"
  ON public.usage FOR SELECT
  USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own usage"
  ON public.usage FOR INSERT
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update own usage"
  ON public.usage FOR UPDATE
  USING (auth.uid() = user_id);

-- 3. Auto-create profile when a user signs up
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
  INSERT INTO public.profiles (id, email)
  VALUES (NEW.id, NEW.email);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW
  EXECUTE FUNCTION public.handle_new_user();

-- 4. Helper function to increment usage (called after each transcription)
CREATE OR REPLACE FUNCTION public.increment_usage(p_user_id UUID, p_audio_seconds FLOAT)
RETURNS VOID AS $$
BEGIN
  INSERT INTO public.usage (user_id, date, transcription_count, total_audio_seconds)
  VALUES (p_user_id, CURRENT_DATE, 1, p_audio_seconds)
  ON CONFLICT (user_id, date)
  DO UPDATE SET
    transcription_count = public.usage.transcription_count + 1,
    total_audio_seconds = public.usage.total_audio_seconds + p_audio_seconds;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;
