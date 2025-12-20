import os
import time
from dotenv import load_dotenv
from supabase import create_client, Client

# Load env vars
load_dotenv()

url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_ANON_KEY")

if not url or not key:
    print("❌ Error: SUPABASE_URL or SUPABASE_ANON_KEY not found in .env")
    exit(1)

supabase: Client = create_client(url, key)

print(f"Connecting to {url}...")

# 1. Check for Config Firmware
try:
    response = supabase.table("firmware") \
        .select("*") \
        .eq("type", "config") \
        .eq("is_active", "true") \
        .is_("deleted_at", "null") \
        .order("created_at", desc=True) \
        .limit(1) \
        .execute()
    
    if response.data:
        fw = response.data[0]
        print(f"✅ Config Firmware FOUND: {fw['name']} (v{fw['version']})")
        print(f"   Created at: {fw['created_at']}")
    else:
        print("⚠️  Config Firmware NOT found yet (Action might still be running)")

except Exception as e:
    print(f"❌ Error checking firmware: {e}")

# 2. Check for Default AI Model (General Org)
GENERAL_ORG_ID = "550e8400-e29b-41d4-a716-446655440002"
try:
    response = supabase.table("ai_models") \
        .select("*") \
        .eq("organisation_id", GENERAL_ORG_ID) \
        .is_("deleted_at", "null") \
        .order("created_at", desc=True) \
        .limit(1) \
        .execute()

    if response.data:
        model = response.data[0]
        print(f"✅ Default AI Model FOUND: {model['name']} (v{model['version']})")
    else:
        print("⚠️  Default AI Model NOT found (You need to upload one via Streamlit)")

except Exception as e:
    print(f"❌ Error checking AI models: {e}")
