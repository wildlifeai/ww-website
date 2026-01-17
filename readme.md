<p align="center">
  <a href="https://wildlife.ai/">
    <img src="https://wildlife.ai/wp-content/uploads/2025/10/wildlife_ai_logo_dark_lightbackg_1772x591.png" alt="Wildlife.ai Logo" width="400">
  </a>
</p>

<h1 align="center">
Wildlife Watcher Toolkit - Camera Firmware & AI Models
</h1>

<p align="center">
  <strong>Convert Edge Impulse models and download complete MANIFEST packages for Wildlife Watcher devices.</strong>
  <br />
  <a href="https://wildlifewatcher.streamlit.app" target="_blank">
    <img src="https://static.streamlit.io/badges/streamlit_badge_black_white.svg" alt="Streamlit App" />
  </a>
</p>

## 🚀 Features

The app has two main modes to support different workflows:

### ⬇️ Download Firmware/Models Mode

**Download complete packages for your Wildlife Watcher camera device:**
- **Pre-trained Models**: Select from curated models (Person Detection, YOLOv11, etc.)
- **Organization Models**: Access custom models from your organization (login required)
- **No Model**: Download firmware only without AI model
- **Camera Configuration**: Choose between Raspberry Pi or HM0360 sensor configurations
- **Model Versioning**: Set Project ID and Version numbers for firmware compatibility
- **One-click Download**: Get ready-to-use MANIFEST.zip packages

### ☁️ Upload/Convert Model Mode

**Process and deploy your custom Edge Impulse models:**

#### Step 1: Process Model
- Upload Edge Impulse C++ Library export (ZIP)
- Optional Vela compiler optimization (`ethos-u55-64`)
- Extract labels from `model_variables.h`
- Convert to Wildlife Watcher format

#### Step 2: Upload or Download
- **Upload to Cloud**: Deploy to your organization for mobile app access
- **Download Locally**: Save the processed model to your computer
- Both options available after processing completes

**Key Benefits:**
- Role-based access control (`organisation_manager` or `ww_admin`)
- Automatic versioning and metadata management
- Flexible deployment options

## 🎯 Usage Guide

### Mode 1: Download Firmware/Models

**For getting pre-configured packages for your camera:**

1. **Select Camera Device**: Choose your sensor (Raspberry Pi or HM0360)
2. **Choose Model Source**:
   - **Pre-trained Model**: Select architecture (Person Detection, YOLOv11, etc.) and resolution
   - **My Organization Models**: Login and select from your organization's models
   - **No Model**: Skip AI model inclusion
3. **Set Model Versioning** (if model selected):
   - **Project ID (OP 14)**: Corresponds to firmware parameter
   - **Version (OP 15)**: Model version number
   - Creates filename: `{ProjectID}V{Version}.TFL`
4. **Generate & Download**: Click "🚀 Generate MANIFEST.zip"
5. **Deploy**: Extract to SD card root and insert into camera device

### Mode 2: Upload/Convert Model

**For deploying your custom trained models:**

#### Prerequisites
- Edge Impulse C++ Library export (ZIP file)
- Wildlife Watcher account with organization access
- `organisation_manager` or `ww_admin` role

#### Steps

**Step 1: Upload & Configure**
1. **Login** using the interface
2. **Upload Model**: Select your Edge Impulse ZIP file
3. **Enable Conversion**: Check "Convert with Vela" (recommended for Vision AI V2)
4. **Set Metadata**:
   - Model Name (auto-extracted from filename)
   - Version number
   - Description
5. **Select Organization**: Choose target organization

**Step 2: Process**
1. Click "🔄 Process Model"
2. Wait for conversion to complete
3. Success message appears when ready

**Step 3: Deploy**
- **Option A**: Click "☁️ Upload to Cloud" to deploy to your organization
- **Option B**: Click "💾 Download Locally" to save to your computer
- **Start Over**: Click "🔄 Process Another Model" to begin again

The uploaded model will be immediately available in the Wildlife Watcher mobile app for your organization members.


## 💻 Local Development

### Prerequisites
- Python 3.9+
- pip

### Setup
1. **Clone the repository:**
   ```bash
   git clone https://github.com/wildlifeai/wildlife-watcher-model-conversion.git
   cd wildlife-watcher-model-conversion
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment variables:**
   Create a `.env` file in the root directory (do NOT commit this file):
   ```env
   SUPABASE_URL=https://your-project.supabase.co/
   SUPABASE_ANON_KEY=your-anon-key-here
   ```
   > **Note:** Ensure `SUPABASE_URL` has a trailing slash to avoid client warnings.

4. **Run the app:**
   ```bash
   streamlit run app.py
   ```
   The application will open at `http://localhost:8501`.

### Verification Scripts
We provide helper scripts to verify database connectivity and RLS policies locally:

- `check_db_status.py`: Checks if Config Firmware and AI Models exist in the DB (using your `.env` keys).
- `check_anon_access.py`: Simulates a public user (ANON key) to verify RLS policies allow reading data.

Run them via:
```bash
python check_db_status.py
```


## 🔧 Tech Stack
- **Frontend:** Streamlit
- **Backend:** Supabase (PostgreSQL + Storage)
- **ML Compiler:** Ethos-U Vela
- **Deployment:** Streamlit Community Cloud

## 🧩 How It Works: Manifest Generation

The **public MANIFEST.zip download** feature dynamically assembles the package on-the-fly:

1.  **Config Firmware**: Fetches the latest active firmware record of type `config` from Supabase.
2.  **AI Model**: 
    - **Priority**: Searches for a model named **"Person Detector"** in the General organization.
    - **Fallback**: Uses the latest available AI model if the above is not found.
3.  **Structure**: 
    - Downloads and extracts components into a temporary `MANIFEST/` directory.
    - **Flattens** the structure so all files are at the root level of the folder.
    - Packages the result into an **uncompressed** `MANIFEST.zip` (method 0).

> [!NOTE]
> If either the Config Firmware or the Default AI Model is missing in the database, the app will warn the user and skip including that component in the final zip.

## 📁 File Structure
- `app.py` - Main Streamlit application
- `requirements.txt` - Python dependencies
- `.env` - Supabase credentials (local only)
- `readme.md` - This file

---

## 🔄 Automated Model Deployment (Database Seeding)

The `scripts/` directory contains automation for deploying the baseline person detection model to Supabase. This is primarily used for **initial database setup**, not routine model uploads.

### Scripts
- **`convert_github_model.py`** - Extracts TFLite model from GitHub C source
- **`deploy_models.py`** - Uploads model to Supabase and registers in database

### CI/CD Workflow
The `.github/workflows/deploy-models.yml` workflow runs during database initialization to:
1. Convert the person detection model from [Seeed Grove Vision AI source](https://github.com/wildlifeai/Seeed_Grove_Vision_AI_Module_V2)
2. Deploy it as the default baseline model for new database instances

**Model Details:**
- **Labels:** `person`, `no person`
- **Size:** ~251KB
- **Version:** 1.0.0

**When to use:** This workflow is part of the database seeding process. For ongoing model management, users should use the Streamlit web interface above.

**Required CI Secrets:**
- `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `GENERAL_ORG_ID`
- `UPLOADER_EMAIL` (set to `apps@wildlife.ai`)
- `UPLOADER_PASSWORD`

---

## 👥 Contributors
- Tobyn Packer
- Victor Anton

## 📜 License
This project is licensed under the **GPL-3.0 License** - see the `LICENSE` file for details.