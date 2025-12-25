import os
import sys
import argparse
import zipfile
import tempfile
from pathlib import Path
from dotenv import load_dotenv
from supabase import create_client, Client

# Constants
GENERAL_ORG_ID = os.environ.get("GENERAL_ORG_ID", "b0000000-0000-0000-0000-000000000001")
DEFAULT_EMAIL = os.environ.get("UPLOADER_EMAIL", "apps@wildlife.ai")
DEFAULT_PASSWORD = os.environ.get("UPLOADER_PASSWORD")

def deploy_model(supabase: Client, file_path: Path, model_name: str, version: str, description: str, labels: list):
    """Uploads model to storage and registers in database."""
    print(f"📦 Deploying {model_name} v{version}...")
    
    if not file_path.exists():
        print(f"❌ Error: File {file_path} not found.")
        print(f"   Current Working Directory: {os.getcwd()}")
        if os.path.exists('models'):
            print(f"   Contents of 'models' directory: {os.listdir('models')}")
        else:
            print("   'models' directory does NOT exist.")
        return False

    # Package into ai_model.zip compatible with Manifest generator
    with tempfile.TemporaryDirectory() as temp_dir:
        working_path = Path(temp_dir)
        zip_path = working_path / "ai_model.zip"
        labels_path = working_path / "labels.txt"
        
        # 1. Create labels.txt
        labels_path.write_text("\n".join(labels))
        
        # 2. Create uncompressed ZIP (ZIP_STORED) as expected by the camera firmware
        # Filename inside zip should be the model's basename (usually .tflite or .tfl)
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_STORED) as zf:
            zf.write(file_path, file_path.name)
            zf.write(labels_path, "labels.txt")
            
        # 3. Upload to Storage
        # NEW FORMAT: {org_id}/{model_name}-custom-{version}/ai_model.zip
        # This matches the RLS policy regex requirement and Streamlit app convention
        safe_model_name = model_name.lower().replace(' ', '-')
        storage_path = f"{GENERAL_ORG_ID}/{safe_model_name}-custom-{version}/ai_model.zip"
        
        try:
            print(f"   Uploading to storage: {storage_path}...")
            # Memory efficient: pass the file path instead of bytes
            supabase.storage.from_('ai-models').upload(
                path=storage_path,
                file=str(zip_path), 
                file_options={"content-type": "application/zip", "upsert": "true"}
            )
        except Exception as e:
            print(f"   ❌ Storage upload failed: {e}")
            return False

        # 4. Register in Database
        try:
            print(f"   Registering in database...")
            # Reduction of redundant API calls: call once
            user_response = supabase.auth.get_user()
            user_id = user_response.user.id
            
            # Check if model already exists (org_id + name + version unique)
            existing = supabase.table('ai_models')\
                .select('id')\
                .eq('organisation_id', GENERAL_ORG_ID)\
                .eq('name', model_name)\
                .eq('version', version)\
                .is_('deleted_at', 'null')\
                .execute()
            
            model_data = {
                "name": model_name,
                "version": version,
                "description": description,
                "organisation_id": GENERAL_ORG_ID,
                "storage_path": storage_path,
                "file_size_bytes": zip_path.stat().st_size,
                "file_type": "manifest",
                "detection_capabilities": labels,
                "modified_by": user_id,
                "uploaded_by": user_id
            }
            
            if existing.data:
                # Update existing model (version overwrite)
                model_id = existing.data[0]['id']
                supabase.table('ai_models')\
                    .update(model_data)\
                    .eq('id', model_id)\
                    .execute()
                print(f"   ✅ Updated existing model record.")
            else:
                # Insert new model
                supabase.table('ai_models')\
                    .insert(model_data)\
                    .execute()
                print(f"   ✅ Inserted new model record.")
            
            return True
        except Exception as e:
            print(f"   ❌ Database registration failed: {e}")
            return False

def main():
    parser = argparse.ArgumentParser(description="Deploy AI models to Supabase using apps@wildlife.ai persona")
    parser.add_argument("--url", help="Supabase URL")
    parser.add_argument("--key", help="Supabase Anon Key")
    parser.add_argument("--email", default=DEFAULT_EMAIL, help="User email")
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help="User password")
    args = parser.parse_args()

    load_dotenv()
    url = args.url or os.environ.get("SUPABASE_URL")
    key = args.key or os.environ.get("SUPABASE_ANON_KEY")

    if not url or not key:
        print("❌ Error: SUPABASE_URL and SUPABASE_ANON_KEY are required.")
        return

    # Early validation of password
    if not args.password:
        print("❌ Error: Password is required. Set UPLOADER_PASSWORD or use --password.")
        return

    supabase: Client = create_client(url, key)
    
    try:
        print(f"🔑 Logging in as {args.email}...")
        supabase.auth.sign_in_with_password({"email": args.email, "password": args.password})
        print("✅ Login successful.")
    except Exception as e:
        print(f"❌ Login failed: {e}")
        sys.exit(1)

    # Define models to deploy
    # Note: Person detection model is converted from GitHub source
    models_to_deploy = [
        {
            "path": Path("models/person_detection.tflite"),
            "name": "Person Detection Model",
            "version": "1.0.0",
            "description": "High-accuracy model for detecting human presence in camera trap footage",
            "labels": ["person", "no person"]
        }
    ]

    success_all = True
    for m in models_to_deploy:
        if not deploy_model(supabase, m["path"], m["name"], m["version"], m["description"], m["labels"]):
            success_all = False
    
    if not success_all:
        print("❌ One or more deployments failed.")
        sys.exit(1)
    else:
        print("🏁 All deployments completed successfully.")

if __name__ == "__main__":
    main()
