// ─────────────────────────────────────────────────────────────────────────────
// StatusBadge — annotation lifecycle state for a media item
//
// States (icons from lucide-react)
//   pending     grey   Hourglass  No observations yet (unprocessed / pipeline running)
//   issue       red    X          Explicit pipeline error (reserved for BE-2)
//   ai          teal   Cpu        Identified by the ML model only
//   reviewed    green  Check      A human has reviewed / annotated this image
//
// The review grid shows no badge for 'pending' (an unlabelled card says "No label"), so the
// thumbnails carry the three DESIGN.md states only. When BE-2 lands (explicit per-media status
// field) genuine failures surface as 'issue'.
// ─────────────────────────────────────────────────────────────────────────────
import { Check, Cpu, Hourglass, X, type LucideIcon } from 'lucide-react'
import type { AnnotationStatus } from '../../lib/annotationStatus'

export type { AnnotationStatus }

export interface StatusBadgeProps {
  status: AnnotationStatus
  /** 'sm' is the thumbnail overlay (compact), 'md' is inline in a list. */
  size?: 'sm' | 'md'
  /** Override the label. Useful for e.g. "AI 94%" */
  label?: string
}

interface BadgeStyle {
  bg: string
  border: string
  color: string
  icon: LucideIcon
  defaultLabel: string
}

const STYLES: Record<AnnotationStatus, BadgeStyle> = {
  pending: {
    bg:           'rgba(107,114,128,0.55)',
    border:       'rgba(107,114,128,0.35)',
    color:        '#ffffff',
    icon:         Hourglass,
    defaultLabel: 'Processing',
  },
  issue: {
    bg:           'rgba(239,68,68,0.88)',
    border:       'rgba(239,68,68,0.4)',
    color:        '#ffffff',
    icon:         X,
    defaultLabel: 'Error',
  },
  ai: {
    bg:           'rgba(20,184,166,0.88)',
    border:       'rgba(20,184,166,0.4)',
    color:        '#ffffff',
    icon:         Cpu,
    defaultLabel: 'AI',
  },
  reviewed: {
    bg:           'rgba(16,185,129,0.88)',
    border:       'rgba(16,185,129,0.4)',
    color:        '#ffffff',
    icon:         Check,
    defaultLabel: 'Reviewed',
  },
}

export function StatusBadge({ status, size = 'sm', label }: StatusBadgeProps) {
  const s = STYLES[status]
  const isSm = size === 'sm'
  const Icon = s.icon

  return (
    <span
      title={s.defaultLabel}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: isSm ? '0.2rem' : '0.3rem',
        padding: isSm ? '1px 5px' : '2px 7px',
        borderRadius: '4px',
        border: `1px solid ${s.border}`,
        backgroundColor: s.bg,
        color: s.color,
        fontSize: isSm ? '0.625rem' : '0.75rem',
        fontWeight: 700,
        lineHeight: 1.4,
        letterSpacing: '0.02em',
        whiteSpace: 'nowrap',
        userSelect: 'none',
      }}
    >
      <Icon size={isSm ? 10 : 12} strokeWidth={3} aria-hidden />
      {(!isSm || label) && (
        <span>{label ?? s.defaultLabel}</span>
      )}
    </span>
  )
}
