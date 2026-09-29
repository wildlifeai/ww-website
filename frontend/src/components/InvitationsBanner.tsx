/**
 * InvitationsBanner lists the signed-in user's pending project invitations
 * with Accept and Decline. Until this existed only the mobile app could
 * accept one, so a website-only user invited from the members panel had no
 * way in. Backed by `get_my_pending_invitations` / `respond_to_invitation`.
 */
import { useMyInvitations, useRespondToInvitation } from '../hooks/useProjectMembers'
import { useProjectSelection } from '../hooks/useProjectSelection'
import { ROLE_LABELS } from '../lib/projectMembers'

const BTN: React.CSSProperties = {
  padding: '0.3rem 0.75rem', fontSize: '0.8rem', borderRadius: 'var(--radius)',
  cursor: 'pointer', whiteSpace: 'nowrap',
}

export function InvitationsBanner() {
  const { reloadProjects } = useProjectSelection()
  const invitations = useMyInvitations()
  const respond = useRespondToInvitation(reloadProjects)

  if (!invitations.data?.length) return null

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', margin: '0 0 1rem' }}>
      {invitations.data.map(inv => (
        <div
          key={inv.id}
          style={{
            display: 'flex', alignItems: 'center', gap: '0.6rem', flexWrap: 'wrap',
            padding: '0.6rem 1rem', fontSize: '0.85rem', borderRadius: 'var(--radius)',
            background: 'rgba(76,175,80,0.1)', border: '1px solid rgba(76,175,80,0.4)',
          }}
        >
          <span style={{ flex: 1, minWidth: '14rem' }}>
            {inv.inviter_name} invited you to join <strong>{inv.project_name}</strong> as{' '}
            {(ROLE_LABELS[inv.role] ?? inv.role).toLowerCase()}.
          </span>
          <button
            disabled={respond.isPending}
            onClick={() => respond.mutate({ id: inv.id, projectId: inv.project_id, accept: true })}
            style={{ ...BTN, border: 'none', backgroundColor: 'var(--primary)', color: '#fff' }}
          >
            Accept
          </button>
          <button
            disabled={respond.isPending}
            onClick={() => respond.mutate({ id: inv.id, projectId: inv.project_id, accept: false })}
            style={{ ...BTN, border: '1px solid var(--border)', backgroundColor: 'transparent', color: 'var(--text-color)' }}
          >
            Decline
          </button>
        </div>
      ))}
      {respond.isError && (
        <p style={{ color: 'var(--error, #f44336)', fontSize: '0.8125rem', margin: 0 }}>⚠ {respond.error.message}</p>
      )}
    </div>
  )
}
