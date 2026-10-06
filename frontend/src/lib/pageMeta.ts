/**
 * Per-page document metadata for a single-page app: the title in the tab and
 * in a shared link, the description search engines and link previews show,
 * the canonical URL. Every public page calls usePageMeta; signed-in pages
 * fall back to the site name when the public page unmounts.
 *
 * The defaults live in index.html too, for a crawler that reads the HTML
 * before React runs. Keep the two in step.
 */
import { useEffect } from 'react'

export const SITE_NAME = 'Wildlife Watcher'
export const SITE_URL = 'https://wildlifewatcher.ai'
export const DEFAULT_DESCRIPTION =
  'Open-source AI camera traps for invertebrates and small animals, with a mobile app for the field and a website to review, analyse and report on what they capture.'

function setMeta(attr: 'name' | 'property', key: string, content: string) {
  let el = document.head.querySelector<HTMLMetaElement>(`meta[${attr}="${key}"]`)
  if (!el) {
    el = document.createElement('meta')
    el.setAttribute(attr, key)
    document.head.appendChild(el)
  }
  el.content = content
}

function setCanonical(href: string | null) {
  let el = document.head.querySelector<HTMLLinkElement>('link[rel="canonical"]')
  if (!href) { el?.remove(); return }
  if (!el) {
    el = document.createElement('link')
    el.rel = 'canonical'
    document.head.appendChild(el)
  }
  el.href = href
}

export interface PageMeta {
  /** Page title without the site name; omitted on the home page. */
  title?: string
  description?: string
  /** Canonical path when it differs from the current one, e.g. /signup rendering LoginPage. */
  path?: string
}

export function usePageMeta({ title, description, path }: PageMeta) {
  useEffect(() => {
    const fullTitle = title ? `${title} · ${SITE_NAME}` : SITE_NAME
    const text = description || DEFAULT_DESCRIPTION
    const url = SITE_URL + (path ?? window.location.pathname)
    document.title = fullTitle
    setMeta('name', 'description', text)
    setMeta('property', 'og:title', fullTitle)
    setMeta('property', 'og:description', text)
    setMeta('property', 'og:url', url)
    setCanonical(url)
    return () => {
      document.title = SITE_NAME
      setMeta('name', 'description', DEFAULT_DESCRIPTION)
      setMeta('property', 'og:title', SITE_NAME)
      setMeta('property', 'og:description', DEFAULT_DESCRIPTION)
      setMeta('property', 'og:url', SITE_URL)
      setCanonical(null)
    }
  }, [title, description, path])
}
