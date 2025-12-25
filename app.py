# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later

import streamlit as st
import os
import re
import zipfile
import shutil
import subprocess
import tempfile
from pathlib import Path
from dotenv import load_dotenv
from supabase import create_client, Client
from gotrue.errors import AuthApiError
from typing import Optional, Dict, List
from urllib.parse import urlparse

# Load environment variables
load_dotenv()

# Constants
GENERAL_ORG_ID = 'b0000000-0000-0000-0000-000000000001'  # General organization from seed data

# Initialize Supabase client
def get_supabase() -> Optional[Client]:
    """
    Get a Supabase client that is correctly initialized with the 
    user's session token if they are logged in.
    """
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_ANON_KEY")
    
    if not url or not key:
        return None
        
    # Ensure URL has trailing slash to avoid warnings and potential path issues
    if not url.endswith("/"):
        url += "/"
    
    try:
        # Create client
        client = create_client(url, key)
        
        # If user is logged in, restore the session
        # This is the most reliable way to propagate auth to all sub-clients (DB, Storage, etc.)
        if 'session' in st.session_state and st.session_state.session:
            try:
                client.auth.set_session(
                    access_token=st.session_state.session.access_token,
                    refresh_token=st.session_state.session.refresh_token
                )
            except Exception as auth_e:
                st.warning(f"Failed to restore session: {auth_e}")
            
        return client
    except Exception as e:
        st.error(f"Failed to initialize Supabase client: {str(e)}")
        return None

def init_supabase() -> Optional[Client]:
    """Initialize or update the global Supabase client"""
    return get_supabase()

# --- Helper Functions from your Notebook ---

def parse_model_zip_name(zip_path: str):
    """Parse '<modelname>-custom-<version>.zip' -> (modelname, version)"""
    name = os.path.basename(zip_path)
    if not name.endswith('.zip'):
        raise ValueError('Zip file must end with .zip')
    base = name[:-4]
    if '-custom-' in base:
        modelname, version = base.split('-custom-', 1)
        if modelname and version:
            return modelname, version
    
    # Fallback if pattern is not found
    return "unknown", "1.0.0"

def safe_move(src: Path, dst: Path):
    """Safely move a file, creating parent dirs and overwriting old file."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    shutil.move(str(src), str(dst))

def find_vela_output(work_dir: Path, original_tflite_name: str) -> Path:
    """Find the output file from Vela.
    Vela can be inconsistent. It might be 'trained_vela.tflite', 
    'MOD00001.tfl', or overwrite the original.
    """
    # Convert string filename to Path to access .stem
    tflite_path_obj = Path(original_tflite_name)
    stem = tflite_path_obj.stem
    
    # Common vela output names
    possible_names = [
        work_dir / f"{stem}_vela.tflite",
        work_dir / "MOD00001.tfl", # As seen in your notebook log
        work_dir / "output.tflite"
    ]
    
    for path in possible_names:
        if path.exists():
            return path
            
    # Check if it overwrote the original (less common, but possible)
    original_path = work_dir / original_tflite_name
    if original_path.exists():
        # This is tricky; we assume if no other file exists, it's this one.
        # A more robust check might be needed if vela behavior is unknown.
        st.warning(f"Could not find a distinct Vela output file. Assuming {original_tflite_name} was overwritten.")
        return original_path

    raise FileNotFoundError(f"Could not find Vela output file in {work_dir}. Looked for {possible_names}.")


# --- Main Conversion Logic ---

def run_conversion(uploaded_file):
    """
    Takes an uploaded file object, runs the full conversion pipeline,
    and returns the bytes of the final Manifest.zip (or None on failure).
    Returning bytes avoids TemporaryDirectory cleanup race conditions
    when Streamlit re-runs the script.
    """
    # Create a temporary directory to work in
    with tempfile.TemporaryDirectory() as temp_dir:
        base_path = Path(temp_dir)
        
        # 1. Save uploaded file
        uploaded_zip_path = base_path / uploaded_file.name
        with open(uploaded_zip_path, 'wb') as f:
            f.write(uploaded_file.getbuffer())

        # 2. Unzip and validate
        st.write(f"Processing {uploaded_file.name}...")
        model_name, model_version = parse_model_zip_name(str(uploaded_zip_path))
        container_name = f"{model_name}-custom-{model_version}"
        work_dir = base_path / 'work' / container_name
        work_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(uploaded_zip_path, 'r') as z:
            z.extractall(work_dir)

        tflite_path = work_dir / 'trained.tflite'
        vars_h_path = work_dir / 'model-parameters' / 'model_variables.h'

        if not tflite_path.exists():
            raise FileNotFoundError(f"trained.tflite not found at {tflite_path}")
        if not vars_h_path.exists():
            raise FileNotFoundError(f"model_variables.h not found at {vars_h_path}")
        
        st.write("File unzipped. Found trained.tflite and model_variables.h.")

        # 3. Run Vela conversion
        st.write("Running Vela conversion...")
        
        # Note: Vela config is hardcoded as in your notebook
        cmd = [
            'vela',
            '--accelerator-config', 'ethos-u55-64',
            '--memory-mode', 'Shared_Sram',
            '--output-dir', str(work_dir),
            str(tflite_path),
        ]

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=120)
            st.code(res.stdout)
            if res.stderr:
                st.warning(f"Vela stderr:\n{res.stderr}")
        except subprocess.CalledProcessError as e:
            st.error(f"Vela conversion FAILED. Return code: {e.returncode}")
            st.error(f"Stdout:\n{e.stdout}")
            st.error(f"Stderr:\n{e.stderr}")
            return None
        except FileNotFoundError:
            st.error("Vela command not found. This app is likely not deployed correctly.")
            return None
            
        st.write("Vela conversion successful.")

        # Find the output file and rename it
        vela_original_output = find_vela_output(work_dir, tflite_path.name)

        # Build 8.3-style name from model_variables.h: <last4digits>V<version>.tfl
        target_name = None
        try:
            with open(vars_h_path, 'r', encoding='utf-8', errors='replace') as _f:
                _hdr = _f.read()
            _pid_m = re.search(r"\.project_id\s*=\s*(\d+)", _hdr)
            _ver_m = re.search(r"\.deploy_version\s*=\s*(\d+)", _hdr)
            if _pid_m and _ver_m:
                _pid = _pid_m.group(1)
                _ver = str(int(_ver_m.group(1)))  # normalize to no leading zeros
                _last4 = _pid[-4:].rjust(4, '0')
                _base = f"{_last4}V{_ver}"
                if len(_base) > 8:
                    st.warning(f"Generated filename '{_base}' exceeds 8 characters; truncating to 8 for 8.3 compliance.")
                    _base = _base[:8]
                target_name = _base + ".tfl"
        except Exception as _e:
            # fall back if parsing fails
            pass

        if not target_name:
            # Fallback to a default pattern if parsing failed
            target_name = "MOD00001.tfl"

        vela_final_path = work_dir / target_name
        # If a file with target_name already exists, overwrite it to ensure a deterministic name
        if vela_final_path.exists():
            vela_final_path.unlink()

        safe_move(vela_original_output, vela_final_path)
        st.write(f"Vela model saved as: {vela_final_path.name}")


        # 4. Extract labels
        with open(vars_h_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()

        match = re.search(r'const char\*\s*ei_classifier_inferencing_categories.*?=\s*\{(.*?)\};', content, re.DOTALL)
        if match:
            labels = re.findall(r'\"([^\"]+)\"', match.group(1))
        else:
            labels = []

        if not labels:
            raise RuntimeError('No labels found in model_variables.h.')

        # Store labels in session state for upload
        st.session_state['labels'] = labels
        
        labels_txt_path = work_dir / 'labels.txt'
        with open(labels_txt_path, 'w') as f:
            f.write('\n'.join(labels))
        
        st.write(f"Labels extracted: {labels}")

        # 5. Package AI Model
        # The AI model package itself is stored as ai_model.zip in the bucket
        ai_model_zip_path = work_dir / 'ai_model.zip'
        
        # Create a store-compressed zip (no compression) containing labels.txt and the model file
        with zipfile.ZipFile(ai_model_zip_path, mode='w', compression=zipfile.ZIP_STORED) as zf:
            # We want these files at the root of ai_model.zip
            zf.write(vela_final_path, vela_final_path.name)
            zf.write(labels_txt_path, 'labels.txt')

        if not ai_model_zip_path.exists():
            raise FileNotFoundError(f"Failed to create ai_model.zip at {ai_model_zip_path}")

        # Read bytes while tempdir is still valid and return them to caller
        with open(ai_model_zip_path, 'rb') as f:
            model_bytes = f.read()

        st.success("ai_model.zip created successfully!")
        return model_bytes

# --- Public MANIFEST Download Functions ---

def fetch_latest_config_firmware(supabase: Client) -> Optional[Dict]:
    """
    Fetch the latest active config firmware from the firmware table.
    Returns dict with location_path and metadata, or None if not found.
    """
    try:
        supabase_client = get_supabase()
        if not supabase_client:
            return None
            
        response = supabase_client.table('firmware')\
            .select('*')\
            .eq('type', 'config')\
            .eq('is_active', True)\
            .is_('deleted_at', 'null')\
            .order('created_at', desc=True)\
            .limit(1)\
            .execute()
        
        if response.data and len(response.data) > 0:
            return response.data[0]
        return None
    except Exception as e:
        st.warning(f"Could not fetch config firmware: {str(e)}")
        return None

def fetch_latest_default_model(supabase: Client) -> Optional[Dict]:
    """
    Fetch the default AI model.
    Prioritizes the 'Person Detector' model, otherwise falls back to the latest 
    available model from the General organization.
    Returns dict with storage_path and metadata, or None if not found.
    """
    try:
        supabase_client = get_supabase()
        if not supabase_client:
            return None

        # First, try to find a Person Detector model (case-insensitive and partial match)
        response = supabase_client.table('ai_models')\
            .select('*')\
            .ilike('name', '%Person%Detector%')\
            .is_('deleted_at', 'null')\
            .order('created_at', desc=True)\
            .limit(1)\
            .execute()
        
        if response.data and len(response.data) > 0:
            return response.data[0]
            
        # Second try: Any model containing "Person"
        response = supabase_client.table('ai_models')\
            .select('*')\
            .ilike('name', '%Person%')\
            .is_('deleted_at', 'null')\
            .order('created_at', desc=True)\
            .limit(1)\
            .execute()
            
        if response.data and len(response.data) > 0:
            return response.data[0]
            
        # Fallback to the latest model available in the system
        response = supabase_client.table('ai_models')\
            .select('*')\
            .is_('deleted_at', 'null')\
            .order('created_at', desc=True)\
            .limit(1)\
            .execute()
        
        if response.data and len(response.data) > 0:
            return response.data[0]
            
        return None
    except Exception as e:
        st.warning(f"Could not fetch default AI model: {str(e)}")
        return None

def download_from_storage(supabase: Client, bucket: str, path: str, dest: Path, silent: bool = False) -> bool:
    """
    Download a file from Supabase Storage to local path.
    Returns True on success, False on failure.
    """
    try:
        # FIX: Robust storage pathing
        # 1. Clean up potential prefixes or full URLs
        if path.startswith('http'):
            parsed_url = urlparse(path)
            path_parts = parsed_url.path.strip('/').split('/')
            try:
                bucket_index = path_parts.index(bucket)
                path = '/'.join(path_parts[bucket_index + 1:])
            except (ValueError, IndexError):
                if not silent:
                    st.error(f"Could not extract path from URL: {path}")
                return False
        
        # 2. Candidate paths to try
        candidates = [path]
        
        # If starts with bucket name, try without it
        if path.startswith(f"{bucket}/"):
            candidates.append(path[len(bucket)+1:])
            
        # If it contains folders, try just the filename as a fallback (root level)
        if '/' in path:
            filename = path.split('/')[-1]
            if filename not in candidates:
                candidates.append(filename)
        
        last_error = None
        for try_path in candidates:
            try:
                # Silence internal attempts to keep UI clean
                response = supabase.storage.from_(bucket).download(try_path)
                if response:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(response)
                    return True
            except Exception as e:
                last_error = e
                # Continue to next candidate
                pass
        
        # Only show error if all candidates failed AND not silent
        if not silent:
            st.error(f"❌ Failed to download from storage (tried {candidates}): {str(last_error)}")
        return False
    except Exception as e:
        if not silent:
            st.error(f"An error occurred while downloading the file: {str(e)}")
        return False

def flatten_directory(directory: Path):
    """
    Move all files from subdirectories to the root of 'directory' and remove subdirectories.
    """
    for item in list(directory.rglob('*')):
        if item.is_file():
            # If the file is not already in the root, move it
            if item.parent != directory:
                target = directory / item.name
                if target.exists():
                    target.unlink() # Overwrite if duplicate name
                shutil.move(str(item), str(target))
    
    # Remove now-empty subdirectories, which may contain other empty directories.
    for item in directory.iterdir():
        if item.is_dir():
            shutil.rmtree(item)


def create_manifest_package(supabase: Client) -> Optional[bytes]:
    """
    Create a complete MANIFEST.zip package containing:
    - Latest config firmware files
    - Latest default AI model
    
    Structure:
    MANIFEST/
      CONFIG.TXT
      labels.txt
      <model>.tfl
      ...
    
    Returns bytes of the final zip, or None on failure.
    """
    temp_dir = None
    try:
        # Create temporary directory
        temp_dir = Path(tempfile.mkdtemp())
        manifest_dir = temp_dir / "MANIFEST"
        manifest_dir.mkdir()
        
        # 1. Fetch and download latest config firmware
        config_firmware = fetch_latest_config_firmware(supabase)
        if config_firmware:
            path = config_firmware['location_path']
            config_file_path = temp_dir / "config_component"
            
            # Try primary path silently
            if download_from_storage(supabase, 'firmware', path, config_file_path, silent=True):
                if path.lower().endswith('.zip'):
                    with zipfile.ZipFile(config_file_path, 'r') as zip_ref:
                        zip_ref.extractall(manifest_dir)
                else:
                    filename = path.split('/')[-1]
                    shutil.copy2(config_file_path, manifest_dir / filename)
                st.success(f"✅ Added config firmware: {config_firmware.get('version', 'latest')}")
            else:
                # FALLBACK Discovery
                try:
                    files = supabase.storage.from_('firmware').list('config', {'order_by': {'column': 'created_at', 'order': 'desc'}})
                    if files:
                        latest_file = files[0]['name']
                        new_path = f"config/{latest_file}"
                        if download_from_storage(supabase, 'firmware', new_path, config_file_path, silent=True):
                            if latest_file.lower().endswith('.zip'):
                                with zipfile.ZipFile(config_file_path, 'r') as zip_ref:
                                    zip_ref.extractall(manifest_dir)
                            else:
                                shutil.copy2(config_file_path, manifest_dir / latest_file)
                            st.success(f"✅ Recovered latest config: {latest_file}")
                except:
                    pass
        
        # 2. Fetch and download latest default AI model
        ai_model = fetch_latest_default_model(supabase)
        if not ai_model:
            # Discovery fallback for AI models if table is empty
            try:
                org_folder = "b0000000-0000-0000-0000-000000000001"
                subdirs = supabase.storage.from_('ai-models').list(org_folder, {'limit': 5})
                if subdirs:
                    for sd in subdirs:
                        model_name = sd['name']
                        files = supabase.storage.from_('ai-models').list(f"{org_folder}/{model_name}")
                        for f in files:
                            if f['name'] == 'ai_model.zip':
                                model_zip_path = temp_dir / "ai_model.zip"
                                if download_from_storage(supabase, 'ai-models', f"{org_folder}/{model_name}/{f['name']}", model_zip_path, silent=True):
                                    with zipfile.ZipFile(model_zip_path, 'r') as zip_ref:
                                        zip_ref.extractall(manifest_dir)
                                    st.success(f"✅ Added AI model: {model_name}")
                                    break
                        else: continue
                        break
            except:
                pass
        else:
            model_zip_path = temp_dir / "ai_model_db.zip"
            path = ai_model['storage_path']
            if download_from_storage(supabase, 'ai-models', path, model_zip_path, silent=True):
                with zipfile.ZipFile(model_zip_path, 'r') as zip_ref:
                    zip_ref.extractall(manifest_dir)
                st.success(f"✅ Added AI model: {ai_model.get('name', 'latest')}")
        
        # 3. Flatten the directory structure
        flatten_directory(manifest_dir)
        
        # 4. Create final MANIFEST.zip (uncompressed)
        final_zip_path = temp_dir / "MANIFEST_final.zip"
        files_to_zip = list(manifest_dir.glob('*'))
        if not files_to_zip:
            st.error("❌ No files found to include in MANIFEST. Discovery failed.")
            return None
            
        with zipfile.ZipFile(final_zip_path, 'w', zipfile.ZIP_STORED) as zipf:
            for file in files_to_zip:
                if file.is_file():
                    arcname = f"MANIFEST/{file.name}"
                    zipf.write(file, arcname)
        
        # Read final zip as bytes
        manifest_bytes = final_zip_path.read_bytes()
        return manifest_bytes
        
    except Exception as e:
        st.error(f"❌ Failed to create MANIFEST package: {str(e)}")
        return None
    finally:
        # Cleanup temp directory
        if temp_dir and temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)

# --- Supabase Integration Functions ---

def render_login(supabase: Client) -> bool:
    """
    Render authentication sidebar.
    Returns True if user is logged in, False otherwise.
    """
    st.sidebar.title("🔐 Authentication")
    if 'user' in st.session_state and st.session_state.get('user'):
        user_email = st.session_state.user.email
        user_id = st.session_state.user.id
        st.sidebar.success(f"✅ Logged in as:  \n**{user_email}**")
        
        # Diagnostic Info
        with st.sidebar.expander("🔍 Account Diagnostics"):
            st.code(f"Streamlit User ID: {user_id}")
            try:
                # 1. Verify what Supabase thinks the user is
                supabase_user = supabase.auth.get_user()
                if supabase_user and supabase_user.user:
                    st.success(f"Supabase-side ID: {supabase_user.user.id}")
                else:
                    st.error("Supabase client is NOT authenticated!")
                
                # 2. Check for roles in DB
                resp = supabase.table('user_roles').select('role, scope_type').eq('user_id', user_id).execute()
                if resp.data:
                    st.write("Roles found in DB:")
                    for r in resp.data:
                        st.write(f"- {r['role']} ({r['scope_type']})")
                else:
                    st.warning("No roles found for this ID in DB.")
            except Exception as e:
                st.error(f"Diagnostic Error: {e}")

        if st.sidebar.button("Logout", use_container_width=True):
            supabase.auth.sign_out()
            st.session_state.clear()
            st.rerun()
        return True
    
    with st.sidebar.form("login_form"):
        email = st.text_input("Email", placeholder="user@example.com")
        password = st.text_input("Password", type="password")
        submit = st.form_submit_button("Login", use_container_width=True)
        
        if submit:
            if not email or not password:
                st.sidebar.error("Please enter both email and password")
                return False
            
            try:
                response = supabase.auth.sign_in_with_password({
                    "email": email,
                    "password": password
                })
                st.session_state.user = response.user
                st.session_state.session = response.session
                st.sidebar.success("Login successful!")
                st.rerun()
            except AuthApiError as e:
                st.sidebar.error(f"Login failed: {e}")
                return False
            except Exception as e:
                st.sidebar.error(f"An unexpected error occurred: {e}")
                return False
    
    return False


def check_user_role(supabase: Client, user_id: str, org_id: str) -> bool:
    """
    Check if user has organisation_manager or ww_admin role.
    Returns True if authorized, False otherwise.
    """
    try:
        supabase_client = get_supabase()
        if not supabase_client:
            return False

        # Use RPC function for secure role checking
        # This prevents injection vulnerabilities and moves logic to the database
        response = supabase_client.rpc('check_user_uploader_role', {
            'p_user_id': user_id,
            'p_org_id': org_id
        }).execute()
        
        return bool(response.data)
        
    except Exception as e:
        st.error(f"❌ Role check failed: {str(e)}")
        return False


def get_user_organizations(supabase: Client, user_id: str) -> List[Dict[str, str]]:
    """
    Fetch organizations the user belongs to.
    Returns a list of dicts, each with 'name' and 'id' of an org.
    """
    try:
        supabase_client = get_supabase()
        if not supabase_client:
            return []

        # Step 1: Get user roles for organisations (fetch scope_ids)
        roles_response = supabase_client.table('user_roles')\
            .select('scope_id')\
            .eq('user_id', user_id)\
            .eq('scope_type', 'organisation')\
            .eq('is_active', True)\
            .is_('deleted_at', 'null')\
            .execute()
        
        if not roles_response.data:
            return []
        
        # Extract unique org IDs
        org_ids = list(set(role['scope_id'] for role in roles_response.data))
        
        # Step 2: Fetch organization details
        orgs_response = supabase_client.table('organisations')\
            .select('id, name')\
            .in_('id', org_ids)\
            .execute()
        
        orgs = [
            {"name": org['name'], "id": org['id']}
            for org in orgs_response.data
        ]
        # Sort by name for a better user experience
        orgs.sort(key=lambda x: x['name'])
        return orgs
        
    except Exception as e:
        st.error(f"❌ Failed to fetch organizations: {str(e)}")
        return []


def upload_model_to_storage(
    supabase: Client,
    manifest_bytes: bytes,
    model_name: str,
    version: str,
    org_id: str
) -> str:
    """
    Upload ai_model.zip to Supabase Storage.
    Storage path: <org_id>/<model_name>-custom-<version>/ai_model.zip
    Returns: storage_path
    """
    # Sanitize model_name and version to prevent path traversal
    safe_model_name = os.path.basename(model_name)
    safe_version = os.path.basename(version)
    
    # Generate storage path following the naming convention
    # New format: {org_id}/{model_name}-custom-{version}/ai_model.zip
    storage_path = f"{org_id}/{safe_model_name}-custom-{safe_version}/ai_model.zip"
    
    try:
        # Upload to ai-models bucket
        response = supabase.storage.from_('ai-models').upload(
            path=storage_path,
            file=manifest_bytes,
            file_options={
                "content-type": "application/zip",
                "upsert": "true"  # Allow overwriting existing versions
            }
        )
        
        return storage_path
        
    except Exception as e:
        raise Exception(f"Storage upload failed: {str(e)}")


def register_model_in_db(
    supabase: Client,
    name: str,
    version: str,
    description: str,
    org_id: str,
    user_id: str,
    storage_path: str,
    file_size_bytes: int,
    detection_capabilities: List[str]
) -> Dict:
    """
    Insert or update model record in ai_models table.
    Supports version overwriting.
    Returns: model record
    """
    try:
        # Check if model already exists (org_id + name + version unique)
        existing = supabase.table('ai_models')\
            .select('id')\
            .eq('organisation_id', org_id)\
            .eq('name', name)\
            .eq('version', version)\
            .is_('deleted_at', 'null')\
            .execute()
        
        model_data = {
            "name": name,
            "version": version,
            "description": description,
            "organisation_id": org_id,
            "uploaded_by": user_id,
            "modified_by": user_id,
            "storage_path": storage_path,
            "file_size_bytes": file_size_bytes,
            "file_type": "manifest",
            "detection_capabilities": detection_capabilities
        }
        
        if existing.data:
            # Update existing model (version overwrite)
            model_id = existing.data[0]['id']
            response = supabase.table('ai_models')\
                .update(model_data)\
                .eq('id', model_id)\
                .execute()
            st.info(f"ℹ️ Overwriting existing model version")
        else:
            # Insert new model
            response = supabase.table('ai_models')\
                .insert(model_data)\
                .execute()
        
        if not response.data:
            raise Exception("Database operation did not return the expected model record.")
        
        st.success(f"✅ Model registered successfully")
        return response.data[0]
        
    except Exception as e:
        # Rollback: delete from storage
        try:
            supabase.storage.from_('ai-models').remove([storage_path])
        except Exception as rollback_e:
            # Log critical rollback failure
            st.error(f"⚠️ CRITICAL: Failed to rollback storage file {storage_path}. Error: {rollback_e}")
            st.warning(f"⚠️ Manual cleanup required: Please remove '{storage_path}' from the 'ai-models' bucket in Supabase.")

        # Re-raise with context
        raise Exception(f"Database registration failed: {str(e)}") from e


def upload_and_register_model(
    supabase: Client,
    manifest_bytes: bytes,
    model_name: str,
    model_version: str,
    description: str,
    labels: List[str],
    org_id: str
) -> bool:
    """
    Complete workflow: Upload to storage + Register in database
    Returns True on success, False on failure
    """
    user = st.session_state.user
    
    # 1. Check permissions
    if not check_user_role(supabase, user.id, org_id):
        st.error("❌ You don't have permission to upload models")
        st.info("💡 Required role: **organisation_manager** or **ww_admin**")
        return False
    
    # 2. Upload to storage
    with st.spinner("📤 Uploading to storage..."):
        try:
            storage_path = upload_model_to_storage(
                supabase=supabase,
                manifest_bytes=manifest_bytes,
                model_name=model_name,
                version=model_version,
                org_id=org_id
            )
            st.success(f"✅ Uploaded to storage: `{storage_path}`")
        except Exception as e:
            st.error(f"❌ {str(e)}")
            return False
    
    # 3. Register in database
    with st.spinner("💾 Registering model in database..."):
        try:
            model_record = register_model_in_db(
                supabase=supabase,
                name=model_name,
                version=model_version,
                description=description,
                org_id=org_id,
                user_id=user.id,
                storage_path=storage_path,
                file_size_bytes=len(manifest_bytes),
                detection_capabilities=labels
            )
            st.success(f"✅ Model registered with ID: `{model_record['id']}`")
            st.balloons()
            return True
        except Exception as e:
            st.error(f"❌ {str(e)}")
            return False

# --- Streamlit UI ---

st.set_page_config(layout="centered")

# Initialize Supabase client
supabase = init_supabase()

# Show authentication sidebar if Supabase is configured
if supabase:
    is_logged_in = render_login(supabase)
else:
    st.sidebar.warning("⚠️ Supabase not configured")
    st.sidebar.info("Set SUPABASE_URL and SUPABASE_ANON_KEY in .env file to enable upload")
    is_logged_in = False

st.image(
    "http://wildlife.ai/wp-content/uploads/2025/10/wildlife_ai_logo_dark_lightbackg_1772x591.png",
    width="stretch",
)
st.title("Edge Impulse Model Converter (Vela)")
st.markdown("""
Upload your Edge Impulse model zip file (e.g., `model-custom-v1.zip`).
This tool will:
1.  Unzip the file.
2.  Run the `vela` command (`ethos-u55-64`).
3.  Extract labels from `model_variables.h`.
4.  Package the converted `.tflite` and `labels.txt` into `ai_model.zip`.
""")

# Public MANIFEST Download Section (No Auth Required)
st.divider()
st.subheader("📦 Download Latest MANIFEST Package")
st.info("""
**Get everything you need for your camera device in one click!**

This package combines:
- ✅ Latest device configuration files (CONFIG.TXT, HMSTB1.BIN, etc.)
- ✅ Latest default AI wildlife detection model

Simply extract to your SD card root and insert into the camera device!
""")

col1, col2 = st.columns([1, 3])
with col1:
    if st.button("🚀 Download MANIFEST.zip", type="primary", width="stretch", disabled=not supabase):
        with st.spinner("Preparing MANIFEST package..."):
            manifest_bytes = create_manifest_package(supabase)
            if manifest_bytes:
                st.session_state['public_manifest_bytes'] = manifest_bytes
                st.success("✅ MANIFEST package ready!")
                st.rerun()

with col2:
    if 'public_manifest_bytes' in st.session_state:
        st.download_button(
            label="💾 Save MANIFEST.zip to your computer",
            data=st.session_state['public_manifest_bytes'],
            file_name="MANIFEST.zip",
            mime="application/zip",
            width="stretch"
        )
        if st.button("Clear", width="stretch"):
            del st.session_state['public_manifest_bytes']
            st.rerun()

if not supabase:
    st.warning("⚠️ Supabase not configured. Public download unavailable.")

st.divider()

with st.expander("License Information"):
    st.markdown("""
    This tool is provided under the GPL-3.0 license. The source code and license can be found on GitHub.
    """)

tab1, tab2 = st.tabs(["🔄 Convert & Upload", "📤 Direct Upload"])

with tab1:
    st.markdown("### Convert Edge Impulse Export")
    uploaded_file = st.file_uploader(
        "Choose your <modelname>-custom-<version>.zip",
        type="zip",
        accept_multiple_files=False,
        key="converter_uploader"
    )

    if uploaded_file is not None:
        if st.button(f"Convert {uploaded_file.name}", key="convert_btn"):
            
            zip_bytes = None
            try:
                with st.spinner("Running conversion pipeline... This may take a minute."):
                    # run_conversion now returns bytes of ai_model.zip
                    model_zip_bytes = run_conversion(uploaded_file)
                    
                    # Store in session state
                    if model_zip_bytes:
                        st.session_state['ai_model_zip_bytes'] = model_zip_bytes
                        
                        # Also parse and store model name/version for upload
                        model_name, model_version = parse_model_zip_name(uploaded_file.name)
                        st.session_state['model_name'] = model_name
                        st.session_state['model_version'] = model_version
                        if model_name == 'unknown':
                            st.warning("⚠️ Could not parse model metadata from filename. Please check the 'Upload' section below.")

            except Exception as e:
                st.error(f"An error occurred: {e}")

with tab2:
    st.markdown("### Upload Pre-converted Model")
    st.info("💡 Use this if you already have an `ai_model.zip` (containing the model and labels.txt).")
    
    direct_file = st.file_uploader(
        "Choose your ai_model.zip",
        type="zip",
        accept_multiple_files=False,
        key="direct_uploader"
    )
    
    if direct_file is not None:
        st.write(f"Ready to upload: {direct_file.name}")
        
        # User provides metadata
        col_m1, col_m2 = st.columns(2)
        with col_m1:
            m_name = st.text_input("Model Name", value="Person Detector", placeholder="e.g. My Custom Model")
        with col_m2:
            m_ver = st.text_input("Version", value="1.0.0", placeholder="e.g. 1.0.0")
            
        m_labels_raw = st.text_input("Detection Labels (comma-separated)", value="no_person, person", help="Enter the labels in order (index 0, index 1...)")
        
        if st.button("Prepare for Upload", key="prepare_direct_btn"):
            st.session_state['ai_model_zip_bytes'] = direct_file.getvalue()
            st.session_state['model_name'] = m_name
            st.session_state['model_version'] = m_ver
            st.session_state['labels'] = [l.strip() for l in m_labels_raw.split(',') if l.strip()]
            st.session_state['show_upload'] = True
            st.success("✅ Model data prepared! See the 'Upload to Database' section below.")

# Show download and upload options if conversion completed
if 'ai_model_zip_bytes' in st.session_state and st.session_state['ai_model_zip_bytes']:
    
    st.divider()
    st.subheader("✅ Conversion Complete!")
    
    # Extract labels from session state (from run_conversion)
    labels = st.session_state.get('labels', [])
    if labels:
        st.info(f"🏷️ Detected {len(labels)} classes: {', '.join(labels)}")
    
    # Always show download button
    col1, col2 = st.columns([1, 1])
    
    with col1:
        st.download_button(
            label="💾 Download ai_model.zip",
            data=st.session_state['ai_model_zip_bytes'],
            file_name="ai_model.zip",
            mime="application/zip",
            width="stretch"
        )
    
    # Show upload button if logged in
    if supabase and is_logged_in:
        with col2:
            st.button("📤 Upload to Database", key="upload_toggle", width="stretch", on_click=lambda: st.session_state.update({"show_upload": True}))
        
        # Show upload form if user clicked upload
        if st.session_state.get('show_upload', False):
            st.divider()
            st.subheader("📤 Upload to Wildlife Watcher")
            
            # Get user's organizations
            user_id = st.session_state.user.id
            orgs = get_user_organizations(supabase, user_id)
            
            if not orgs:
                st.warning("⚠️ You are not a member of any organization.")
                st.info("💡 Contact your organization manager to get added to an organization.")
            else:
                # Organization selector - handles duplicate names correctly
                selected_org = st.selectbox(
                    "Select Organization",
                    options=orgs,
                    format_func=lambda org: org['name'],
                    help="Choose which organization to upload this model to"
                )
                selected_org_id = selected_org['id']
                
                # Model description
                model_name = st.session_state.get('model_name', 'unknown')
                model_version = st.session_state.get('model_version', 'unknown')
                
                description = st.text_area(
                    "Model Description (optional)",
                    value=f"Converted Edge Impulse model - {model_name} v{model_version}",
                    help="Describe what this model detects and any important details"
                )
                
                # Upload button
                if st.button("🚀 Upload Model", type="primary", width="stretch"):
                    success = upload_and_register_model(
                        supabase=supabase,
                        manifest_bytes=st.session_state['ai_model_zip_bytes'],
                        model_name=model_name,
                        model_version=model_version,
                        description=description,
                        labels=labels,
                        org_id=selected_org_id
                    )
                    
                    if success:
                        #  Clear upload form
                        st.session_state['show_upload'] = False
    else:
        with col2:
            if supabase:
                st.info("🔒 Login to upload to database", icon="ℹ️")
            else:
                st.info("⚙️ Configure Supabase to enable upload", icon="ℹ️")
