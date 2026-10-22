# UI Components Reference

The shared design-system primitives in `frontend/src/components/ui/` (plus `ThreeStepGuide` in
`components/common/`). **All use CSS variables** (`--primary`, `--surface`, `--border`, `--radius`)
and inline styles — no Tailwind, no component-library dependency.

Import from the barrel:

```tsx
import { Modal, Ribbon, DataTable, FilterSelect, SearchInput, ColumnToggle,
         StatusBadge, deriveAnnotationStatus, VegaChart } from '../components/ui'
import { ThreeStepGuide, DEFAULT_SIGNED_IN_STEPS } from '../components/common'
```

## Quick reference

| Export | File | Used by |
|---|---|---|
| `<Ribbon>` | `Ribbon.tsx` | **Annotations + Insights command bars** (Word-style menu bar + grouped ribbon) |
| `<Modal>` | `Modal.tsx` | Advanced settings, create-project (the upload flow is a page now, `/upload-data`) |
| `<DataTable>` | `DataTable.tsx` | Insights → Deployments / Projects tables |
| `<FilterSelect>`, `<SearchInput>`, `<ColumnToggle>` | `ControlBar.tsx` | `FilterSelect` inside ribbon groups and the admin Devices toolbar; the other two inside `DataTable` |
| `<StatusBadge>`, `deriveAnnotationStatus` | `StatusBadge.tsx` | Annotation thumbnail grid + modal |
| `<VegaChart>`, `VEGA_CONFIG` | `VegaChart.tsx` | All charts (Vega-Lite) |
| `<ThreeStepGuide>` | `../common/ThreeStepGuide.tsx` | Marketing + signed-in landing |

---

## `<Ribbon>` — the branded command bar

A Microsoft-Word-style surface: a **menu bar** (leaf brand mark + tabs + status slot) over a
**ribbon body** of **grouped categories**, each with an optional **dialog launcher** (the ⤢ arrow)
for advanced settings. Controlled (wire `activeTabId`/`onTabChange` to page state) or uncontrolled.

```tsx
<Ribbon
  status={<span><strong>{count}</strong> media</span>}
  tabs={[
    { id: 'filter', label: 'Filter', icon: '⛃', groups: [
      { id: 'deployment', title: 'Deployment', content: <FilterSelect … /> },
      { id: 'refine', title: 'Refine',
        launcher: () => setAdvancedOpen(true), launcherActive: hasAdvancedFilters,
        content: <button onClick={() => setAdvancedOpen(true)}>⚙ Date · Day/Night</button> },
    ] },
    { id: 'view', label: 'View', icon: '🗗', groups: [
      { id: 'thumbs', title: 'Thumbnail size', content: thumbToggle },
    ] },
  ]}
/>
```

| Prop | Type | Description |
|---|---|---|
| `tabs` | `RibbonTabDef[]` | `{ id, label, icon?, groups: RibbonGroupDef[] }` |
| `activeTabId` / `onTabChange` | `string` / `(id) => void` | Controlled mode (e.g. Insights sub-tabs) |
| `defaultTabId` | `string` | Uncontrolled initial tab |
| `status` | `ReactNode` | Right-aligned menu-bar status |

`RibbonGroupDef`: `{ id, title, content, launcher?, launcherTitle?, launcherActive? }`.

---

## `<Modal>`

Overlay dialog with focus trap, ESC-to-close, and click-backdrop-to-close.

| Prop | Type | Default | Description |
|---|---|---|---|
| `open` | `boolean` | — | Visibility |
| `onClose` | `() => void` | — | ESC / X / backdrop |
| `title` | `string` | — | Header row with title + X (omit for bare body) |
| `footer` | `ReactNode` | — | Right-aligned footer (action buttons) |
| `size` | `'sm'\|'md'\|'lg'\|'xl'` | `'md'` | Max-width 400 / 560 / 720 / 960 px |
| `persistent` | `boolean` | `false` | Backdrop click does not close |

---

## `<DataTable>`

Generic typed table with sort, search, column toggle, CSV + JSON export, and optional client paging.

| Prop | Type | Description |
|---|---|---|
| `columns` | `Column<T>[]` | `{ key, label, sortable?, hideable?, render?, cellStyle?, getValue? }` |
| `rows` / `rowKey` | `T[]` / `(row) => string` | data + stable key |
| `searchable`, `searchPlaceholder` | | search box |
| `exportFilename` | `string` | enables CSV + JSON buttons |
| `onRowClick`, `selectedKeys` | | row click + highlight |
| `toolbar` | `ReactNode` | extra content beside search |
| `pageSize` | `number` | `0` = no paging |

---

## `<FilterSelect>` / `<SearchInput>` / `<ColumnToggle>`

`FilterSelect` (a sized `<select>`) is used **inside ribbon groups** and as a `DataTable`
`toolbar`; `SearchInput` and `ColumnToggle` are used by `DataTable`. They live in `ControlBar.tsx`,
named after a flat toolbar that had no users and was removed in October 2026.

```tsx
<FilterSelect value={dep} onChange={setDep}
  options={deployments.map(d => ({ value: d.id, label: d.location_name ?? d.id }))}
  placeholder="All deployments" />
```

---

## `<StatusBadge>` + `deriveAnnotationStatus`

Annotation-lifecycle badge for a media item (thumbnail overlay or inline). The status contract is
driven by `review_status` — see [`lib/observations.ts`](../onboarding/05-ANNOTATION-WORKFLOW.md).

| `status` | Colour | Icon | Meaning |
|---|---|---|---|
| `'pending'` | grey | ⧗ | No observations yet — still working through the pipeline (default) |
| `'ai'` | teal | AI | AI-produced label (incl. `blank`), not yet human-reviewed |
| `'reviewed'` | green | ✓ | A human validated the label |
| `'issue'` | red | ✕ | Explicit pipeline error — **reserved**, not yet emitted (see `StatusBadge.tsx`) |

```tsx
const status = deriveAnnotationStatus({
  hasReviewed: obs.some(isHumanReviewed),   // from lib/observations
  hasAi:       obs.some(isAiLabel),
})
<StatusBadge status={status} size="sm" label={status === 'ai' ? `AI ${pct}%` : undefined} />
```

Props: `status: AnnotationStatus`, `size: 'sm' | 'md'` (default `sm`), `label?` (override).

---

## `<VegaChart>`

Thin wrapper around `vega-embed` with the shared `VEGA_CONFIG` theme. Pass a Vega-Lite spec; the
chart sizes to its container. Used by `ReportsDashboard` (Insights Reports).

---

## `<ThreeStepGuide>`

Three-card "how it works" component shared by the marketing page and the signed-in landing.
Props: `steps: GuideStep[]` (`{ icon, title, description, linkTo, linkLabel }`), optional `heading`.
Presets: `DEFAULT_SIGNED_IN_STEPS`, `DEFAULT_MARKETING_STEPS` from `../components/common`.

---

## Design decisions

- **Inline styles + CSS variables, not Tailwind** — matches the rest of the codebase; no new build config.
- **No third-party component library** — these primitives cover what the app needs without a heavy dependency; swap gradually if that changes.
- **Manual focus trap in `<Modal>`** (~20 lines) avoids a dependency for a single component.
