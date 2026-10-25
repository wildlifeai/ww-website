import { useState, useRef, useEffect, useCallback } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import { useProjectSelection } from '../../hooks/useProjectSelection'
import { projectPickerLabel } from '../../lib/projectSelection'
import { PICKER_SEARCH_MIN, filterProjects, groupByOrganisation, projectPickerTitle } from '../../lib/projectPicker'
import { Folder, ChevronDown, CheckSquare, Square, Settings2, Search } from 'lucide-react'

const MENU_WIDTH = 320

const LINK_BUTTON: React.CSSProperties = { fontSize: '0.75rem', background: 'none', border: 'none', cursor: 'pointer' }

// A row of the menu; .picker-row in index.css adds the hover and ticked tints.
const ROW: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: '0.5rem', width: '100%', padding: '0.5rem 0.75rem',
  border: 'none', background: 'transparent', color: 'var(--text-color)', cursor: 'pointer',
  fontSize: '0.875rem', textAlign: 'left',
}

export function GlobalProjectSelector() {
  const { projects, selectedProjectIds, toggleProject, selectAll, clearAll, isLoading } = useProjectSelection()
  const [isOpen, setIsOpen] = useState(false)
  const [query, setQuery] = useState('')
  const dropdownRef = useRef<HTMLDivElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  // The menu renders in a body portal (fixed position) so it overlays the Leaflet map on the
  // Insights page: an absolutely-positioned dropdown loses the stacking war to Leaflet's
  // controls/panes (z-index up to 1000) and gets hidden behind the map.
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)
  const navigate = useNavigate()

  // Anchor the menu under the trigger, left-aligned, clamped to the viewport.
  const place = useCallback(() => {
    const r = dropdownRef.current?.getBoundingClientRect()
    if (r) setPos({ top: r.bottom + 8, left: Math.max(8, Math.min(r.left, window.innerWidth - MENU_WIDTH - 8)) })
  }, [])

  // Close on outside click (the portalled menu lives outside dropdownRef) and on Escape.
  useEffect(() => {
    if (!isOpen) return
    const handleClickOutside = (event: MouseEvent) => {
      const t = event.target as Node
      if (dropdownRef.current?.contains(t) || menuRef.current?.contains(t)) return
      setIsOpen(false)
    }
    const handleKey = (event: KeyboardEvent) => { if (event.key === 'Escape') setIsOpen(false) }
    document.addEventListener('mousedown', handleClickOutside)
    document.addEventListener('keydown', handleKey)
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('keydown', handleKey)
    }
  }, [isOpen])

  // Keep the menu anchored to the trigger while open. rAF-throttled so scroll/resize don't
  // thrash layout with synchronous getBoundingClientRect + state updates on every event.
  useEffect(() => {
    if (!isOpen) return
    let frame = 0
    const onScrollResize = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(place) }
    window.addEventListener('scroll', onScrollResize, true)
    window.addEventListener('resize', onScrollResize)
    return () => {
      cancelAnimationFrame(frame)
      window.removeEventListener('scroll', onScrollResize, true)
      window.removeEventListener('resize', onScrollResize)
    }
  }, [isOpen, place])

  if (isLoading || projects.length === 0) return null

  const searchable = projects.length > PICKER_SEARCH_MIN
  const groups = groupByOrganisation(filterProjects(projects, searchable ? query : ''))
  const selected = new Set(selectedProjectIds)

  return (
    <div ref={dropdownRef} style={{ position: 'relative', minWidth: 0 }}>
      <button
        onClick={() => { if (!isOpen) { place(); setQuery('') } setIsOpen(o => !o) }}
        title={projectPickerTitle(projects, selectedProjectIds)}
        aria-haspopup="menu"
        aria-expanded={isOpen}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '0.5rem',
          padding: '0.375rem 0.75rem',
          fontSize: '0.875rem',
          backgroundColor: 'var(--surface)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius)',
          color: 'var(--text-color)',
          cursor: 'pointer',
          maxWidth: '100%',
        }}
      >
        <Folder size={16} color="var(--text-muted)" aria-hidden="true" style={{ flexShrink: 0 }} />
        <span className="picker-label" style={{ maxWidth: '150px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {projectPickerLabel(projects, selectedProjectIds)}
        </span>
        <ChevronDown size={14} color="var(--text-muted)" aria-hidden="true" style={{ flexShrink: 0 }} />
      </button>

      {isOpen && pos && createPortal(
        <div
          ref={menuRef}
          role="menu"
          aria-label="Projects"
          style={{
            position: 'fixed',
            top: pos.top,
            left: pos.left,
            width: `${MENU_WIDTH}px`,
            maxWidth: 'calc(100vw - 16px)',
            backgroundColor: 'var(--bg-color)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius)',
            boxShadow: '0 4px 16px rgba(0, 0, 0, 0.18)',
            // Above Leaflet (controls ~1000) and the upload dock (300).
            zIndex: 2000,
            overflow: 'hidden'
          }}
        >
          {searchable && (
            <label style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', padding: '0.5rem 0.75rem', borderBottom: '1px solid var(--border)' }}>
              <Search size={14} color="var(--text-muted)" aria-hidden="true" />
              <input
                autoFocus
                value={query}
                onChange={e => setQuery(e.target.value)}
                placeholder={`Search ${projects.length} projects`}
                aria-label="Search projects"
                style={{ flex: 1, minWidth: 0, border: 'none', outline: 'none', background: 'transparent', color: 'var(--text-color)', fontSize: '0.8125rem' }}
              />
            </label>
          )}
          <div style={{ padding: '0.5rem', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between' }}>
            <button onClick={selectAll} style={{ ...LINK_BUTTON, color: 'var(--primary)' }}>
              Select All
            </button>
            <button onClick={clearAll} style={{ ...LINK_BUTTON, color: 'var(--text-muted)' }}>
              Clear
            </button>
          </div>
          <div style={{ maxHeight: '360px', overflowY: 'auto' }}>
            {groups.map(group => (
              <div key={group.organisation ?? '-'} role="group" aria-label={group.organisation ?? undefined}>
                {groups.length > 1 && (
                  <div style={{ padding: '0.5rem 0.75rem 0.2rem', fontSize: '0.6875rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--text-muted)' }}>
                    {group.organisation ?? 'Other'}
                  </div>
                )}
                {group.projects.map(project => {
                  const isSelected = selected.has(project.id)
                  return (
                    <button
                      key={project.id}
                      type="button"
                      role="menuitemcheckbox"
                      aria-checked={isSelected}
                      className="picker-row"
                      onClick={() => toggleProject(project.id)}
                      title={project.name}
                      style={ROW}
                    >
                      {isSelected
                        ? <CheckSquare size={16} color="var(--primary)" aria-hidden="true" style={{ flexShrink: 0 }} />
                        : <Square size={16} color="var(--text-muted)" aria-hidden="true" style={{ flexShrink: 0 }} />}
                      <span style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        {project.name}
                      </span>
                    </button>
                  )
                })}
              </div>
            ))}
            {groups[0].projects.length === 0 && (
              <div style={{ padding: '0.75rem', fontSize: '0.8125rem', color: 'var(--text-muted)' }}>No project matches.</div>
            )}
          </div>
          {/* Manage projects: Settings */}
          <button
            type="button"
            role="menuitem"
            className="picker-row"
            onClick={() => { setIsOpen(false); navigate('/settings') }}
            style={{ ...ROW, borderTop: '1px solid var(--border)', color: 'var(--primary)', fontWeight: 600, fontSize: '0.8125rem' }}
          >
            <Settings2 size={15} aria-hidden="true" />
            <span>Manage projects…</span>
          </button>
        </div>,
        document.body,
      )}
    </div>
  )
}
