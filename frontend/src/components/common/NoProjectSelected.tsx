import { Link } from 'react-router-dom'
import { useProjectSelection } from '../../hooks/useProjectSelection'
import { useCreatableOrgs } from '../../hooks/useCreatableOrgs'

/**
 * Shown in place of a page's data when the project picker has nothing ticked (#214), or when the
 * user has no project at all, which offers a way to create one where the role allows it.
 */
export function NoProjectSelected() {
  const { projects, selectAll } = useProjectSelection()
  const none = projects.length === 0
  const { canCreate } = useCreatableOrgs(none)
  return (
    <div className="card" style={{ padding: '2rem', textAlign: 'center' }}>
      <p style={{ margin: '0 0 1rem 0', color: 'var(--text-muted)' }}>
        {none
          ? (canCreate === false ? 'You have no projects yet. Ask a project admin to invite you.' : 'You have no projects yet.')
          : 'Nothing to display, you have no project selected.'}
      </p>
      {!none && <button className="btn" onClick={selectAll}>Select all projects</button>}
      {none && canCreate && (
        <Link to="/settings?create=true" className="btn" style={{ textDecoration: 'none' }}>Create a project</Link>
      )}
    </div>
  )
}
