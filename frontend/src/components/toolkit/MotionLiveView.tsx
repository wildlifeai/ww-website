// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// MotionLiveView: the live view page's body (#400), a bench tool close to the firmware's
// live_view.py. Connect, Reconnect and Disconnect; the picture with the motion grid and status on
// the left; Controls, Console and Device tabs on the right. How to read it is in the guide.
import { useState } from 'react'
import type { GridRows } from '../../lib/liveView'
import { liveViewStatus } from '../../lib/liveViewSession'
import { serialSupported, useLiveView } from '../../hooks/useLiveView'
import { LiveViewConsole } from './LiveViewConsole'
import { LiveViewControls } from './LiveViewControls'
import { LiveViewDevice } from './LiveViewDevice'
import { LiveViewPicture } from './LiveViewPicture'

const TABS = [
  { id: 'controls', label: 'Controls' },
  { id: 'console', label: 'Console' },
  { id: 'device', label: 'Device' },
] as const
type Tab = (typeof TABS)[number]['id']

export function MotionLiveView() {
  const lv = useLiveView()
  const [rows, setRows] = useState<GridRows>(16)
  const [tab, setTab] = useState<Tab>('controls')

  if (!serialSupported()) {
    return (
      <p style={{ margin: 0, fontSize: '0.875rem' }}>
        This page needs Chrome or Edge on a computer. It talks to the camera&apos;s USB cable through
        the browser, which Firefox, Safari and phones cannot do.
      </p>
    )
  }

  const { view } = lv
  const running = view !== null && view.phase !== 'stopped'
  const status = liveViewStatus(view)
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap' }}>
        {running ? (
          <button type="button" className="btn" onClick={lv.disconnect} disabled={view.phase === 'stopping'}>Disconnect</button>
        ) : (
          <>
            <button type="button" className="btn" onClick={() => void lv.connect()}>Connect to a camera</button>
            {lv.canReconnect && (
              <button type="button" className="btn btn-outline" onClick={() => void lv.reconnect()}>Reconnect</button>
            )}
          </>
        )}
        {status && <span role="status" style={{ fontSize: '0.8125rem', color: 'var(--text-muted)' }}>{status}</span>}
      </div>
      {lv.error && <p role="alert" style={{ margin: 0, fontSize: '0.8125rem', color: '#ef4444' }}>{lv.error}</p>}
      {view && (
        <div style={{ display: 'flex', gap: '1.5rem', flexWrap: 'wrap', alignItems: 'flex-start' }}>
          <LiveViewPicture view={view} rows={rows} />
          <div style={{ flex: '1 1 300px', maxWidth: 440, display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
            <div role="tablist" aria-label="Live view" style={{ display: 'flex', gap: '0.25rem', borderBottom: '1px solid var(--border)' }}>
              {TABS.map(t => (
                <button
                  key={t.id}
                  type="button"
                  role="tab"
                  id={`live-view-tab-${t.id}`}
                  aria-selected={tab === t.id}
                  aria-controls={`live-view-panel-${t.id}`}
                  onClick={() => setTab(t.id)}
                  style={{
                    padding: '0.5rem 0.75rem', border: 'none', background: 'none', cursor: 'pointer', fontSize: '0.875rem',
                    fontWeight: tab === t.id ? 600 : 400, color: tab === t.id ? 'var(--text-color)' : 'var(--text-muted)',
                    borderBottom: tab === t.id ? '2px solid var(--primary)' : '2px solid transparent', marginBottom: -1,
                  }}
                >
                  {t.label}
                </button>
              ))}
            </div>
            <div role="tabpanel" id={`live-view-panel-${tab}`} aria-labelledby={`live-view-tab-${tab}`}>
              {tab === 'controls' && <LiveViewControls view={view} rows={rows} onRows={setRows} actions={lv} />}
              {tab === 'console' && <LiveViewConsole view={view} onCommand={lv.sendCommand} onSaveLog={lv.saveLog} />}
              {tab === 'device' && <LiveViewDevice view={view} />}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
