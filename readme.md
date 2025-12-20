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
  <a href="https://wildlife-watcher.streamlit.app" target="_blank">
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
- Package into `Manifest.zip` for Wildlife Watcher devices

### 📤 Upload to Supabase (Login Required)
- Upload custom models to your organization
- Requires `organisation_manager` or `ww_admin` role
- Automatic version management
- Rollback on failure

## 🎯 Usage

### Download MANIFEST Package
1. Visit [wildlife-watcher.streamlit.app](https://wildlife-watcher.streamlit.app)
2. Click **"🚀 Download MANIFEST.zip"** (top of page)
3. Extract to SD card root directory
4. Insert SD card into Wildlife Watcher camera device

### Upload Custom Model
1. **Login** with your Wildlife Watcher account (sidebar)
2. **Upload** your Edge Impulse model zip (format: `modelname-custom-version.zip`)
3. **Convert** - app runs Vela optimization
4. **Download** converted model (optional)
5. **Upload to Database** - select your organization
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
   Create `.env` file:
   ```env
   SUPABASE_URL=https://your-project.supabase.co
   SUPABASE_ANON_KEY=your-anon-key-here
   ```

4. **Run the app:**
   ```bash
   streamlit run app.py
   ```

The application will open at `http://localhost:8501`.

## 🔧 Tech Stack
- **Frontend:** Streamlit
- **Backend:** Supabase (PostgreSQL + Storage)
- **ML Compiler:** Ethos-U Vela
- **Deployment:** Streamlit Community Cloud

## 🧩 How It Works: Manifest Generation

The **public MANIFEST.zip download** feature dynamically assembles the package on-the-fly:

1.  **Config Firmware**: Fetches the latest active firmware record of type `config` from Supabase.
2.  **AI Model**: Fetches the latest active AI model for the **General Organization** (`550e...`).
3.  **Merging**:
    - Downloads both zip files from Supabase Storage.
    - Extracts them into a temporary structure.
    - Zips the combined result into a single `MANIFEST.zip`.

> [!NOTE]
> If either the Config Firmware or the Default AI Model is missing in the database, the app will warn the user and skip including that component in the final zip.

## 💻 Local Development & Testing

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

## 📁 File Structure
- `app.py` - Main Streamlit application
- `requirements.txt` - Python dependencies
- `.env` - Supabase credentials (local only)
- `readme.md` - This file

## 👥 Contributors
- Will McEwan
- Tobyn Packer
- Victor Anton

## 📜 License
This project is licensed under the **GPL-3.0 License** - see the `LICENSE` file for details.