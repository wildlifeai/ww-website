// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// useGridNotice: the review grid's one notice at a time (a result, a failure, an iNaturalist
// publish), shown as a Banner. A success hides itself after 4 s; an iNaturalist notice stays
// until the next one, as in Cluster Review, so its link can be used.
import { useCallback, useState } from 'react'
import type { BannerTone } from '../components/ui/Banner'
import type { CardNotice, NoticeLink } from '../lib/inatPublish'

export interface GridNotice {
  tone: BannerTone
  text: string
  link?: NoticeLink
}

export function useGridNotice() {
  const [notice, setNotice] = useState<GridNotice | null>(null)
  const flash = useCallback((tone: BannerTone, text: string, autoHide = tone === 'success') => {
    setNotice({ tone, text })
    if (autoHide) window.setTimeout(() => setNotice(n => (n?.text === text ? null : n)), 4000)
  }, [])
  const flashError = useCallback((text: string) => flash('error', text), [flash])
  const showCardNotice = useCallback(
    (n: CardNotice) => setNotice({ tone: n.ok ? 'success' : 'error', text: n.text, link: n.link }),
    [],
  )
  const dismiss = useCallback(() => setNotice(null), [])
  return { notice, flash, flashError, showCardNotice, dismiss }
}
