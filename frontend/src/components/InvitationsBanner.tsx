/**
 * InvitationsBanner lists the signed-in user's pending project invitations
 * with Accept and Decline. Until this existed only the mobile app could
 * accept one, so a website-only user invited from the members panel had no
 * way in. Backed by `get_my_pending_invitations` / `respond_to_invitation`.
 */
import { useMyInvitations, useRespondToInvitation } from '../hooks/useProjectMembers'
import { useProjectSelection } from '../hooks/useProjectSelection'
import { ROLE_LABELS } from '../lib/projectMembers'
import { TriangleAlert, UserPlus } from 'lucide-react'
import { Banner } from './common/Banner'

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
    <>
      {invitations.data.map(inv => (
        <Banner
          key={inv.id}
          icon={UserPlus}
          actions={(
            <>
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
            </>
          )}
        >
          {inv.inviter_name} invited you to join <strong>{inv.project_name}</strong> as{' '}
          {(ROLE_LABELS[inv.role] ?? inv.role).toLowerCase()}.
        </Banner>
      ))}
      {respond.isError && (
        <Banner icon={TriangleAlert} tone="warning">{respond.error.message}</Banner>
      )}
    </>
  )
}
