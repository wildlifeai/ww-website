// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// SecondLookPanel: the head of the grid's "Needs a second look" list (#448): how many photos are
// flagged, the sensitivity slider (decision 14), the species pairs most often confused, each a
// filter to both species' flagged photos (decision 8), and Clear. What the flags mean is in the
// Wildlife Brain guide.
import { ScanEye, X } from 'lucide-react'
import type { GridSecondLook } from '../../hooks/useGridSecondLook'
import { SECOND_LOOK_MAX } from '../../hooks/useGridSecondLook'
import { Banner } from '../ui/Banner'
import { SMALL_BTN } from './gridStyles'

const CHIP: React.CSSProperties = { ...SMALL_BTN, fontSize: '0.72rem', padding: '0.15rem 0.5rem' }

export function SecondLookPanel({ look }: { look: GridSecondLook }) {
  return (
    <Banner
      tone="info"
      icon={ScanEye}
      action={
        <button type="button" onClick={() => look.setOn(false)}
          style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--primary)', fontSize: '0.8rem' }}>
          <X size={13} aria-hidden /> Clear
        </button>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
        <span>
          <strong>{look.count}</strong> {look.count === 1 ? 'photo needs' : 'photos need'} a second look
          {look.count > SECOND_LOOK_MAX && `, the first ${SECOND_LOOK_MAX} shown`}
        </span>
        <label style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.75rem' }}>
          Sensitivity
          <input type="range" min={0} max={100} step={5} value={look.sensitivity} onChange={e => look.setSensitivity(+e.target.value)}
            aria-label="Sensitivity" style={{ width: 120, accentColor: 'var(--primary)', cursor: 'pointer' }} />
        </label>
        {look.pairs.length > 0 && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.3rem', flexWrap: 'wrap', fontSize: '0.75rem' }}>
            <span>Often confused:</span>
            {look.pairs.map(p => {
              const active = !!look.pair && look.pair.join('|') === p.species.join('|')
              return (
                <button key={p.species.join('|')} type="button" aria-pressed={active} onClick={() => look.setPair(active ? null : p.species)}
                  style={{ ...CHIP, borderColor: active ? 'var(--primary)' : 'var(--border)', color: active ? 'var(--primary)' : 'var(--text-color)' }}>
                  {p.species[0]} / {p.species[1]} ({p.count})
                </button>
              )
            })}
          </div>
        )}
      </div>
    </Banner>
  )
}
