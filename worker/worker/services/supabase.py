"""Single shared Supabase client for the worker process.

The previous codebase instantiated a Supabase client in 4+ modules per request.
This consolidates to one and lets stages import it directly.
"""
from supabase import Client, create_client

from worker.config import settings

supabase_client: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
