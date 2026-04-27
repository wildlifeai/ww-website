# Wildlife Watcher V2 Frontend

This is the frontend user interface for the Wildlife Watcher platform, built with **React**, **TypeScript**, and **Vite**, and styled with standard web components.

## Features

- **Auth & Authorization**: Supabase Row Level Security (RLS) and Role-Based Access Control integration.
- **Model Conversion UI**: Configure Edge Impulse model optimizations and upload zip payloads directly.
- **Manifest Generation**: Real-time download package compilation for SD card insertion.
- **Image Analysis Toolkit**: Drag-and-drop hardware EXIF images/folders for meta-analysis, Google Drive uploading, and GPS verification.
- **Realtime Observability**: Connects to the backend Supabase-backed asynchronous job system to poll active job conversion/upload states.

## 🚀 Setup & Installation

### Prerequisites
- Node.js (v18 or higher)
- NPM (or Yarn/PNPM)

### 1. Install Dependencies

1. Navigate to the `frontend/` directory (if you aren't already here):
   ```bash
   cd frontend
   ```
2. Install the `node_modules`:
   ```bash
   npm install
   ```

### 2. Environment Variables

The frontend reads environment variables from the **root `.env` file** (one level up from `frontend/`). This is configured via `envDir: '../'` in `vite.config.ts`. **Do not create a `frontend/.env.local` file.**

The root `.env` must contain:

```env
# Supabase Configuration (shared with backend)
SUPABASE_URL=https://<your-project>.supabase.co
SUPABASE_ANON_KEY=eyJhbGciOiJIUzI1Ni...

# Frontend-specific (Vite injects at build time)
VITE_API_BASE_URL=http://localhost:8000
```

> **Note**: `vite.config.ts` maps `SUPABASE_URL` → `import.meta.env.VITE_SUPABASE_URL` and `SUPABASE_ANON_KEY` → `import.meta.env.VITE_SUPABASE_ANON_KEY` automatically. On Cloudflare Pages, set `VITE_API_BASE_URL` to your deployed Azure Container App FQDN.

### 3. Run the Development Server

Start the local Vite dev server:

```bash
npm run dev
```

Visit `http://localhost:5173` in your browser. The page will hot-reload as you make modifications to the source code.

## 📁 Key File Structure

- `src/components/` - Reusable UI components.
  - `toolkit/` - Main interface modules (e.g., `AnalyseImages.tsx`, `UploadModel.tsx`, `BuildManifest.tsx`).
- `src/config/` - External configuration definitions (e.g. `supabase.ts`).
- `src/lib/` - Utility libraries.
  - `apiClient.ts` - Fetch wrapper providing standardized error-handling and REST connectivity to the FastAPI Python backend.

## 🛠️ Build for Production

To create a production-ready optimized bundle:

```bash
npm run build
```

This will run TypeScript checks and output static files into the `dist/` directory, which can be easily hosted on platforms like Cloudflare Pages, Vercel, or standard NGINX servers.
