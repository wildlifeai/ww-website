// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// GridCards: the review grid's cards, one per photo (Photo view) or one per label (Labels view),
// with the card gestures of lib/cardSelection (#283): a click or Enter opens, the circle,
// Ctrl/Cmd-click or Space selects, Shift ranges, the arrow keys move between cards. A photo with
// an open label check carries a badge, or in "Needs a second look" its reason (#448).
import { Check, Hourglass, ImageOff, ScanEye, Upload } from 'lucide-react'
import type { InatStates } from '../../hooks/useGridInat'
import { deriveAnnotationStatus } from '../../lib/annotationStatus'
import { boxOf } from '../../lib/boxCrop'
import { arrowTarget, cardClickIntent, cardKeyIntent, circleClickIntent, type CardIntent } from '../../lib/cardSelection'
import { labelCards, type MediaRecord, type ObservationRecord } from '../../lib/gridMedia'
import { reasonText, type LabelCheck } from '../../lib/labelChecks'
import { displayImageUrl } from '../../lib/mediaImageUrl'
import { isAiLabel, isHumanReviewed, observationLabel, photoVerdict } from '../../lib/observations'
import { formatCaptureTime } from '../../lib/time'
import { StatusBadge } from '../ui/StatusBadge'
import { BoxCropImage } from './BoxCropImage'
import { INatBadge } from './INatBadge'
import { ELLIPSIS, IMAGE_AREA, TWO_LINES, cardStyle } from './gridStyles'

/** What every card needs from the grid. */
export interface CardEnv {
  /** Small thumbnails: no text lines. */
  compact: boolean
  /** Large thumbnails: a second label line. */
  roomy: boolean
  /** The photo open in the viewer, whose keys are its own. */
  openId: string | null
  selectedIds: ReadonlySet<string>
  isSelectable: (m: MediaRecord) => boolean
  isStuck: (m: MediaRecord) => boolean
  failedThumbs: ReadonlySet<string>
  onThumbError: (id: string) => void
  inatStates: InatStates
  timezoneOf: (deploymentId: string) => string | null | undefined
  onIntent: (id: string, intent: CardIntent, focusObsId: string | null) => void
  /** A photo's open label check, while its label is still the one flagged. */
  flagOf?: (m: MediaRecord) => LabelCheck | undefined
  /** In "Needs a second look": each card's reason, and "Checked, it's right" for a project member. */
  secondLook?: { onChecked: (checkId: string) => void; marking: boolean } | null
}

const fileNameOf = (m: MediaRecord) => m.file_name || m.file_path.split('/').pop() || ''

/** Arrow keys move focus between the grid's cards, a row at a time up and down. */
function moveFocus(e: React.KeyboardEvent<HTMLElement>): boolean {
  const cards = Array.from(document.querySelectorAll<HTMLElement>('.media-card'))
  const grid = e.currentTarget.parentElement
  const columns = grid ? getComputedStyle(grid).gridTemplateColumns.split(' ').filter(Boolean).length : 1
  const next = arrowTarget(e.key, cards.indexOf(e.currentTarget), cards.length, columns)
  if (next === null) return false
  cards[next].focus()
  return true
}

function cardProps(m: MediaRecord, focusObs: string | null, env: CardEnv) {
  const selectable = env.isSelectable(m)
  return {
    className: 'media-card',
    tabIndex: 0,
    title: selectable ? 'Click to open · Ctrl/Cmd-click or the circle to select · Shift-click to select a range' : 'Click to open',
    // Someone who cannot label these photos opens them with any click.
    onClick: (e: React.MouseEvent) => env.onIntent(m.id, selectable ? cardClickIntent(e) : 'open', focusObs),
    onKeyDown: (e: React.KeyboardEvent<HTMLElement>) => {
      // The clicked card keeps focus under the viewer, whose keys are its own.
      if (env.openId) return
      const intent = cardKeyIntent(e)
      if (intent && (selectable || intent === 'open')) { e.preventDefault(); env.onIntent(m.id, intent, focusObs) }
      else if (e.target === e.currentTarget && moveFocus(e)) e.preventDefault()
    },
  }
}

function SelectCircle({ m, env }: { m: MediaRecord; env: CardEnv }) {
  if (!env.isSelectable(m)) return null
  const sel = env.selectedIds.has(m.id)
  return (
    <button
      type="button"
      className="media-card-select"
      aria-label={`Select ${m.file_name || m.file_path.split('/').pop() || 'photo'}`}
      aria-pressed={sel}
      onClick={e => { e.stopPropagation(); env.onIntent(m.id, circleClickIntent(e), null) }}
      style={{
        position: 'absolute', bottom: 4, left: 4, width: 18, height: 18, padding: 0, border: 'none',
        borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center',
        cursor: 'pointer',
        backgroundColor: sel ? 'var(--primary)' : 'rgba(0,0,0,0.45)',
        color: '#fff', boxShadow: '0 0 0 1.5px rgba(255,255,255,0.85)',
        // Every circle shows while a selection is active; otherwise on hover (index.css).
        opacity: env.selectedIds.size > 0 ? 1 : undefined,
      }}
    >
      {sel && <Check size={12} strokeWidth={3} aria-hidden />}
    </button>
  )
}

interface LabelBarProps {
  name: string | null
  isEmpty: boolean
  conf: number | null
  hint: string
  /** The capture time, on photo cards only. */
  time?: string | null
  roomy: boolean
}

// The card's label line: one name (common name first, as the viewer shows it) and one
// confidence, the verdict's (02 #17, #18). Two lines on large thumbnails.
function LabelBar({ name, isEmpty, conf, hint, time, roomy }: LabelBarProps) {
  return (
    <div style={{ padding: '0.375rem 0.5rem', fontSize: '0.6875rem' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.4rem' }}>
        <span title={hint} style={{ flex: 1, minWidth: 0, fontWeight: 600, ...(roomy ? TWO_LINES : ELLIPSIS) }}>
          {name ?? (isEmpty
            ? <span style={{ color: 'var(--text-muted)', fontStyle: 'italic' }}>Empty</span>
            : <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>No label</span>)}
        </span>
        {conf !== null && <span style={{ color: 'var(--text-muted)', flexShrink: 0 }}>{(conf * 100).toFixed(0)}%</span>}
      </div>
      {time && <div style={{ color: 'var(--text-muted)', fontSize: '0.625rem', ...ELLIPSIS }}>{time}</div>}
    </div>
  )
}

const AMBER = '#f59e0b'

/** A flagged photo's mark on its thumbnail; the reason is its tooltip. */
function FlagBadge({ flag }: { flag: LabelCheck }) {
  return (
    <span title={`Needs a second look: ${reasonText(flag)}`}
      style={{ position: 'absolute', bottom: 4, right: 4, width: 18, height: 18, borderRadius: '50%', background: AMBER, color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <ScanEye size={11} aria-label="Needs a second look" />
    </span>
  )
}

/** In "Needs a second look": why the photo is flagged, and the button that clears it (decision 9). */
function FlagLine({ flag, canMark, look }: { flag: LabelCheck; canMark: boolean; look: NonNullable<CardEnv['secondLook']> }) {
  return (
    <div style={{ padding: '0 0.5rem 0.4rem', fontSize: '0.6875rem', display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
      <span style={{ display: 'flex', alignItems: 'flex-start', gap: 4 }}>
        <ScanEye size={12} aria-hidden style={{ color: AMBER, flexShrink: 0, marginTop: 1 }} />
        {reasonText(flag)}
      </span>
      {canMark && (
        <button type="button" disabled={look.marking} onClick={e => { e.stopPropagation(); look.onChecked(flag.id) }}
          style={{ alignSelf: 'flex-start', fontSize: '0.6875rem', padding: '0.1rem 0.45rem', border: '1px solid var(--border)', borderRadius: 'var(--radius)', background: 'transparent', color: 'var(--text-color)', cursor: 'pointer' }}>
          Checked, it's right
        </button>
      )}
    </div>
  )
}

/** A file still uploading: display only, no selection, viewer or actions. */
function PendingCard({ m, compact }: { m: MediaRecord; compact: boolean }) {
  const imgUrl = displayImageUrl(m, 'thumb')
  const fileName = fileNameOf(m)
  return (
    <div title={`Uploading ${fileName}`} style={{
      border: '1px solid var(--border)', borderRadius: 'var(--radius)', overflow: 'hidden',
      backgroundColor: 'var(--surface)',
    }}>
      <div style={{ ...IMAGE_AREA, backgroundColor: 'var(--surface)' }}>
        {imgUrl ? (
          <img src={imgUrl} alt={fileName} decoding="async" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
        ) : (
          <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>Uploading…</span>
        )}
        <span style={{ position: 'absolute', top: 6, left: 6, display: 'inline-flex', alignItems: 'center', gap: 3, background: 'rgba(0,0,0,0.6)', color: '#fff', fontSize: '0.62rem', padding: '2px 6px', borderRadius: 4 }}>
          <Upload size={10} aria-hidden /> Uploading
        </span>
      </div>
      {!compact && <div style={{ padding: '0.4rem 0.5rem', fontSize: '0.68rem', color: 'var(--text-muted)', ...ELLIPSIS }}>{fileName}</div>}
    </div>
  )
}

/** A photo's card in the Photo view. */
function ThumbCard({ m, env }: { m: MediaRecord; env: CardEnv }) {
  const imgUrl = displayImageUrl(m, 'thumb')
  const fileName = fileNameOf(m)
  // WS5-T4 / AN-2: derive status badge from the review_status contract
  const annotStatus = deriveAnnotationStatus({
    hasReviewed: m.observations.some(isHumanReviewed),
    hasAi: m.observations.some(isAiLabel),
  })
  // Top label: a human verdict, then the consensus row, then the first row (#170)
  const { labelObs: topObs, isEmpty } = photoVerdict(m.observations)
  const name = topObs && !isEmpty && (topObs.scientific_name || topObs.vernacular_name || topObs.observation_type)
    ? observationLabel(topObs) : null
  const inatSt = env.inatStates.get(m.id)
  const stuck = env.isStuck(m)
  const flag = env.flagOf?.(m)

  return (
    <div {...cardProps(m, null, env)} style={cardStyle(env.selectedIds.has(m.id), env.openId === m.id)}>
      <div style={{ ...IMAGE_AREA, backgroundColor: imgUrl ? undefined : 'rgba(0,0,0,0.04)' }}>
        {imgUrl && !env.failedThumbs.has(m.id) ? (
          <img
            src={imgUrl}
            alt={fileName || 'photo'}
            loading="lazy"
            decoding="async"
            style={{ width: '100%', height: '100%', objectFit: 'cover' }}
            onError={() => env.onThumbError(m.id)}
          />
        ) : (
          // Stuck: the thumbnail should exist and nothing is making it (one Retry above the
          // grid). Otherwise still generating.
          <span style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '0.3rem', fontSize: '0.72rem', color: 'var(--text-muted)' }}>
            {stuck ? <ImageOff size={18} aria-hidden /> : <Hourglass size={18} aria-hidden />}
            {!env.compact && <span>{stuck ? 'No thumbnail' : 'Processing…'}</span>}
          </span>
        )}
        {/* Status badge, top-left. No badge until a label exists. */}
        {annotStatus !== 'pending' && (
          <span style={{ position: 'absolute', top: 4, left: 4 }}><StatusBadge status={annotStatus} size="sm" /></span>
        )}
        {/* iNaturalist dove badge, top-right */}
        {inatSt && (
          <span style={{ position: 'absolute', top: 4, right: 4 }}><INatBadge state={inatSt.state} uri={inatSt.uri} /></span>
        )}
        {flag && !env.secondLook && <FlagBadge flag={flag} />}
        <SelectCircle m={m} env={env} />
      </div>
      {!env.compact && (
        <LabelBar name={name} isEmpty={isEmpty} conf={topObs?.classification_probability ?? null}
          hint={[topObs?.scientific_name, fileName].filter(Boolean).join(' · ')}
          time={m.timestamp && formatCaptureTime(m.timestamp, env.timezoneOf(m.deployment_id))} roomy={env.roomy} />
      )}
      {flag && env.secondLook && <FlagLine flag={flag} canMark={env.isSelectable(m)} look={env.secondLook} />}
    </div>
  )
}

// Crop view: one card per observation, showing its crop (observations.crop_url), its box cropped
// on screen when it has a box but no crop file, or the full frame when it has neither. Selection and open are keyed by media id, as the actions are.
function CropCard({ m, obs, env }: { m: MediaRecord; obs: ObservationRecord; env: CardEnv }) {
  const cropUrl = obs.crop_url || null
  const box = cropUrl ? null : boxOf(obs)
  const boxUrl = box ? displayImageUrl(m, 'full') : null
  const imgUrl = cropUrl || (boxUrl ? null : displayImageUrl(m, 'thumb'))
  const noCrop = !cropUrl && !boxUrl
  // Once a person gave the photo its verdict, each crop shows that verdict, not the AI's.
  const verdict = photoVerdict(m.observations)
  const ruled = !!verdict.labelObs && isHumanReviewed(verdict.labelObs) ? verdict : null
  const shown = ruled ? ruled.labelObs : obs
  const status = ruled ? 'reviewed' : deriveAnnotationStatus({ hasReviewed: isHumanReviewed(obs), hasAi: isAiLabel(obs) })
  const isEmpty = ruled ? ruled.isEmpty : !obs.scientific_name && obs.observation_type === 'blank'
  const name = shown && !isEmpty && (shown.scientific_name || shown.vernacular_name || shown.observation_type)
    ? observationLabel(shown) : null
  const aiSaid = ruled && isAiLabel(obs) && obs.scientific_name ? `AI: ${obs.scientific_name}` : null

  return (
    <div {...cardProps(m, obs.id, env)} style={cardStyle(env.selectedIds.has(m.id), env.openId === m.id)}>
      <div style={{ ...IMAGE_AREA, backgroundColor: imgUrl || boxUrl ? undefined : 'rgba(0,0,0,0.04)' }}>
        {box && boxUrl ? (
          <BoxCropImage src={boxUrl} box={box} alt={name || 'crop'} />
        ) : imgUrl ? (
          <img
            src={imgUrl}
            alt={name || 'crop'}
            loading="lazy"
            decoding="async"
            // Crops are tight cut-outs, so 'contain' shows the whole animal; a full-frame
            // fallback fills the card with 'cover'.
            style={{ width: '100%', height: '100%', objectFit: noCrop ? 'cover' : 'contain' }}
            onError={(e) => { (e.target as HTMLImageElement).style.display = 'none' }}
          />
        ) : (
          <ImageOff size={24} aria-hidden style={{ color: 'var(--text-muted)' }} />
        )}
        {status !== 'pending' && (
          <span style={{ position: 'absolute', top: 4, left: 4 }}><StatusBadge status={status} size="sm" /></span>
        )}
        {noCrop && imgUrl && !env.compact && (
          <span style={{ position: 'absolute', bottom: 4, right: 4, fontSize: '0.6rem', padding: '0.05rem 0.3rem', borderRadius: '3px', background: 'rgba(0,0,0,0.55)', color: '#fff' }}>
            full frame
          </span>
        )}
        <SelectCircle m={m} env={env} />
      </div>
      {!env.compact && (
        <LabelBar name={name} isEmpty={isEmpty} conf={ruled ? null : obs.classification_probability ?? null}
          hint={[shown?.scientific_name, aiSaid, m.file_name].filter(Boolean).join(' · ')} roomy={env.roomy} />
      )}
    </div>
  )
}

/**
 * The cards for `items`: a card per photo, or in the Labels view a card per label, AI crops and
 * human labels (full frame when they have no crop), at most MAX_LABEL_CARDS a photo.
 */
export function GridCards({ items, labelsView, env }: { items: readonly MediaRecord[]; labelsView: boolean; env: CardEnv }) {
  if (labelsView) return <>{items.flatMap(m => labelCards(m).map(o => <CropCard key={o.id} m={m} obs={o} env={env} />))}</>
  return <>{items.map(m => (m._pending ? <PendingCard key={m.id} m={m} compact={env.compact} /> : <ThumbCard key={m.id} m={m} env={env} />))}</>
}
