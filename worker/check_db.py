"""Clean up test job row."""
import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

# Delete the test job
res = sb.table("jobs").delete().eq("current_stage", "TEST").execute()
print(f"Deleted: {res.data}")
