// Copyright (c) 2024
// SPDX-License-Identifier: GPL-3.0-or-later
//
// NotificationRulesPanel: the signed-in user's notification preferences for one project
// (Settings). An event type by channel matrix over `notification_rules` (lib/notificationRules).
// A tick saves at once and a species filter saves when you leave the field; each save reports
// through `onSaved`, so a refused one shows its error and the tick goes back.
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '../../hooks/useAuth'
import { supabase } from '../../config/supabase'
import { fetchRules, ruleFor, saveRule, toggleChannel, type Channel, type Rule } from '../../lib/notificationRules'

const EVENTS: { id: string; label: string; hint: string; hasFilter: boolean }[] = [
  { id: 'species_detection', label: 'Species detections', hint: 'When the AI labels a matching species', hasFilter: true },
  { id: 'camera_silent',     label: 'Camera went silent', hint: 'When an active camera stops reporting (needs LoRaWAN)', hasFilter: false },
  { id: 'upload_complete',   label: 'Upload finished',    hint: 'When an upload and its AI run complete', hasFilter: false },
]
const CHANNELS: { id: Channel; label: string; disabled?: boolean; note?: string }[] = [
  { id: 'web',   label: 'In-app' },
  { id: 'email', label: 'Email' },
  { id: 'push',  label: 'Mobile', disabled: true, note: 'Delivered by the mobile app' },
]

const MUTED: React.CSSProperties = { color: 'var(--text-muted)' }

export function NotificationRulesPanel({ projectId, onSaved }: {
  projectId: string
  /** Called after each save: ok, or not with the message to show. */
  onSaved: (ok: boolean, text?: string) => void
}) {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const key = ['notificationRules', projectId, user?.id]
  const query = useQuery({ queryKey: key, queryFn: () => fetchRules(supabase, projectId), enabled: !!user })

  const persist = async (next: Rule) => {
    if (!user) return
    const before = query.data ?? []
    const without = (rows: Rule[]) => rows.filter(r => r.event_type !== next.event_type)
    queryClient.setQueryData<Rule[]>(key, [...without(before), next])
    try {
      const saved = await saveRule(supabase, user.id, next)
      queryClient.setQueryData<Rule[]>(key, rows => [...without(rows ?? []), saved])
      onSaved(true)
    } catch (e) {
      queryClient.setQueryData<Rule[]>(key, before)
      onSaved(false, e instanceof Error ? e.message : undefined)
    }
  }

  if (query.isPending) return <p style={MUTED}>Loading…</p>
  if (query.isError) return <p style={{ ...MUTED, fontSize: '0.85rem' }}>Notification settings are not available here yet.</p>

  const rules = query.data
  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius)', overflow: 'hidden' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem' }}>
        <thead>
          <tr style={{ ...MUTED, fontSize: '0.72rem', textAlign: 'left' }}>
            <th style={{ padding: '0.4rem 0.9rem', fontWeight: 600 }}>Event</th>
            {CHANNELS.map(c => (
              <th key={c.id} style={{ padding: '0.4rem', fontWeight: 600, textAlign: 'center', width: 70 }} title={c.note}>{c.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {EVENTS.map(ev => {
            const r = ruleFor(rules, projectId, ev.id)
            return (
              <tr key={ev.id} style={{ borderTop: '1px solid var(--border)' }}>
                <td style={{ padding: '0.5rem 0.9rem' }}>
                  <div style={{ fontWeight: 500 }}>{ev.label}</div>
                  <div style={{ ...MUTED, fontSize: '0.7rem' }}>{ev.hint}</div>
                  {ev.hasFilter && (
                    <input
                      // Keyed on the stored filter, so a save or a refused one resets the field.
                      key={r.species_filter ?? ''}
                      defaultValue={r.species_filter ?? ''}
                      onBlur={e => {
                        const value = e.currentTarget.value.trim()
                        if (value !== (r.species_filter ?? '')) persist({ ...r, species_filter: value || null })
                      }}
                      onKeyDown={e => { if (e.key === 'Enter') e.currentTarget.blur() }}
                      placeholder="Any species (or type e.g. rat)"
                      aria-label="Species filter"
                      style={{ marginTop: '0.35rem', width: 'min(240px, 100%)', fontSize: '0.72rem', padding: '0.25rem 0.4rem', border: '1px solid var(--border)', borderRadius: 'var(--radius)', background: 'var(--surface)', color: 'var(--text-color)' }}
                    />
                  )}
                </td>
                {CHANNELS.map(c => (
                  <td key={c.id} style={{ padding: '0.5rem', textAlign: 'center' }}>
                    <input
                      type="checkbox"
                      checked={r.channels.includes(c.id)}
                      disabled={c.disabled}
                      title={c.note}
                      aria-label={`${ev.label}, ${c.label}`}
                      onChange={() => persist({ ...r, channels: toggleChannel(r.channels, c.id) })}
                      style={{ cursor: c.disabled ? 'not-allowed' : 'pointer' }}
                    />
                  </td>
                ))}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
