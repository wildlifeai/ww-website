<p align="center">
  <a href="https://wildlife.ai/">
    <img src="https://wildlife.ai/wp-content/uploads/2025/10/wildlife_ai_logo_dark_lightbackg_1772x591.png" alt="Wildlife.ai Logo" width="400">
  </a>
</p>

<h1 align="center">
Wildlife Watcher Model Converter & Upload Tool
</h1>

<p align="center">
  <strong>Convert Edge Impulse models and download complete MANIFEST packages for Wildlife Watcher devices.</strong>
  <br />
  <a href="https://wildlifewatcher.streamlit.app" target="_blank">
    <img src="https://static.streamlit.io/badges/streamlit_badge_black_white.svg" alt="Streamlit App" />
  </a>
</p>

## 🚀 Features

### 📦 Public MANIFEST Download (No Login Required)
- **One-click download** of complete camera device package
- Automatically combines:
  - Latest config firmware (CONFIG.TXT, HMSTB1.BIN, etc.)
  - Latest default AI wildlife detection model
- Ready to extract to SD card and use immediately

### 🔄 Model Conversion
- Convert Edge Impulse models using Vela compiler (`ethos-u55-64`)
- Extract labels from `model_variables.h`
- Package into an **uncompressed, flattened** `ai_model.zip` (ready for Wildlife Watcher devices)

### 📤 Upload & Direct Upload (Login Required)
- **Convert & Upload:** Seamlessly convert an Edge Impulse export and upload it to Supabase
- **Direct Upload:** Upload pre-converted `ai_model.zip` files (helpful if you already have optimized `.tfl` and `labels.txt`)
- Upload to your organization with `organisation_manager` or `ww_admin` role
- Automatic versioning and storage path management: `{org_id}/{model_name}-custom-{version}/ai_model.zip`

## 🎯 Usage

### Download MANIFEST Package
1. Visit [wildlifewatcher.streamlit.app](https://wildlifewatcher.streamlit.app/)
2. Click **"🚀 Download MANIFEST.zip"** (top of page)
3. Extract to SD card root directory
4. Insert SD card into Wildlife Watcher camera device

### Upload Custom Model
1. **Login** with your Wildlife Watcher account (sidebar)
2. **Select Workflow:** Choose **Convert & Upload** for raw exports, or **Direct Upload** if you already have an `ai_model.zip`.
3. **Configure Metadata:** Provide model name, version, and detection labels.
4. **Prepare/Convert:** Tool prepares the optimized package.
5. **Upload to Database:** Confirm organization and description.
6. Model is now available in the Wildlife Watcher mobile app!

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