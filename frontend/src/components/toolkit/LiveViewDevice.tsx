// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// LiveViewDevice: the live view's Device tab (#400). What the camera said about itself in its
// boot banner, its `ver` answer and its SD card lines, and the op params the page reads.
import type { LiveViewSnapshot } from '../../lib/liveViewSession'
import { MONO, SECTION, SECTION_TITLE } from './liveViewStyles'

const OPS: [op: number, name: string][] = [
  [7, 'timelapse interval (s)'],
  [8, 'inactivity before sleep (ms)'],
  [10, 'camera enabled'],
  [11, 'MD interval (ms)'],
  [17, 'MD sensitivity'],
  [33, 'wake threshold (cells)'],
]

function Rows({ rows }: { rows: [string, string | number | null][] }) {
  return (
    <dl style={{ margin: 0, display: 'grid', gridTemplateColumns: 'minmax(8rem, auto) 1fr', columnGap: '0.75rem', rowGap: '0.25rem' }}>
      {rows.map(([k, v]) => (
        <div key={k} style={{ display: 'contents' }}>
          <dt style={{ color: 'var(--text-muted)' }}>{k}</dt>
          <dd style={{ ...MONO, margin: 0, wordBreak: 'break-word' }}>{v ?? '-'}</dd>
        </div>
      ))}
    </dl>
  )
}

export function LiveViewDevice({ view }: { view: LiveViewSnapshot }) {
  const d = view.device
  const boot = d.boot ? `${d.boot}${d.wakeReason ? `, ${d.wakeReason}` : ''}` : null
  const sd = d.sd === 'ready' ? 'ready' : d.sd === 'failed' ? 'failed to start' : null
  const fromBanner = d.built || d.branch || d.flashOffset

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem', fontSize: '0.8125rem' }}>
      <section style={SECTION}>
        <h3 style={SECTION_TITLE}>Camera</h3>
        <Rows rows={[
          ['camera', d.camera ?? view.camera],
          ['board', d.board],
          ['firmware built', d.built],
          ['branch', d.branch],
          ['ver', d.version],
          ['last boot', boot],
          ['slot (flash offset)', d.flashOffset],
        ]} />
        {!fromBanner && (
          <p style={{ margin: 0, color: 'var(--text-muted)' }}>
            The build, branch and slot come from the boot banner: reset the camera to read them.
          </p>
        )}
      </section>
      <section style={SECTION}>
        <h3 style={SECTION_TITLE}>SD card</h3>
        <Rows rows={[['card', sd], ['next image', d.nextImage], ['images folder', d.imagesDir]]} />
      </section>
      <section style={SECTION}>
        <h3 style={SECTION_TITLE}>Op params</h3>
        <Rows rows={OPS.map(([op, name]) => [`${op}, ${name}`, view.ops[op] ?? null])} />
      </section>
    </div>
  )
}
