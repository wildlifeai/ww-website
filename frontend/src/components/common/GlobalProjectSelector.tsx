import { useState, useRef, useEffect, useCallback } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import { useProjectSelection } from '../../hooks/useProjectSelection'
import { projectPickerLabel } from '../../lib/projectSelection'
import { Folder, ChevronDown, CheckSquare, Square, Settings2 } from 'lucide-react'

const MENU_WIDTH = 250

export function GlobalProjectSelector() {
  const { projects, selectedProjectIds, toggleProject, selectAll, clearAll, isLoading } = useProjectSelection()
  const [isOpen, setIsOpen] = useState(false)
  const dropdownRef = useRef<HTMLDivElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  // The menu renders in a body portal (fixed position) so it overlays the Leaflet map on the
  // Insights page — an absolutely-positioned dropdown loses the stacking war to Leaflet's
  // controls/panes (z-index up to 1000) and gets hidden behind the map.
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)
  const navigate = useNavigate()

  // Anchor the menu under the trigger, right-aligned, clamped to the viewport.
  const place = useCallback(() => {
    const r = dropdownRef.current?.getBoundingClientRect()
    if (r) setPos({ top: r.bottom + 8, left: Math.max(8, r.right - MENU_WIDTH) })
  }, [])

  // Close on outside click — account for the portalled menu (it lives outside dropdownRef).
  useEffect(() => {
    if (!isOpen) return
    const handleClickOutside = (event: MouseEvent) => {
      const t = event.target as Node
      if (dropdownRef.current?.contains(t) || menuRef.current?.contains(t)) return
      setIsOpen(false)
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
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

  return (
    <div className="relative" ref={dropdownRef} style={{ position: 'relative' }}>
      <button
        onClick={() => { if (!isOpen) place(); setIsOpen(o => !o) }}
        className="flex items-center gap-2 px-3 py-1.5 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 transition-colors"
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '0.5rem',
          padding: '0.375rem 0.75rem',
          fontSize: '0.875rem',
          backgroundColor: 'var(--surface, #ffffff)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius)',
          color: 'var(--text-color)',
          cursor: 'pointer'
        }}
      >
        <Folder size={16} style={{ opacity: 0.7 }} />
        <span style={{ maxWidth: '150px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {projectPickerLabel(projects, selectedProjectIds)}
        </span>
        <ChevronDown size={14} style={{ opacity: 0.5 }} />
      </button>

      {isOpen && pos && createPortal(
        <div
          ref={menuRef}
          style={{
            position: 'fixed',
            top: pos.top,
            left: pos.left,
            width: `${MENU_WIDTH}px`,
            backgroundColor: 'var(--bg-color, #ffffff)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius)',
            boxShadow: '0 4px 16px rgba(0, 0, 0, 0.18)',
            // Above Leaflet (controls ~1000) and the upload dock (300).
            zIndex: 2000,
            overflow: 'hidden'
          }}
        >
          <div style={{ padding: '0.5rem', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between' }}>
            <button
              onClick={selectAll}
              style={{ fontSize: '0.75rem', color: 'var(--primary)', background: 'none', border: 'none', cursor: 'pointer' }}
            >
              Select All
            </button>
            <button
              onClick={clearAll}
              style={{ fontSize: '0.75rem', opacity: 0.6, background: 'none', border: 'none', cursor: 'pointer' }}
            >
              Clear
            </button>
          </div>
          <div style={{ maxHeight: '300px', overflowY: 'auto' }}>
            {projects.map(project => {
              const isSelected = selectedProjectIds.includes(project.id)
              return (
                <div
                  key={project.id}
                  onClick={() => toggleProject(project.id)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '0.5rem',
                    padding: '0.5rem 0.75rem',
                    cursor: 'pointer',
                    backgroundColor: isSelected ? 'rgba(var(--primary-rgb, 59, 130, 246), 0.05)' : 'transparent',
                    fontSize: '0.875rem'
                  }}
                  className="hover:bg-gray-50 transition-colors"
                >
                  {isSelected ? (
                    <CheckSquare size={16} color="var(--primary)" />
                  ) : (
                    <Square size={16} style={{ opacity: 0.3 }} />
                  )}
                  <span style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {project.name}
                  </span>
                </div>
              )
            })}
          </div>
          {/* Manage projects → Settings */}
          <div
            onClick={() => { setIsOpen(false); navigate('/settings') }}
            style={{
              display: 'flex', alignItems: 'center', gap: '0.5rem',
              padding: '0.55rem 0.75rem', cursor: 'pointer', fontSize: '0.8125rem',
              borderTop: '1px solid var(--border)', color: 'var(--primary)', fontWeight: 600,
            }}
            className="hover:bg-gray-50 transition-colors"
          >
            <Settings2 size={15} />
            <span>Manage projects…</span>
          </div>
        </div>,
        document.body,
      )}
    </div>
  )
}
