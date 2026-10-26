/**
 * Guides loader — turns the markdown files in src/content/guides/ into a
 * typed, sorted list at build time.
 *
 * Publishing workflow: merge a .md file (with the frontmatter below) into
 * src/content/guides/ and the next build renders it at /guides/<slug>.
 * Images live in public/guides/img/<slug>/ and are referenced from the
 * markdown as /guides/img/<slug>/name.png. See the README in the guides
 * folder for authoring conventions.
 *
 * Frontmatter:
 *   ---
 *   title: Machine Learning Models
 *   description: One-line summary shown on the guides index.
 *   category: Analysis
 *   order: 10
 *   updated: 2026-06-12
 *   ---
 */
import { stripFrontmatter } from './guideText'

export interface Guide {
  slug: string
  title: string
  description: string
  category: string
  order: number
  updated: string
}

// The guides index is in the main chunk (GuidesPage renders without a loading step, #228), so it
// imports only each guide's frontmatter, through the ww-guide-meta plugin in vite.config.ts. A
// guide's text is a chunk of its own, loaded by guideBody when its page opens. The folder README
// documents authoring conventions and is left out of both globs.
const metas = import.meta.glob(['../content/guides/*.md', '!**/README.md'], {
  query: '?meta',
  import: 'default',
  eager: true,
}) as Record<string, Record<string, string>>

const texts = import.meta.glob(['../content/guides/*.md', '!**/README.md'], {
  query: '?raw',
  import: 'default',
}) as Record<string, () => Promise<string>>

const slugOf = (path: string) => (path.split('/').pop() ?? '').replace(/\.md$/, '')

function buildGuides(): Guide[] {
  const guides: Guide[] = Object.entries(metas).map(([path, meta]) => ({
    slug: slugOf(path),
    title: meta.title || slugOf(path),
    description: meta.description || '',
    category: meta.category || 'General',
    order: Number(meta.order) || 999,
    updated: meta.updated || '',
  }))
  guides.sort((a, b) => a.category.localeCompare(b.category) || a.order - b.order || a.title.localeCompare(b.title))
  return guides
}

export const guides: Guide[] = buildGuides()

export function getGuide(slug: string): Guide | undefined {
  return guides.find(g => g.slug === slug)
}

const bodies = new Map<string, Promise<string>>()

/** The guide's markdown without its frontmatter, loaded once; undefined for an unknown slug. */
export function guideBody(slug: string): Promise<string> | undefined {
  const load = Object.entries(texts).find(([path]) => slugOf(path) === slug)?.[1]
  if (!load) return undefined
  let body = bodies.get(slug)
  if (!body) {
    body = load().then(stripFrontmatter)
    bodies.set(slug, body)
  }
  return body
}

/** Guides grouped by category, preserving the sorted order. */
export function guidesByCategory(): Map<string, Guide[]> {
  const map = new Map<string, Guide[]>()
  for (const g of guides) {
    const list = map.get(g.category) ?? []
    list.push(g)
    map.set(g.category, list)
  }
  return map
}
