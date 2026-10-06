---
name: wildlife-watcher-web
description: >
  Design system extracted from the ww-website codebase (frontend/src). Source of
  truth for colors, typography, spacing, and component patterns when generating
  or restyling screens for the Wildlife Watcher web platform.
product:
  name: Wildlife Watcher Web
  tagline: Smart monitoring of small animals
  description: >
    Web platform for uploading, AI-labelling, reviewing and analysing
    camera-trap data from Wildlife Watcher devices.
  organisation: wildlife.ai
colors:
  light:
    primary: "rgb(0, 110, 28)"
    primary-light: "rgb(76, 175, 80)"
    bg: "#ffffff"
    text: "#333333"
    surface: "#f9fafb"
    border: "#e2e8f0"
    error: "#ef4444"
    success: "#10b981"
  dark:
    primary: "rgb(76, 175, 80)"
    bg: "#121212"
    text: "#f3f4f6"
    surface: "#1e1e1e"
    border: "#334155"
  semantic:
    success: "#4caf50"
    warning: "#ff9800"   # also #f59e0b in newer code
    error: "#f44336"     # also #ef4444
    info: "#2196f3"      # also #3b82f6
  annotation-status:
    issue: "rgba(239, 68, 68, 0.88)"     # red ✕ — needs action
    ai: "rgba(20, 184, 166, 0.88)"       # teal ⚙ — AI-labelled only
    reviewed: "rgba(16, 185, 129, 0.88)" # green ✓ — human reviewed
  primary-tints: "rgba(76, 175, 80, 0.04–0.30) for hover, selection and highlight fills"
  third-party:
    inaturalist: "#74ac00"  # iNaturalist brand green — only on iNat badges/panels
typography:
  family: "'Inter', system-ui, Avenir, Helvetica, Arial, sans-serif"
  base-line-height: 1.5
  weights: [400, 500, 600, 700]
  scale:
    micro: "0.625rem"     # badge overlays
    caption: "0.75rem"    # table cells, metadata (very common)
    body-dense: "0.8125rem"  # the dominant UI body size
    body: "0.875rem"
    default: "1rem"
    section-title: "1.5rem"
    hero: "3rem"
spacing:
  unit: rem
  common-gaps: ["0.25rem", "0.375rem", "0.5rem", "0.75rem", "1rem", "1.5rem"]
  card-padding: "1.25rem"
  page-padding: "2rem"
radius:
  default: "8px"   # var(--radius) — cards, buttons, inputs, images
  small: "4px"     # badges, inline code
  circle: "50%"    # avatars, status dots
layout:
  container-max-width: "1200px"
  dark-mode: "prefers-color-scheme media query (no manual toggle)"
stack:
  frontend: "React 19 + TypeScript + Vite, plain CSS (no Tailwind / CSS-in-JS framework)"
  styling: "CSS custom properties in src/styles/index.css + inline style objects in components"
  icons: lucide-react
  maps: "Leaflet (react-leaflet)"
  charts: "Vega-Lite (vega-embed)"
---

# Wildlife Watcher Web — Design Language

Extracted from the codebase (`frontend/src/`), 2026-06. The repo is the source
of truth: tokens live in [`frontend/src/styles/index.css`](../frontend/src/styles/index.css);
everything else below was collected from inline `style={{}}` objects across
`frontend/src/components/` and `frontend/src/pages/`.

## Atmosphere

A practical, data-dense **conservation workbench**, not a marketing site. The
single marketing surface (logged-out `HomePage` hero: "Smart monitoring of
small animals") is centred, generous and green; everything behind login is a
compact dashboard of tables, thumbnails, maps and review queues. Whitespace is
functional — small gaps (0.5–1rem), dense type (0.75–0.875rem), information
first. Color temperature is natural: greens carry the brand, white/near-white
surfaces keep imagery (camera-trap photos) the visual focus.

## Color

- **Brand green** is the only brand color. Light mode uses the deep
  `rgb(0, 110, 28)`; dark mode and most component accents use the lighter
  `#4caf50`. Low-opacity green tints (`rgba(76,175,80, .04–.30)`) are the
  house style for hover states, selected rows and highlight panels — prefer a
  tint of the primary over introducing a new hue.
- **Semantic colors** follow Material-ish conventions: orange `#ff9800` /
  amber `#f59e0b` for warnings, red `#f44336`/`#ef4444` for errors, blue
  `#2196f3`/`#3b82f6` for info. (Two generations of values coexist; new work
  should prefer the Tailwind-palette values: `#f59e0b`, `#ef4444`, `#3b82f6`,
  `#10b981`.)
- **Annotation status** is a fixed three-state vocabulary used on media
  thumbnails and lists (see `components/ui/StatusBadge.tsx`): red ✕ "issue",
  teal ⚙ "AI", green ✓ "reviewed", each as an 0.88-alpha fill with a 0.4-alpha
  border of the same hue and white text. Do not repurpose these hues.
- **iNaturalist green** `#74ac00` appears only on iNaturalist badges and
  panels — it is their brand, not ours.
- **Dark mode** is automatic via `prefers-color-scheme`; every screen must
  work in both. Use the CSS variables (`var(--surface)`, `var(--border)`,
  `var(--text-color)`) rather than literal greys so both themes hold.

## Typography

Inter over a system-ui stack, weight 400 body / 500 buttons-labels / 700
emphasis and badges. The UI is deliberately small: `0.8125rem` is the de-facto
body size, `0.75rem` for table cells and metadata, `0.875rem` for prominent
body text. Section titles sit at `1.5rem`; only the logged-out hero uses
`3rem`. Line-height 1.5 (1.7 in long-form guide content).

Inter is self-hosted: `main.tsx` imports the latin subset of `@fontsource/inter`
in weights 400, 500, 600 and 700 (`font-display: swap`), so the face the
tokens describe is the one the browser renders. Until October 2026 nothing
loaded it and the site rendered in the system fallback.

## Components

- **Button** (`.btn`): solid primary fill, white text, `var(--radius)`,
  `0.5rem 1rem` padding, weight 500, hover = opacity 0.9. Secondary actions
  are outlined or text-only variants built inline.
- **Card** (`.card`): `var(--surface)` fill, 1px `var(--border)`,
  `var(--radius)`, `1.25–1.5rem` padding. The basic building block of every
  dashboard page.
- **StatusBadge**: the annotation-status pill described above; `sm` overlays
  thumbnails, `md` sits inline in lists.
- **DataTable / ControlBar**: dense tables (0.75–0.8125rem cells, bordered,
  `var(--surface)` headers) with a control bar of filters above. Most pages
  are a ControlBar + table/grid + map or chart.
- **Modal**: centred dialog over `rgba(0,0,0,0.45–0.5)` scrim, card styling
  inside.
- **Icons**: lucide-react, sized to the text they accompany.
- **Maps & charts**: Leaflet for deployment locations, Vega-Lite for insight
  charts. Charts inherit the green-first palette.

## Information architecture

The product's native shape is a **catalog with review workflows**, organised
project → deployment → media → observations:

- **Explore/triage surfaces**: Image Explorer, Media Browser, Review Queue,
  Cluster Review, Event Review — thumbnail grids with status badges plus
  filters.
- **Analysis surfaces**: Insights, Analysis, Reporting, Dataset Health, UMAP
  Explorer — charts and tables over the same data.
- **Field/ops surfaces**: Upload Data, Field, Manifest, Toolkit — task-driven
  forms and progress docks.
- **Long-form**: Guides and FAQ render markdown via the `.guide-markdown`
  styles in `index.css`.

Generated screens should lead with media (images) and their status — what
matters before what measures. Pull real terminology from the domain: projects,
deployments, devices, observations, species, clusters, CamtrapDP exports.
Never invent species names, stats or copy; source them from the repo.

## Conventions for new screens

1. Use the CSS variables for surface/border/text and the brand green (or its
   tints) for emphasis — both themes come free.
2. Stay dense: 0.8125rem body, 0.5–1rem gaps, 1.25rem card padding, 1200px max
   content width.
3. One radius (`8px`) everywhere except small badges (`4px`).
4. Respect the fixed status vocabularies (annotation triad, semantic colors).
5. Plain CSS / inline styles — do not introduce Tailwind, CSS-in-JS or a
   component library.
