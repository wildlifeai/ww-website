import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
// Inter, the face index.css declares: the latin subset in the four weights the
// UI uses, self-hosted through the bundle (about 24 kB each, font-display: swap).
// Until October 2026 no webfont was loaded and the site rendered in the system
// fallback, see .agents/DESIGN.md.
import '@fontsource/inter/latin-400.css'
import '@fontsource/inter/latin-500.css'
import '@fontsource/inter/latin-600.css'
import '@fontsource/inter/latin-700.css'
import "./styles/index.css";
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
