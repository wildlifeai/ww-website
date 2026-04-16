<p align="center">
  <a href="https://wildlife.ai/">
    <img src="https://wildlife.ai/wp-content/uploads/2025/10/wildlife_ai_logo_dark_lightbackg_1772x591.png" alt="Wildlife.ai Logo" width="400">
  </a>
</p>

<h1 align="center">
Wildlife Watcher Web
</h1>

<p align="center">
  <strong>Complete toolkit for configuring and analyzing data from Wildlife Watcher devices.</strong>
</p>

## 🚀 Welcome to the real WWW

The Wildlife Watcher Website architecture is as follows:

- **Frontend**: React + TypeScript + Vite (Tailwind CSS)
- **Backend**: FastAPI + Python 3.11+
- **Async Workers**: Local Asyncio loop + Supabase (for heavy model conversion and Drive uploading)
- **Realtime DB**: Supabase (PostgreSQL, Storage, Realtime, Auth)

### Platform Features
- **LoRaWAN Webhook Integration**: Ingest real-time telemetry from TTN and Chirpstack.
- **Model Conversion**: Automate Edge Impulse C++ library conversion into Vela-optimized firmware models.
- **Manifest Generation**: Wrap firmware configs and models into `MANIFEST.zip` packages.
- **EXIF Image Analysis**: Drag and drop SD card folders to extract firmware EXIF data, match GPS, and route images to Google Drive.
- **Clustering and iNaturalist Integration**: TBC.

---

## 💻 Local Development Walkthrough

Since this is a multi-service workspace, you must run the frontend UI and the backend API as separate processes.

### 1. Prerequisites
- **Node.js 18+** & NPM
- **Python 3.11+**


### 2. Backend Setup (FastAPI)

> For full details, see the comprehensive [Backend README](backend/README.md).

1. Navigate to the backend:
   ```bash
   cd backend
   ```
2. Create and activate a Virtual Environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # or venv\Scripts\activate on Windows
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Configure `.env`:
   Copy `.env.example` to `.env` and fill in your Supabase variables.
5. Start the API Server:
   ```bash
   uvicorn app.main:app --reload --port 8000
   ```


### 3. Frontend Setup (React/Vite)

> For UI details, see the [Frontend README](frontend/README.md).

1. Navigate to the frontend:
   ```bash
   cd frontend
   ```
2. Install dependencies:
   ```bash
   npm install
   ```
3. Configure Environment Variables:
   Create a `.env.local` file with the following variables:
   ```env
   VITE_SUPABASE_URL=https://your-project.supabase.co
   VITE_SUPABASE_ANON_KEY=your-anon-key
   VITE_API_URL=http://localhost:8000
   ```
4. Start the Dev Server:
   ```bash
   npm run dev
   ```
   The site will be available at `http://localhost:5173`.

---

## 📁 Repository Structure

- `/backend/` - FastAPI application, domain logic, and background jobs.
- `/frontend/` - React/Vite user interface.
- `/docs/` - System architecture, API specs, and webhook configurations.

---

## 👥 Contributors
- Tobyn Packer
- Victor Anton

## 📜 License
This project is licensed under the **GPL-3.0 License** - see the `LICENSE` file for details.