// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// LiveViewConsole: the live view's Console tab (#400). A command box for any console command
// (setop, getop, camreg, vcm ...), live_view.py's quick commands, and the session log: the pane
// shows the latest lines, Save log downloads the whole session with each frame as one line.
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { QUICK_COMMANDS } from '../../lib/liveView'
import { logText, type LiveViewSnapshot } from '../../lib/liveViewSession'
import { INPUT, MONO, ROW, SECTION, SECTION_TITLE } from './liveViewStyles'

export function LiveViewConsole({ view, onCommand, onSaveLog }: {
  view: LiveViewSnapshot
  onCommand: (command: string) => void
  onSaveLog: () => void
}) {
  const [command, setCommand] = useState('')
  const pane = useRef<HTMLPreElement>(null)
  const live = view.phase !== 'stopping' && view.phase !== 'stopped'

  // Follow the newest line unless the reader has scrolled up.
  useEffect(() => {
    const el = pane.current
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 60) el.scrollTop = el.scrollHeight
  }, [view.logLines])

  const send = (e: FormEvent) => {
    e.preventDefault()
    if (!command.trim()) return
    onCommand(command)
    setCommand('')
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', fontSize: '0.8125rem' }}>
      <form onSubmit={send} style={ROW}>
        <label htmlFor="live-view-command" style={{ fontWeight: 500 }}>Command</label>
        <input
          id="live-view-command"
          value={command}
          onChange={e => setCommand(e.target.value)}
          disabled={!live}
          spellCheck={false}
          autoComplete="off"
          placeholder="getop 11"
          style={{ ...INPUT, ...MONO, flex: '1 1 8rem', width: 'auto' }}
        />
        <button type="submit" className="btn" disabled={!live || !command.trim()}>Send</button>
      </form>
      <section style={SECTION}>
        <h3 style={SECTION_TITLE}>Quick commands</h3>
        <div style={ROW}>
          {QUICK_COMMANDS.map(cmd => (
            <button key={cmd} type="button" className="btn btn-outline" disabled={!live} onClick={() => onCommand(cmd)} style={{ ...MONO, fontSize: '0.75rem' }}>
              {cmd}
            </button>
          ))}
        </div>
      </section>
      <section style={SECTION}>
        <h3 style={SECTION_TITLE}>Log</h3>
        <pre
          ref={pane}
          style={{
            ...MONO, margin: 0, height: 320, overflow: 'auto', padding: '0.5rem 0.75rem', fontSize: '0.72rem',
            backgroundColor: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius)',
            whiteSpace: 'pre-wrap', wordBreak: 'break-all',
          }}
        >
          {logText(view.log)}
        </pre>
        <div style={{ ...ROW, justifyContent: 'space-between' }}>
          <span style={{ color: 'var(--text-muted)' }}>{view.logLines.toLocaleString('en-NZ')} lines in this session</span>
          <button type="button" className="btn btn-outline" onClick={onSaveLog}>Save log</button>
        </div>
      </section>
    </div>
  )
}
