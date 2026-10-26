// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// GridNotices: what the review grid says between the ribbon and the cards. The last result or
// failure, the page loading or failing, photos with no hosted file, and stuck thumbnails with
// one Retry for their deployments (02 #22).
import { Link } from 'react-router-dom'
import { ImageOff } from 'lucide-react'
import type { GridNotice } from '../../hooks/useGridNotice'
import type { PageStats } from '../../lib/gridMedia'
import type { NoticeLink } from '../../lib/inatPublish'
import { Banner } from '../ui/Banner'

const LINK_STYLE: React.CSSProperties = { fontSize: '0.8125rem', color: 'var(--primary)', whiteSpace: 'nowrap' }

/** A notice's link: a route of this site, or an iNaturalist page in a new tab. */
function NoticeLinkTo({ link }: { link: NoticeLink }) {
  return link.external
    ? <a href={link.to} target="_blank" rel="noreferrer" style={LINK_STYLE}>{link.label}</a>
    : <Link to={link.to} style={LINK_STYLE}>{link.label}</Link>
}

interface Props {
  notice: GridNotice | null
  onDismiss: () => void
  loading: boolean
  error: string | null
  stats: PageStats
  /** Cards on the page whose thumbnail is stuck; none in the similar list. */
  stuckCount: number
  onRetry: () => void
}

export function GridNotices({ notice, onDismiss, loading, error, stats, stuckCount, onRetry }: Props) {
  return (
    <>
      {notice && (
        <Banner tone={notice.tone} onClose={onDismiss} action={notice.link && <NoticeLinkTo link={notice.link} />}>
          {notice.text}
        </Banner>
      )}
      {loading && <p style={{ color: 'var(--text-muted)' }}>Loading media…</p>}
      {error && <Banner tone="error">{error}</Banner>}

      {/* Keyed on the loaded rows, not the loading flag: the 4 s refetch during an upload sets
          loading on every poll, which blinked the old banner on and off. Uploading placeholders
          are not counted (pageStats), so an upload in progress does not trigger it either. */}
      {stats.noImage > 0 && (
        <Banner tone="warning" icon={ImageOff}>
          {stats.noImage === stats.total ? 'These photos are' : `${stats.noImage} of these photos are`} not
          hosted online, so only their records can be shown. Upload the originals from the Upload page to
          attach them, or set a hosted URL in the media detail panel.
        </Banner>
      )}

      {stuckCount > 0 && (
        <Banner
          tone="warning"
          icon={ImageOff}
          action={
            <button type="button" className="btn btn-outline" style={{ fontSize: '0.75rem', padding: '0.2rem 0.6rem' }}
              title="Make the missing thumbnails for these deployments"
              onClick={onRetry}>
              Retry
            </button>
          }
        >
          {stuckCount} photo{stuckCount !== 1 ? 's' : ''} on this page {stuckCount !== 1 ? 'have' : 'has'} no thumbnail.
        </Banner>
      )}
    </>
  )
}
