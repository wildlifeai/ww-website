import { useProjectSelection } from '../../hooks/useProjectSelection'

/** Shown in place of a page's data when the project picker has nothing ticked (#214). */
export function NoProjectSelected() {
  const { projects, selectAll } = useProjectSelection()
  return (
    <div className="card" style={{ padding: '2rem', textAlign: 'center' }}>
      <p style={{ margin: '0 0 1rem 0', opacity: 0.7 }}>Nothing to display, you have no project selected.</p>
      {projects.length > 0 && <button className="btn" onClick={selectAll}>Select all projects</button>}
    </div>
  )
}
