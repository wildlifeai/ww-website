# Wildlife Watcher V2 Frontend

This is the frontend user interface for the Wildlife Watcher platform, built with **React**, **TypeScript**, and **Vite**, and styled with standard web components.

## Features

- **Auth & Authorization**: Supabase Row Level Security (RLS) and Role-Based Access Control integration.
- **Model Conversion UI**: Configure Edge Impulse model optimizations and upload zip payloads directly.
- **Manifest Generation**: Real-time download package compilation for SD card insertion.
- **Image Analysis Toolkit**: Drag-and-drop hardware EXIF images/folders for meta-analysis, Google Drive uploading, and GPS verification.
- **Realtime Observability**: Connects to the backend ARQ-Redis job queue to poll active job conversion/upload states.

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

Create a file named `.env.local` in the `frontend/` root directory and add your backend references:

```env
# Supabase Configuration
VITE_SUPABASE_URL=https://<your-project>.supabase.co
VITE_SUPABASE_ANON_KEY=eyJhbGciOiJIUzI1Ni...

# Backend API Configuration
VITE_API_URL=http://localhost:8000
```

> **Note**: In production (e.g. Cloudflare Pages or Vercel), ensure `VITE_API_URL` points to your deployed FastAPI HTTPS domain.

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
  - `apiClient.ts` - Axios wrapper providing standardized error-handling and REST connectivity to the FastAPI Python backend.

## 🛠️ Build for Production

To create a production-ready optimized bundle:

```bash
npm run build
```

This will run TypeScript checks and output static files into the `dist/` directory, which can be easily hosted on platforms like Cloudflare Pages, Vercel, or standard NGINX servers.
