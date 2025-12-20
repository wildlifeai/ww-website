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