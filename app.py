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
from typing import Optional, Dict, List

# Load environment variables
load_dotenv()

# Initialize Supabase client
@st.cache_resource
def init_supabase() -> Optional[Client]:
    """Initialize Supabase client with caching"""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_ANON_KEY")
    
    if not url or not key:
        return None
    
    return create_client(url, key)

# --- Helper Functions from your Notebook ---

def parse_model_zip_name(zip_path: str):
    """Parse '<modelname>-custom-<version>.zip' -> (modelname, version)"""
    name = os.path.basename(zip_path)
    if not name.endswith('.zip'):
        raise ValueError('Zip file must end with .zip')
    base = name[:-4]
    if '-custom-' not in base:
        raise ValueError("Filename must contain '-custom-' (e.g. mymodel-custom-v10.zip)")
    modelname, version = base.split('-custom-', 1)
    if not modelname or not version:
        raise ValueError('Invalid filename segments before/after -custom-')
    return modelname, version

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

        # 5. Package Manifest
        manifest_dir = work_dir / 'Manifest'
        manifest_dir.mkdir(parents=True, exist_ok=True)

        shutil.copy2(vela_final_path, manifest_dir / vela_final_path.name)
        shutil.copy2(labels_txt_path, manifest_dir / 'labels.txt')

        # Zip Manifest using STORE (no compression)
        manifest_dir_path = work_dir / 'Manifest'
        manifest_zip_path = work_dir / 'Manifest.zip'
        # Create a store-compressed zip so files are stored without deflate
        with zipfile.ZipFile(manifest_zip_path, mode='w', compression=zipfile.ZIP_STORED) as zf:
            for root, _, files in os.walk(manifest_dir_path):
                for fname in files:
                    full_path = Path(root) / fname
                    # write with arcname relative to Manifest directory
                    arcname = full_path.relative_to(work_dir)
                    zf.write(full_path, arcname)

        final_zip_path = manifest_zip_path
        if not final_zip_path.exists():
            raise FileNotFoundError(f"Failed to create Manifest.zip at {final_zip_path}")

        # Read bytes while tempdir is still valid and return them to caller
        with open(final_zip_path, 'rb') as f:
            manifest_bytes = f.read()

        st.success("Manifest.zip created successfully!")
        return manifest_bytes

# --- Supabase Integration Functions ---

def render_login(supabase: Client) -> bool:
    """
    Render authentication sidebar.
    Returns True if user is logged in, False otherwise.
    """
    st.sidebar.title("🔐 Authentication")
    
    if 'user' in st.session_state and st.session_state.get('user'):
        user_email = st.session_state.user.email
        st.sidebar.success(f"✅ Logged in as:  \n**{user_email}**")
        
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
            except Exception as e:
                # Catch auth-specific errors (gotrue.errors.AuthApiError)
                # Using Exception to handle both auth errors and network issues
                st.sidebar.error(f"Login failed: {str(e)}")
                return False
    
    return False


def check_user_role(supabase: Client, user_id: str, org_id: str) -> bool:
    """
    Check if user has organisation_manager or ww_admin role.
    Returns True if authorized, False otherwise.
    """
    try:
        # Validate org_id to prevent injection vulnerabilities
        if not re.match(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", org_id):
            st.error("❌ Invalid organization ID format.")
            return False
        
        # Optimized: Check for ww_admin OR organisation_manager in a single query
        # This combines both role checks into one database call
        response = supabase.table('user_roles')\
            .select('role')\
            .eq('user_id', user_id)\
            .eq('is_active', True)\
            .is_('deleted_at', 'null')\
            .or_(f'and(role.eq.ww_admin,scope_type.eq.system),and(role.eq.organisation_manager,scope_type.eq.organisation,scope_id.eq.{org_id})')\
            .execute()
        
        return bool(response.data)
        
    except Exception as e:
        st.error(f"❌ Role check failed: {str(e)}")
        return False


def get_user_organizations(supabase: Client, user_id: str) -> Dict[str, str]:
    """
    Fetch organizations the user belongs to.
    Returns dict mapping org name to org ID.
    """
    try:
        # Get user roles for organisations
        response = supabase.table('user_roles')\
            .select('scope_id, organisations:scope_id(id, name)')\
            .eq('user_id', user_id)\
            .eq('scope_type', 'organisation')\
            .eq('is_active', True)\
            .is_('deleted_at', 'null')\
            .execute()
        
        orgs = {
            role['organisations']['name']: role['organisations']['id']
            for role in response.data if role.get('organisations')
        }
        return orgs
        
    except Exception as e:
        st.error(f"❌ Failed to fetch organizations: {str(e)}")
        return {}


def upload_model_to_storage(
    supabase: Client,
    manifest_bytes: bytes,
    model_name: str,
    version: str,
    org_id: str
) -> str:
    """
    Upload Manifest.zip to Supabase Storage.
    Storage path: <org_id>/<model_name>-custom-<version>/Manifest.zip
    Returns: storage_path
    """
    # Generate storage path following the naming convention
    storage_path = f"{org_id}/{model_name}-custom-{version}/Manifest.zip"
    
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
            "description": description or f"Converted Edge Impulse model - {name} v{version}",
            "organisation_id": org_id,
            "uploaded_by": user_id,
            "modified_by": user_id,
            "storage_path": storage_path,
            "file_size_bytes": file_size_bytes,
            "file_type": "manifest",
            "detection_capabilities": detection_capabilities
        }
        
        if existing.data and len(existing.data) > 0:
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
        return response.data[0]
        
    except Exception as e:
        # Rollback: delete from storage
        try:
            supabase.storage.from_('ai-models').remove([storage_path])
        except Exception as rollback_e:
            # Log critical rollback failure
            st.error(f"⚠️ CRITICAL: Failed to rollback storage file {storage_path}. Error: {rollback_e}")
            st.warning(f"⚠️ Manual cleanup required: Please remove '{storage_path}' from the 'ai-models' bucket in Supabase.")
        raise Exception(f"Database registration failed: {str(e)}")


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
    use_container_width=True,
)
st.title("Edge Impulse Model Converter (Vela)")
st.markdown("""
Upload your Edge Impulse model zip file (e.g., `model-custom-v1.zip`).
This tool will:
1.  Unzip the file.
2.  Run the `vela` command (`ethos-u55-64`).
3.  Extract labels from `model_variables.h`.
4.  Package the converted `.tflite` and `labels.txt` into `Manifest.zip`.
""")

with st.expander("License Information"):
    st.markdown("""
    This tool is provided under the GPL-3.0 license. The source code and license can be found on GitHub.
    """)

uploaded_file = st.file_uploader(
    "Choose your <modelname>-custom-<version>.zip",
    type="zip",
    accept_multiple_files=False
)

if uploaded_file is not None:
    if st.button(f"Convert {uploaded_file.name}"):
        
        zip_bytes = None
        try:
            with st.spinner("Running conversion pipeline... This may take a minute."):
                # run_conversion now returns bytes
                zip_bytes = run_conversion(uploaded_file)
                
                # Store in session state
                if zip_bytes:
                    st.session_state['manifest_bytes'] = zip_bytes
                    
                    # Also parse and store model name/version for upload
                    try:
                        model_name, model_version = parse_model_zip_name(uploaded_file.name)
                        st.session_state['model_name'] = model_name
                        st.session_state['model_version'] = model_version
                    except ValueError as e:
                        st.warning(f"⚠️ Could not parse model name/version from filename: {e}")
                        st.info("💡 Using default values. You can edit the description when uploading.")
                        # Set defaults so the app doesn't crash, but the user is aware
                        st.session_state['model_name'] = 'unknown'
                        st.session_state['model_version'] = 'unknown'

        except Exception as e:
            st.error(f"An error occurred: {e}")

# Show download and upload options if conversion completed
if 'manifest_bytes' in st.session_state and st.session_state['manifest_bytes']:
    
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
            label="💾 Download Manifest.zip",
            data=st.session_state['manifest_bytes'],
            file_name="Manifest.zip",
            mime="application/zip",
            use_container_width=True
        )
    
    # Show upload button if logged in
    if supabase and is_logged_in:
        with col2:
            st.button("📤 Upload to Database", key="upload_toggle", use_container_width=True, on_click=lambda: st.session_state.update({"show_upload": True}))
        
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
                # Organization selector
                selected_org_name = st.selectbox(
                    "Select Organization",
                    options=list(orgs.keys()),
                    help="Choose which organization to upload this model to"
                )
                selected_org_id = orgs[selected_org_name]
                
                # Model description
                model_name = st.session_state.get('model_name', 'unknown')
                model_version = st.session_state.get('model_version', 'unknown')
                
                description = st.text_area(
                    "Model Description (optional)",
                    value=f"Converted Edge Impulse model - {model_name} v{model_version}",
                    help="Describe what this model detects and any important details"
                )
                
                # Upload button
                if st.button("🚀 Upload Model", type="primary", use_container_width=True):
                    success = upload_and_register_model(
                        supabase=supabase,
                        manifest_bytes=st.session_state['manifest_bytes'],
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
