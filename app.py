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
from typing import Optional, Dict, List, Tuple
from urllib.parse import urlparse
import json

import urllib.request

# Load environment variables
load_dotenv()

# --- Configuration Registry ---
MODEL_REGISTRY = {
    "Person Detection": {
        "resolutions": {
            "96x96": {
                "url": "https://raw.githubusercontent.com/wildlifeai/Seeed_Grove_Vision_AI_Module_V2/main/EPII_CM55M_APP_S/app/scenario_app/allon_sensor_tflm/person_detect_model_data_vela.cc",
                "type": "cc_array",
                "filename": "person_detect_model_data_vela.cc"
            }
        },
        "labels": ["no person", "person"]
    },

    "YOLOv8 Object Detection": {
        "resolutions": {
            "192x192": {
                "url": "https://raw.githubusercontent.com/wildlifeai/Seeed_Grove_Vision_AI_Module_V2/main/model_zoo/tflm_yolov8_od/yolov8n_od_192_delete_transpose_0xB7B000.tflite",
                "type": "tflite",
                "filename": "yolov8n_od_192.tflite"
            }
        },
        "labels": ["object"] 
    },
    "YOLOv11 Object Detection": {
        "resolutions": {
            "192x192": {
                "url": "https://raw.githubusercontent.com/wildlifeai/Seeed_Grove_Vision_AI_Module_V2/main/model_zoo/tflm_yolo11_od/yolo11n_full_integer_quant_192_241219_batch_matmul_vela.tflite",
                "type": "tflite",
                "filename": "yolo11n_od_192.tflite"
            },
            "224x224": {
                "url": "https://raw.githubusercontent.com/wildlifeai/Seeed_Grove_Vision_AI_Module_V2/main/model_zoo/tflm_yolo11_od/yolo11n_full_integer_quant_vela_imgz_224_kris_nopost_241230.tflite",
                "type": "tflite",
                "filename": "yolo11n_od_224.tflite"
            }
        },
        "labels": ["object"]
    },
    "YOLOv8 Pose Estimation": {
        "resolutions": {
            "256x256": {
                "url": "https://raw.githubusercontent.com/wildlifeai/Seeed_Grove_Vision_AI_Module_V2/main/model_zoo/tflm_yolov8_pose/yolov8n_pose_256_vela_3_9_0x3BB000.tflite",
                "type": "tflite",
                "filename": "yolov8n_pose_256.tflite"
            }
        },
        "labels": ["person_pose"]
    }
}

# Camera Configuration Registry
CAMERA_CONFIGS = {
    "Raspberry Pi": {
        "description": "Standard configuration (RPi v1/v2/v3)",
        "url": None, # Will fetch from DB 'latest' or use default placeholder if needed
        "filename": "CONFIG.TXT"
    },
    "HM0360": {
        "description": "Configuration for Himax HM0360 sensor",
        "url": "https://raw.githubusercontent.com/wildlifeai/Seeed_Grove_Vision_AI_Module_V2/main/_Tools/hm0360_md_medium.txt",
        "filename": "CONFIG.TXT" 
    }
}


# Constants
GENERAL_ORG_ID = 'b0000000-0000-0000-0000-000000000001'  # General organization from seed data

# Helper: Get configuration from secrets or environment
def get_config(key: str, default: Optional[str] = None) -> Optional[str]:
    """
    Retrieve config value from Streamlit secrets (priority) or environment variables.
    This function handles flat (top-level) keys.
    """
    # 1. Try Streamlit Secrets
    try:
        if key in st.secrets:
            return st.secrets[key]
        # Check for nested keys (e.g. SUPABASE_URL -> supabase.url is not automatic, 
        # but commonly secrets are grouped. We handle flat keys here to match env vars)
    except FileNotFoundError:
        pass # No secrets file
    
    # 2. Fallback to Environment Variables
    return os.environ.get(key, default)

# Initialize Supabase client
def create_supabase_client(privileged: bool = False) -> Optional[Client]:
    """Factory to create standard or privileged clients."""
    url = get_config("SUPABASE_URL")
    
    # Prefer Service Role Key for backend ops, fallback to Anon
    if privileged:
        key = get_config("SUPABASE_SERVICE_ROLE_KEY") or get_config("SUPABASE_ANON_KEY")
    else:
        key = get_config("SUPABASE_ANON_KEY")

    if not url or not key:
        return None
        
    if not url.endswith("/"):
        url += "/"
    else:
        # Warn if trailing slash is missing but valid otherwise? 
        # Actually client warns if it IS valid but has no slash. 
        # The library warning says "Storage endpoint URL should have a trailing slash"
        pass
        
    try:
        client = create_client(url, key)
        
        # If not using Service Role but need privilege, handle login (Legacy method)
        if privileged and not get_config("SUPABASE_SERVICE_ROLE_KEY"):
            email = get_config("UPLOADER_EMAIL")
            password = get_config("UPLOADER_PASSWORD")
            if email and password:
                client.auth.sign_in_with_password({"email": email, "password": password})
        
        # If standard client & logged in via UI session, restore session
        elif not privileged and 'session' in st.session_state and st.session_state.session:
             try:
                client.auth.set_session(
                    access_token=st.session_state.session.access_token,
                    refresh_token=st.session_state.session.refresh_token
                )
             except Exception as auth_e:
                st.warning(f"Failed to restore session: {auth_e}")
                
        return client
    except Exception as e:
        if privileged:
             st.warning(f"Privileged auth failed: {e}")
        else:
             st.error(f"Failed to initialize Supabase client: {str(e)}")
        return None


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



# Validated GitHub Model Registry
# Helper for Model Config
def get_model_config(model_type: str, resolution: str) -> dict:
    """Safe retrieval of model config."""
    try:
        return MODEL_REGISTRY[model_type]["resolutions"][resolution]
    except KeyError:
        raise ValueError(f"Configuration not found for {model_type} at {resolution}")








class DownloadError(Exception):
    pass

def download_url_content(url: str) -> bytes:
    """Download content from URL"""
    print(f"Downloading from {url}...")
    try:
        with urllib.request.urlopen(url) as response:
            return response.read() # Returns bytes
    except (urllib.error.HTTPError, urllib.error.URLError) as e:
        raise DownloadError(f"Error downloading file from {url}: {e}")

def extract_hex_array(c_content_str: str) -> bytes:
    """Parse C array and extract hex values from string content"""
    # Pattern: const unsigned char array_name[] = { 0xNN, 0xNN, ... };
    pattern = r'const\s+unsigned\s+char\s+\w+\[\]\s*=\s*\{([^}]+)\}'
    match = re.search(pattern, c_content_str, re.DOTALL)
    
    if not match:
        raise ValueError("Could not find byte array in C file")
    
    array_content = match.group(1)
    hex_values = re.findall(r'0x([0-9a-fA-F]{2})', array_content)
    
    if not hex_values:
        raise ValueError("No hex values found in array")
        
    return bytes([int(h, 16) for h in hex_values])

def process_github_model(model_type: str, resolution: str) -> tuple[Optional[bytes], List[str]]:
    """
    Downloads and packages a pre-trained model from GitHub.
    Returns (zip_bytes, labels_list) or (None, []) on failure.
    """
    try:
        # Use safe helper to get config
        config = get_model_config(model_type, resolution)
    except ValueError as e:
        st.error(str(e))
        return None, []

    labels = MODEL_REGISTRY[model_type].get("labels", ["unknown"])

    # Create temp dir
    with tempfile.TemporaryDirectory() as temp_dir:
        work_dir = Path(temp_dir)
        
        try:
            # 1. Download source
            content = download_url_content(config["url"])
            
            # 2. Convert if needed
            model_binary = None
            if config["type"] == "cc_array":
                # decode bytes to string for regex
                content_str = content.decode('utf-8')
                model_binary = extract_hex_array(content_str)
            else:
                model_binary = content
        except (DownloadError, ValueError) as e:
            st.error(f"Failed to process model: {e}")
            return None, []
        except Exception as e:
            st.error(f"Unexpected error: {e}")
            return None, []
            
        if not model_binary:
            st.error("Model binary is empty.")
            return None, []
            
        # 3. Save as .TFL
        tflite_path = work_dir / "trained_vela.TFL" # Standardize name
        with open(tflite_path, 'wb') as f:
            f.write(model_binary)
            
        # 4. Create labels.txt
        labels_txt_path = work_dir / "labels.txt"
        with open(labels_txt_path, 'w') as f:
            f.write('\n'.join(labels))
            
        # 5. Create ai_model.zip
        ai_model_zip_path = work_dir / "ai_model.zip"
        with zipfile.ZipFile(ai_model_zip_path, mode='w', compression=zipfile.ZIP_STORED) as zf:
            # Match the label filename to the model filename (e.g. model.TFL -> model.TXT)
            label_arcname = tflite_path.stem + ".TXT"
            zf.write(tflite_path, tflite_path.name)
            zf.write(labels_txt_path, label_arcname)
            
        # Read final bytes
        with open(ai_model_zip_path, 'rb') as f:
            return f.read(), labels

# --- SSCMA Zoo Integration ---
@st.cache_data(ttl=3600)
def fetch_sscma_models() -> List[Dict]:
    """Fetch and cache models from SSCMA Zoo."""
    url = "https://raw.githubusercontent.com/Seeed-Studio/sscma-model-zoo/main/models.json"
    try:
        content = download_url_content(url)
        data = json.loads(content)
        return data.get("models", [])
    except (DownloadError, json.JSONDecodeError) as e:
        st.error(f"Failed to fetch or parse SSCMA models: {e}")
        return []
    except Exception as e:
        st.error(f"Unexpected error fetching SSCMA models: {e}")
        return []

def process_sscma_model(model_entry: Dict) -> tuple[Optional[bytes], List[str]]:
    """Download best asset from SSCMA model entry."""
    benchmarks = model_entry.get("benchmark", [])
    best_asset = None
    target_type = "tflite" 
    
    # Define search preferences in order of priority
    preferences = [
        {'backend': 'TFLite(vela)', 'devices': ('we2', 'grove_vision_ai_we2'), 'target': 'vela'},
        {'backend': 'TFLite', 'precision': 'INT8', 'target': 'tflite'},
        {'backend': 'TFLite', 'precision': 'FLOAT32', 'target': 'tflite'},
    ]

    for pref in preferences:
        for b in benchmarks:
            if b.get('backend') == pref['backend']:
                if 'devices' in pref:
                    if any(d in b.get('device', []) for d in pref['devices']):
                        best_asset = b
                        target_type = pref['target']
                        break
                # Handle precision matching safely
                elif b.get('precision') == pref.get('precision'):
                    best_asset = b
                    target_type = pref['target']
                    break
        if best_asset:
            break
                
    if not best_asset:
        st.error("No compatible TFLite model found in SSCMA entry.")
        return None, []
        
    url = best_asset["url"]
    
    try:
        content = download_url_content(url)
        labels = model_entry.get("classes", ["unknown"])
        
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)
            
            if target_type == "vela":
                 tfl_path = work_dir / "trained_vela.TFL"
                 tfl_path.write_bytes(content)
            else:
                 # Need conversion
                 input_path = work_dir / "trained.tflite"
                 input_path.write_bytes(content)
                 
                 # Run Vela
                 cmd = [
                    'vela',
                    '--accelerator-config', 'ethos-u55-64',
                    '--memory-mode', 'Shared_Sram',
                    '--output-dir', str(work_dir),
                    str(input_path),
                ]
                 subprocess.run(cmd, capture_output=True, text=True, check=True)
                 
                 vela_out = find_vela_output(work_dir, input_path.name)
                 tfl_path = work_dir / "trained_vela.TFL"
                 safe_move(vela_out, tfl_path)

            labels_path = work_dir / "labels.txt"
            labels_path.write_text('\n'.join(labels))
                 
            zip_path = work_dir / "ai_model.zip"
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_STORED) as zf:
                 # Match the label filename to the model filename (e.g. model.TFL -> model.TXT)
                 label_arcname = tfl_path.stem + ".TXT"
                 zf.write(tfl_path, tfl_path.name)
                 zf.write(labels_path, label_arcname)
                 
            return zip_path.read_bytes(), labels

    except subprocess.CalledProcessError as e:
        st.error(f"Vela conversion failed (Return code: {e.returncode})")
        if e.stdout: st.code(e.stdout)
        if e.stderr: st.code(e.stderr)
        return None, []
    except Exception as e:
        st.error(f"Failed to process SSCMA model: {e}")
        return None, []

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
                _pid = str(int(_pid_m.group(1))) # normalize to no leading zeros
                _ver = str(int(_ver_m.group(1))) # normalize to no leading zeros
                # Firmware expects %dV%d format, no padding (e.g. 1V1.TFL)
                _base = f"{_pid}V{_ver}"
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
            # Ensure model has .TFL extension for firmware compatibility
            model_arcname = vela_final_path.stem + ".TFL"
            # Match the label filename to the model filename (e.g. model.TFL -> model.TXT)
            label_arcname = vela_final_path.stem + ".TXT"
            zf.write(vela_final_path, model_arcname)
            zf.write(labels_txt_path, label_arcname)

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
        # FIX: Use the passed client, do not call get_supabase()
        if not supabase:
            return None
            
        response = supabase.table('firmware')\
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
        # FIX: Use the passed client, do not call get_supabase()
        if not supabase:
            return None

        # First, try to find a Person Detector model (case-insensitive and partial match)
        response = supabase.table('ai_models')\
            .select('*')\
            .ilike('name', '%Person%Detector%')\
            .is_('deleted_at', 'null')\
            .order('created_at', desc=True)\
            .limit(1)\
            .execute()
        
        if response.data and len(response.data) > 0:
            return response.data[0]
            
        # Second try: Any model containing "Person"
        response = supabase.table('ai_models')\
            .select('*')\
            .ilike('name', '%Person%')\
            .is_('deleted_at', 'null')\
            .order('created_at', desc=True)\
            .limit(1)\
            .execute()
            
        if response.data and len(response.data) > 0:
            return response.data[0]
            
        # Fallback to the latest model available in the system
        response = supabase.table('ai_models')\
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
    Uses a simple two-step approach: SDK download → Public URL fallback.
    Returns True on success, False on failure.
    """
    try:
        # Step 1: Try standard SDK download
        # This works for authenticated users with proper permissions
        response = supabase.storage.from_(bucket).download(path)
        if response:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(response)
            return True
    except Exception as sdk_error:
        # Step 2: Fallback to Public URL
        # If the bucket is public, we can bypass RLS/Auth by using the public URL
        try:
            base_url = get_config("SUPABASE_URL")
            if base_url:
                if not base_url.endswith("/"):
                    base_url += "/"
                
                # Construct standard Supabase Storage public URL
                public_url = f"{base_url}storage/v1/object/public/{bucket}/{path}"
                
                # Use existing download helper
                content = download_url_content(public_url)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(content)
                return True
        except Exception as fallback_error:
            if not silent:
                st.error(f"Failed to download {path} from {bucket}: SDK failed ({sdk_error}), Public URL failed ({fallback_error})")
            return False
    
    # Should not reach here, but just in case
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


def create_manifest_package(default_client: Optional[Client]) -> Optional[bytes]:
    """
    Create a complete MANIFEST.zip package containing the latest config firmware
    and the latest default AI model.
    The package is structured for SD card deployment on a camera device.
    
    This function uses a Privileged Client (Service Account) to ensure it has
    read access to all necessary files in storage.
    Returns bytes of the final zip file, or None on failure.
    """
    # 1. Get Privileged Client
    supabase = create_supabase_client(privileged=True)
    if not supabase:
        st.error("❌ Failed to initialize privileged client for download.")
        return None

    temp_dir = None
    try:
        # Create temporary directory
        temp_dir = Path(tempfile.mkdtemp())
        manifest_dir = temp_dir / "MANIFEST"
        manifest_dir.mkdir()
        
        # 1. Fetch and download latest config firmware
        config_firmware = fetch_latest_config_firmware(supabase)
        config_found = False
        config_file_path = temp_dir / "config_component"

        if config_firmware:
            path = config_firmware['location_path']
            
            # Try primary path silently
            if download_from_storage(supabase, 'firmware', path, config_file_path, silent=True):
                if path.lower().endswith('.zip'):
                    with zipfile.ZipFile(config_file_path, 'r') as zip_ref:
                        zip_ref.extractall(manifest_dir)
                else:
                    filename = path.split('/')[-1]
                    shutil.copy2(config_file_path, manifest_dir / filename)
                st.success(f"✅ Added config firmware: {config_firmware.get('version', 'latest')}")
                config_found = True

        if not config_found:
            # FALLBACK Discovery (if DB record missing OR download failed)
            try:
                # Try to list files in the 'config' folder of the firmware bucket
                # Using 'sortBy' because 'order_by' might be implementation specific to the JS client logic vs Python
                files = supabase.storage.from_('firmware').list('config', {'sortBy': {'column': 'created_at', 'order': 'desc'}})
                
                # If that fails or returns empty, try without sort options (some mock/local servers can be picky)
                if not files:
                    files = supabase.storage.from_('firmware').list('config')
                    # Sort manually by name if needed, assuming standard naming
                    files.sort(key=lambda x: x.get('created_at', x.get('name')), reverse=True)

                if files:
                    # Filter out placeholders or folders
                    files = [f for f in files if f['name'] != '.emptyFolderPlaceholder' and not f['name'].endswith('/')]
                    
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
            except Exception as e:
                # Log the error for debugging purposes. The UI won't show an error since this is a fallback.
                print(f"Fallback config discovery failed: {e}")
        
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
    Render authentication section.
    Returns True if user is logged in, False otherwise.
    """
    st.subheader("🔐 Authentication")
    if 'user' in st.session_state and st.session_state.get('user'):
        user_email = st.session_state.user.email
        user_id = st.session_state.user.id
        st.success(f"✅ Logged in as:  \n**{user_email}**")
        
        # Diagnostic Info
        with st.expander("🔍 Account Diagnostics"):
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

        if st.button("Logout", use_container_width=True):
            supabase.auth.sign_out()
            st.session_state.clear()
            st.rerun()
        return True
    
    with st.form("login_form"):
        email = st.text_input("Email", placeholder="user@example.com")
        password = st.text_input("Password", type="password")
        submit = st.form_submit_button("Login", width="stretch")
        
        if submit:
            if not email or not password:
                st.error("Please enter both email and password")
                return False
            
            try:
                response = supabase.auth.sign_in_with_password({
                    "email": email,
                    "password": password
                })
                st.session_state.user = response.user
                st.session_state.session = response.session
                st.success("Login successful!")
                st.rerun()
            except AuthApiError as e:
                st.error(f"Login failed: {e}")
                return False
            except Exception as e:
                st.error(f"An unexpected error occurred: {e}")
                return False
    
    return False


def check_user_role(supabase: Client, user_id: str, org_id: str) -> bool:
    """
    Check if user has organisation_manager or ww_admin role.
    Returns True if authorized, False otherwise.
    """
    try:
        supabase_client = create_supabase_client()
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
        supabase_client = create_supabase_client()
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

# Configure page (Must be the first Streamlit command)
st.set_page_config(layout="centered", page_title="Wildlife Watcher Firmware Tool", page_icon="📸")

# Initialize Supabase client
supabase = create_supabase_client()

# --- Maintain Auth State ---
is_logged_in = False

# This ensures session persistence across reruns
if supabase and 'session' in st.session_state:
    try:
        current_user = supabase.auth.get_user()
        if not current_user:
            st.session_state.clear()
        else:
            is_logged_in = True
    except Exception:
        pass


# --- Main Header ---
# --- Main Header ---
col_logo, col_title = st.columns([1, 5])
with col_logo:
    st.image("icon.png", width="stretch")
with col_title:
    st.title("Wildlife Watcher Toolkit - Camera Firmware & AI Models")

st.divider()

# --- Mode Selection ---
# "Download firmware is selected as default"
mode = st.radio(
    "Select Action",
    ["⬇️ Download Firmware/Models", "☁️ Upload/Convert Model"],
    index=0,
    horizontal=True,
    label_visibility="collapsed" # Using custom headers instead or just letting the options speak
)

# Display specific description based on selection
if mode == "⬇️ Download Firmware/Models":
    st.info("Get the latest firmware and AI models for your camera device.")
else:
    st.info("Upload and convert your custom Edge Impulse models to the Wildlife Watcher cloud.")

st.divider()

# --- Journey 1: Download Firmware ---
if mode == "⬇️ Download Firmware/Models":
    
    col1, col2 = st.columns(2)
    
    # Step 1: Camera Configuration
    with col1:
        st.subheader("1. Camera Device")
        selected_camera = st.selectbox(
            "Select Camera Type",
            options=list(CAMERA_CONFIGS.keys()),
            help="Choose the camera sensor connected to your Grove Vision AI V2"
        )
        cam_config = CAMERA_CONFIGS[selected_camera]
        st.caption(f"ℹ️ {cam_config['description']}")

    # Step 2: AI Model Selection
    with col2:
        st.subheader("2. AI Model")
        model_source = st.radio(
            "Model Source",
            ["Pre-trained Model", "SenseCap Models", "My Organization Models", "No Model"],
            help="Select where to get the AI model from"
        )

    # Optional Auth for Organization Models
    if model_source == "My Organization Models":
        st.markdown("#### Organization Access")
        if not supabase:
             st.warning("⚠️ Supabase not configured")
        else:
             # Check if logged in
             is_logged_in = False
             if 'user' in st.session_state and st.session_state.user:
                 is_logged_in = True
             
             if not is_logged_in:
                 st.warning("Please login to access your organization's models.")
                 render_login(supabase)
             
             # Re-check login status after render_login (it might have just logged in)
             if 'user' in st.session_state and st.session_state.user:
                 is_logged_in = True

    st.divider()
    
    # Model Configuration Logic
    selected_model_bytes = None
    selected_model_name = "None"
    selected_model_data = None
    selected_sscma_entry = None
    
    # Logic for model fetch...
    can_generate = True
    
    if model_source == "Pre-trained Model":
        st.info("Select a model from the Grovevision repo")
        c1, c2 = st.columns(2)
        with c1:
            pt_type = st.selectbox("Model Architecture", list(MODEL_REGISTRY.keys()))
        with c2:
            pt_res = st.selectbox("Resolution", list(MODEL_REGISTRY[pt_type]["resolutions"].keys()))
            
        if st.checkbox(f"Include {pt_type} ({pt_res})", value=True):
             selected_model_name = f"{pt_type} ({pt_res})"
        else:
             can_generate = False # User unchecked it

    elif model_source == "SenseCap Models":
        st.info("Select a model from the Seeed Studio SSCMA Zoo.")
        sscma_models = fetch_sscma_models()
        if sscma_models:
             # Helper to get resolution string
             def get_res_str(m):
                 try:
                     shape = m.get('network', {}).get('input', {}).get('shape', [])
                     if len(shape) >= 2:
                         return f"{shape[0]}x{shape[1]}"
                 except (IndexError, TypeError):
                     pass
                 return "Unknown"

             # Group models by category for efficiency
             models_by_category = {}
             for m in sscma_models:
                 cat = m.get("category", "Unknown")
                 if cat not in models_by_category:
                     models_by_category[cat] = []
                 models_by_category[cat].append(m)

             categories = sorted(models_by_category.keys())
             sel_cat = st.selectbox("Category", categories)
             
             # Filter Models
             cat_models = models_by_category.get(sel_cat, [])
             
             sscma_selected = st.selectbox(
                 "Model", 
                 cat_models, 
                 format_func=lambda x: f"{x['name']} ({get_res_str(x)})"
             )
             
             if sscma_selected:
                 res_str = get_res_str(sscma_selected)
                 st.caption(f"**Resolution**: {res_str} | **Algorithm**: {sscma_selected.get('algorithm')}")
                 st.text(sscma_selected.get("description", ""))
                 st.text(f"Key Classes: {', '.join(sscma_selected.get('classes', [])[:5])}")
                 
                 selected_model_name = sscma_selected['name']
                 selected_sscma_entry = sscma_selected # Store for later
        else:
             st.warning("Could not load SenseCap models.")
             can_generate = False

    elif model_source == "My Organization Models":
        if is_logged_in:
             user_id = st.session_state.user.id
             orgs = get_user_organizations(supabase, user_id)
             if orgs:
                 sel_org = st.selectbox("Organization", orgs, format_func=lambda x: x['name'])
                 # Fetch models
                 try:
                     models_resp = supabase.table('ai_models').select('name, version, storage_path, description, id').eq('organisation_id', sel_org['id']).is_('deleted_at', 'null').execute()
                     if models_resp.data:
                         model_opts = {f"{m['name']} v{m['version']}": m for m in models_resp.data}
                         sel_model_key = st.selectbox("Select Model", list(model_opts.keys()))
                         selected_model_data = model_opts[sel_model_key]
                         selected_model_name = selected_model_data['name']
                     else:
                         st.warning("No models found for this organization.")
                         can_generate = False
                 except Exception as e:
                     st.error(f"Error fetching models: {e}")
             else:
                 st.warning("You are not part of any organization.")
                 can_generate = False
        else:
             can_generate = False

    # Step 3: Generation
    st.subheader("3. Generate Package")

    # NEW: Model Versioning Inputs
    if selected_camera in CAMERA_CONFIGS:
        # Camera Config Description
        st.info(f"**Camera Config**: {CAMERA_CONFIGS[selected_camera]['description']}")

        st.markdown("##### 🔢 Model Versioning")
        st.markdown("Define the Model ID and Version matching your model. The firmware will use these to load the correct file.")
        # Default to 1, or use selected model's DB ID/Version if available
        default_pid = 1
        default_ver = 1
        if selected_model_data:
             default_pid = selected_model_data.get('id', 1)
             default_ver = selected_model_data.get('version', 1)

        mv_col1, mv_col2 = st.columns(2)
        with mv_col1:
            model_id = st.number_input("Model ID (OP 14)", min_value=1, value=default_pid, step=1, help="Corresponds to OP_PARAMETER_MODEL_PROJECT (internally Project ID). This matches the Database Model ID.", key=f"pid_{selected_model_name}")
        with mv_col2:
            model_version = st.number_input("Version (OP 15)", min_value=1, value=default_ver, step=1, help="Corresponds to OP_PARAMETER_MODEL_VERSION", key=f"ver_{selected_model_name}")

        target_model_filename = f"{model_id}V{model_version}.TFL"
        st.caption(f"Target Filename: `{target_model_filename}`")

    if st.button("🚀 Generate MANIFEST.zip", type="primary", disabled=not can_generate):
        with st.spinner("Assembling firmware package..."):
            try:
                with tempfile.TemporaryDirectory() as temp_dir:
                    base_dir = Path(temp_dir)
                    manifest_dir = base_dir / "MANIFEST"
                    manifest_dir.mkdir()

                    # --- A. Base Configuration (Bootloader + Defaults) ---
                    # Always fetch the base firmware package first
                    # Use privileged client to ensure access to firmware bucket
                    priv_client = create_supabase_client(privileged=True)
                    target_client = priv_client if priv_client else supabase

                    latest_fw = fetch_latest_config_firmware(target_client)
                    if not latest_fw:
                        # Fallback for offline dev: create basic structure if DB fails
                         st.warning("Could not fetch base firmware from DB. Creating minimal config.")
                         with open(manifest_dir / "CONFIG.TXT", "w") as f:
                             f.write("# Minimal Config\n")
                    else:
                        # Download and unzip base package
                        base_zip_path = base_dir / "base_config.zip"
                        # DEBUG: Remove silent=True to see errors
                        if download_from_storage(target_client, 'firmware', latest_fw['location_path'], base_zip_path, silent=False):
                            with zipfile.ZipFile(base_zip_path, 'r') as z:
                                z.extractall(manifest_dir)

                        # UPDATE CONFIG.TXT with Project ID and Version
                        config_path = manifest_dir / "CONFIG.TXT"
                        if config_path.exists():
                            # Append or Update? Simple append works if firmware reads last value or we just rely on it not being there.
                            # But safer to read and rewrite if we want to be clean.
                            # For now, simplistic append is likely fine as defaults are usually commented out or minimal.
                            # Let's read, filter existing 14/15, and append new.
                            existing_lines = config_path.read_text().splitlines()
                            new_lines = [l for l in existing_lines if not (l.strip().startswith("14 ") or l.strip().startswith("15 "))]
                            new_lines.append(f"14 {model_id}")
                            new_lines.append(f"15 {model_version}")
                            
                            # Sort lines numerically by the first integer ID.
                            # Comments (starting with #) should come first.
                            def config_sort_key(line):
                                line = line.strip()
                                if line.startswith("#"):
                                    return (-1, 0)
                                parts = line.split()
                                if parts and parts[0].isdigit():
                                    return (0, int(parts[0]))
                                return (1, 0) # Fallback for non-standard lines
                                
                            new_lines.sort(key=config_sort_key)
                            config_path.write_text("\n".join(new_lines) + "\n")
                        else:
                            # Create if missing
                            config_path.write_text(f"14 {model_id}\n15 {model_version}\n")

                    # --- B. Camera Specific Overrides ---
                    # If specific camera config URL exists, download and overwrite CONFIG.TXT
                    if cam_config['url']:
                        extra_config = download_url_content(cam_config['url'])
                        if extra_config:
                            # If it's the Himax HM0360, it might need the binary too.
                            # Current URL is just text. We assume the Base Package (step A) might contain
                            # shared binaries, or we just rely on this text file.
                            # Write/Overwrite CONFIG.TXT
                            with open(manifest_dir / "CONFIG.TXT", "wb") as f:
                                f.write(extra_config)
                            
                            # Special case: HM0360 might likely need a specific line in CONFIG.TXT or an external bin
                            # For now, we trust the downloaded text file is the complete CONFIG.TXT replacement.

                    # --- C. AI Model Integration ---
                    model_zip_bytes = None
                    if model_source == "Pre-trained Model":
                         model_zip_bytes, _ = process_github_model(pt_type, pt_res)

                    elif model_source == "SenseCap Models":
                         if selected_sscma_entry:
                             model_zip_bytes, _ = process_sscma_model(selected_sscma_entry)
                         
                    elif model_source == "My Organization Models" and is_logged_in and selected_model_data:
                         mz_path = base_dir / "model_temp.zip"
                         if download_from_storage(supabase, 'ai-models', selected_model_data['storage_path'], mz_path, silent=False):
                                model_zip_bytes = mz_path.read_bytes()
                    
                    if model_zip_bytes:
                        # Extract model zip into MANIFEST
                        # We need to handle if model zip contains "MANIFEST" folder or just files
                        # Logic: Write bytes to temp zip, then extract
                        m_zip_path = base_dir / "model_insert.zip"
                        m_zip_path.write_bytes(model_zip_bytes)
                        
                        with zipfile.ZipFile(m_zip_path, 'r') as z:
                            # Check structure
                            has_manifest_folder = any(n.startswith('MANIFEST/') for n in z.namelist())
                            if has_manifest_folder:
                                # Extract fully, merging folders
                                z.extractall(base_dir) 
                            else:
                                # Extract files directly into MANIFEST
                                z.extractall(manifest_dir)

                        # POST-PROCESS: Rename model to {Project}V{Version}.TFL
                        # Firmware strictly requires this pattern now.
                        # Find ANY .TFL or .tflite and rename it.
                        model_candidates = list(manifest_dir.glob('*.TFL')) + list(manifest_dir.glob('*.tflite'))
                        # Remove duplicates if any
                        model_candidates = list(set(model_candidates))
                        
                        if model_candidates:
                            # Take the first one found (assuming only one model per package)
                            src_model = model_candidates[0]
                            dest_model = manifest_dir / target_model_filename
                            
                            # Rename Model
                            if src_model != dest_model:
                                src_model.rename(dest_model)
                                st.write(f"Renamed model: {src_model.name} -> {dest_model.name}")
                                # If there were others, delete them to avoid confusion?
                                for extra in model_candidates[1:]:
                                    os.remove(extra)
                            
                            # Rename Label File to match model (e.g. 1V1.TXT)
                            # 1. Try finding file with same stem as original model (e.g. trained_vela.TXT)
                            # 2. Try 'labels.txt'
                            # 3. Try 'trained.txt'
                            
                            src_label_candidates = [
                                src_model.with_suffix(".TXT"),
                                src_model.with_suffix(".txt"),
                                manifest_dir / "labels.txt",
                                manifest_dir / "labels.TXT",
                                manifest_dir / "trained.txt",
                                manifest_dir / "trained.TXT"
                            ]
                            
                            found_label = None
                            for cand in src_label_candidates:
                                if cand.exists():
                                    found_label = cand
                                    break
                            
                            if found_label:
                                dest_label = dest_model.with_suffix(".TXT")
                                if found_label != dest_label:
                                    found_label.rename(dest_label)
                                    st.write(f"Renamed labels: {found_label.name} -> {dest_label.name}")
                            else:
                                st.warning("No matching label file found to rename. Ensure labels are named matching the model or 'labels.txt'.")

                        else:
                            if model_source != "None":
                                st.warning(f"No model file found to rename to {target_model_filename}")

                    # --- D. Final Packaging ---
                    final_zip_path = base_dir / "MANIFEST_final.zip"
                    with zipfile.ZipFile(final_zip_path, 'w', zipfile.ZIP_STORED) as zf:
                        # Walk MANIFEST directory and zip it up, ensuring 'MANIFEST/' prefix
                        for root, dirs, files in os.walk(manifest_dir):
                            for file in files:
                                file_path = Path(root) / file
                                arcname = f"MANIFEST/{file_path.relative_to(manifest_dir)}"
                                zf.write(file_path, arcname)
                    
                    final_bytes = final_zip_path.read_bytes()
                    
                    st.session_state['ready_manifest'] = final_bytes
                    st.session_state['generated_manifest_name'] = "MANIFEST.zip"
                    st.success("✅ Package ready! (Model & Labels renamed for firmware compatibility)")
                    st.rerun()

            except Exception as e:
                st.error(f"Error generating package: {e}")
    
    if 'ready_manifest' in st.session_state:
        st.download_button(
            label="💾 Download MANIFEST.zip",
            data=st.session_state['ready_manifest'],
            file_name="MANIFEST.zip",
            mime="application/zip",
            type="primary"
        )


# --- Journey 2: Upload Model ---
elif mode == "☁️ Upload/Convert Model":
    
    if 'upload_success_message' in st.session_state:
         st.success(st.session_state['upload_success_message'])
         del st.session_state['upload_success_message']

    if not supabase:
        st.error("Supabase not configured. Cannot upload models.")
    else: 
        # Check login
        is_logged_in = False
        if 'user' in st.session_state and st.session_state.user:
            is_logged_in = True
            
        if not is_logged_in:
             st.markdown("### Authentication Required")
             st.info("Please login to upload models to the Wildlife Watcher cloud.")
             render_login(supabase)
        else:
             render_login(supabase)
             st.divider()

             st.markdown("### Upload Custom Model")
             st.info("💡 Supports Edge Impulse C++ Library exports (ZIP).")
             
             uploaded_file = st.file_uploader(
                 "Upload Model (ZIP)",
                 type="zip",
                 help="Upload the .zip file exported from Edge Impulse"
             )
             
             enable_conversion = st.checkbox("Convert with Vela", value=True, help="Optimize model for Ethos-U55 NPU (Required for Vision AI V2)")
             
             custom_labels_input = "unknown"
             if not enable_conversion:
                 custom_labels_input = st.text_input("Enter labels (comma-separated)", value="unknown", help="Example: person, cat, dog")
             
             if uploaded_file:
                 c1, c2 = st.columns(2)
                 with c1:
                     val_name, val_ver = parse_model_zip_name(uploaded_file.name)
                     m_name = st.text_input("Model Name", value=val_name)
                 with c2:
                     m_ver = st.text_input("Version", value=val_ver)
                     
                 m_desc = st.text_area("Description", value=f"Converted from {uploaded_file.name}")
                 
                 user_id = st.session_state.user.id
                 orgs = get_user_organizations(supabase, user_id)
                 
                 if orgs:
                     tgt_org = st.selectbox("Target Organization", orgs, format_func=lambda x: x['name'])
                     
                     # Step 1: Process the model
                     if st.button("🔄 Process Model", type="primary"):
                         with st.spinner("Processing model..."):
                             final_zip_bytes = None
                             labels = []
                             
                             if enable_conversion:
                                 final_zip_bytes = run_conversion(uploaded_file)
                                 labels = st.session_state.get('labels', [])
                             else:
                                 final_zip_bytes = uploaded_file.getvalue()
                                 labels = [l.strip() for l in custom_labels_input.split(",") if l.strip()]
                             
                             if final_zip_bytes:
                                 # Store processed model in session state
                                 st.session_state['processed_model'] = {
                                     'bytes': final_zip_bytes,
                                     'name': m_name,
                                     'version': m_ver,
                                     'description': m_desc,
                                     'org_id': tgt_org['id'],
                                     'labels': labels
                                 }
                                 st.session_state['upload_success_message'] = None # Reset
                                 st.rerun()
                     
                     # Step 2: Show upload and download options if model is processed
                     if 'processed_model' in st.session_state:
                         st.divider()
                         st.markdown("#### 📦 Processed Model Ready")
                         
                         proc_data = st.session_state['processed_model']
                         st.info(f"**Model**: {proc_data['name']} v{proc_data['version']}")
                         
                         col_upload, col_download = st.columns(2)
                         
                         # Option 2a: Upload to Cloud
                         with col_upload:
                             if st.button("☁️ Upload to Cloud", type="primary", width="stretch"):
                                 with st.spinner("Uploading to cloud..."):
                                     upload_and_register_model(
                                         supabase=supabase,
                                         manifest_bytes=proc_data['bytes'],
                                         model_name=proc_data['name'],
                                         model_version=proc_data['version'],
                                         description=proc_data['description'],
                                         labels=proc_data['labels'],
                                         org_id=proc_data['org_id']
                                     )
                                     # Clear processed model after successful upload
                                     st.session_state['upload_success_message'] = f"✅ Model '{proc_data['name']} v{proc_data['version']}' uploaded successfully!"
                                     del st.session_state['processed_model']
                                     st.rerun()
                         
                         # Option 2b: Download locally
                         with col_download:
                             st.download_button(
                                 label="💾 Download Locally",
                                 data=proc_data['bytes'],
                                 file_name=f"{proc_data['name']}_v{proc_data['version']}.zip",
                                 mime="application/zip",
                                 type="secondary",
                                 width="stretch"
                             )
                         
                         # Option to clear and start over
                         if st.button("🔄 Process Another Model", width="stretch"):
                             del st.session_state['processed_model']
                             st.rerun()
                 else:
                     st.error("You must belong to an organization to upload.")

