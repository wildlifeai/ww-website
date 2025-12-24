import os
from dotenv import load_dotenv
from supabase import create_client, Client
import json

load_dotenv()
# Using ANON KEY to simulate public user
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_ANON_KEY")

print(f"Checking ANON access to: {url}")
supabase: Client = create_client(url, key)

# 1. Test Firmware Access
print("\n--- ANON Access: Firmware Table ---")
try:
    r = supabase.table("firmware").select("*").execute()
    count = len(r.data)
    print(f"Found {count} firmware records.")
    if count > 0:
        print(f"First record: {r.data[0]['name']}")
    else:
        print("❌ NO records found (RLS blocking?)")
except Exception as e:
    print(f"❌ Error: {e}")

# 2. Test AI Models Access
print("\n--- ANON Access: AI Models Table ---")
try:
    r = supabase.table("ai_models").select("*").execute()
    count = len(r.data)
    print(f"Found {count} ai_models records.")
    if count > 0:
        print(f"First record: {r.data[0]['name']}")
    else:
        print("❌ NO records found (RLS blocking?)")
except Exception as e:
    print(f"❌ Error: {e}")
