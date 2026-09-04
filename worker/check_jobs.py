"""Debug: check jobs table directly with service role."""
import os

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

# Try selecting everything
print("--- ALL jobs (no filter) ---")
res = sb.table("jobs").select("*").limit(5).execute()
print(f"Count: {len(res.data or [])}")
for r in res.data or []:
    print(r)

# Try inserting a test row
print("\n--- INSERT test job ---")
try:
    res = sb.table("jobs").insert({
        "video_id": "5b283228-c775-4000-bb5e-0aac068d0f86",
        "current_stage": "TEST",
        "status": "RUNNING",
    }).execute()
    print(f"Insert result: {res.data}")
except Exception as e:
    print(f"Insert error: {e}")
