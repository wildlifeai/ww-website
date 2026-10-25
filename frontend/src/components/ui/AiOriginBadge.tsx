// ─────────────────────────────────────────────────────────────────────────────
// AiOriginBadge — which AI layer produced an observation
//
//   Camera AI: the camera's on-device (edge) model, ingested from EXIF
//   Cloud AI: the website pipeline (SpeciesNet / Wildlife Brain)
//
// Shown beside the species label so a rat frame reads "Camera AI: rat 87%" next to
// "Cloud AI: Rattus rattus 64%". Renders nothing for non-AI (human/imported) observations.
// The icon is a lucide one picked here; aiOriginMeta's emoji icon is not used.
// ─────────────────────────────────────────────────────────────────────────────

import { Camera, Cloud } from 'lucide-react'
import { aiOriginMeta, type ObservationStatusFields } from '../../lib/observations'

const TONE: Record<string, { bg: string; border: string; color: string }> = {
  edge:  { bg: 'rgba(139,92,246,0.16)', border: 'rgba(139,92,246,0.45)', color: '#7c3aed' }, // Camera AI — violet
  cloud: { bg: 'rgba(59,130,246,0.14)', border: 'rgba(59,130,246,0.4)',  color: '#2563eb' }, // Cloud AI — blue
}

export function AiOriginBadge({ obs, size = 'sm' }: { obs: ObservationStatusFields; size?: 'sm' | 'md' }) {
  const meta = aiOriginMeta(obs)
  if (!meta) return null
  const edge = obs.ai_origin === 'edge'
  const tone = edge ? TONE.edge : TONE.cloud
  const Icon = edge ? Camera : Cloud
  const isSm = size === 'sm'
  return (
    <span
      title={meta.title}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '0.2rem',
        padding: isSm ? '1px 5px' : '2px 7px',
        borderRadius: '4px',
        border: `1px solid ${tone.border}`,
        backgroundColor: tone.bg,
        color: tone.color,
        fontSize: isSm ? '0.625rem' : '0.7rem',
        fontWeight: 700,
        lineHeight: 1.4,
        whiteSpace: 'nowrap',
        userSelect: 'none',
      }}
    >
      <Icon size={isSm ? 10 : 12} aria-hidden />
      {meta.label}
    </span>
  )
}
