"""Supabase client singleton.

NOTE: This client uses the SERVICE_ROLE key, which bypasses RLS. It must NEVER be
exposed to the browser. Every query that touches user-owned data must also be filtered
by `user_id` (or `auth.uid()`-equivalent) at the application layer — see auth.py.
"""
from supabase import Client, create_client

from app.config import settings

supabase: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
