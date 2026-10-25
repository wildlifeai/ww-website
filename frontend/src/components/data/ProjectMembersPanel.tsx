/**
 * ProjectMembersPanel
 *
 * Lists a project's members, invites new ones by email, withdraws pending
 * invitations, changes a member's role and removes members,
 * through the ww-backend membership RPCs (see lib/projectMembers.ts for why
 * nothing here queries `users` or `user_roles` directly).
 *
 * Everyone who can see the project sees its members. Inviting, removing,
 * changing a role (click it) and the pending invitations are for project admins
 * (`canManage`, from useProjectRoles, which counts ww_admin as the RPCs do). An
 * admin's own row stays read-only, so nobody demotes or removes themselves here.
 *
 * Adding is an invitation, not a direct grant: the invitee accepts it from
 * the banner (InvitationsBanner) or the mobile app. The confirmation never
 * says whether the email has an account, so the form can't be used to probe
 * who is registered.
 */
import { useMemo, useState } from 'react'
import { useAuth } from '../../hooks/useAuth'
import {
  useCancelInvitation, useChangeMemberRole, useInviteMember, usePendingInvitations, useProjectMembers,
  useRemoveMember,
} from '../../hooks/useProjectMembers'
import { ROLE_LABELS, normaliseEmail, type ProjectMember, type ProjectRole } from '../../lib/projectMembers'
import { DataTable, type Column } from '../ui/DataTable'
import { ConfirmDialog } from '../common/ConfirmDialog'

interface Props {
  projectId:   string
  projectName: string
  /** Project admin of this project (or ww_admin): may invite and remove. */
  canManage:   boolean
}

// The roles an admin can give, Member first as the default. Viewer is the demo account's
// read-only role and is not offered (maintainer, 22 Oct 2026).
const OFFERED_ROLES: ProjectRole[] = ['project_member', 'project_admin']

const BTN: React.CSSProperties = {
  padding: '0.3rem 0.625rem', fontSize: '0.75rem',
  border: '1px solid var(--border)', borderRadius: 'var(--radius)',
  backgroundColor: 'transparent', cursor: 'pointer', color: 'var(--primary)',
  whiteSpace: 'nowrap',
}

const BTN_DANGER: React.CSSProperties = { ...BTN, color: 'var(--error, #f44336)', borderColor: 'var(--error, #f44336)' }

const TH: React.CSSProperties = { textAlign: 'left', padding: '0.5rem', borderBottom: '2px solid var(--border)', fontWeight: 600, whiteSpace: 'nowrap' }

const MUTED: React.CSSProperties = { color: 'var(--text-muted)' }

const ERROR_TEXT: React.CSSProperties = { color: 'var(--error, #f44336)', fontSize: '0.8125rem', marginTop: '0.5rem' }

const displayName = (m: ProjectMember) => m.name?.trim() || m.email

const CHIP: React.CSSProperties = {
  fontSize: '0.75rem', padding: '0.2rem 0.45rem', borderRadius: '4px',
  backgroundColor: 'rgba(76,175,80,0.12)', color: 'var(--primary)',
}

function RoleChip({ role }: { role: string }) {
  return <span style={CHIP}>{ROLE_LABELS[role] ?? role}</span>
}

/** The role chip as a dropdown: picking another role saves it. */
function RoleSelect({ member, busy, onChange }: {
  member: ProjectMember; busy: boolean; onChange: (role: ProjectRole) => void
}) {
  return (
    <select
      aria-label={`Role of ${displayName(member)}`}
      title="Change role"
      value={member.role}
      disabled={busy}
      onChange={e => onChange(e.target.value as ProjectRole)}
      style={{ ...CHIP, border: 'none', fontFamily: 'inherit', cursor: busy ? 'wait' : 'pointer' }}
    >
      {/* A viewer's own role stays listed so the dropdown shows it; it can be changed, not given. */}
      {(OFFERED_ROLES.includes(member.role) ? OFFERED_ROLES : [member.role, ...OFFERED_ROLES]).map(r => (
        <option key={r} value={r}>{ROLE_LABELS[r] ?? r}</option>
      ))}
    </select>
  )
}

export function ProjectMembersPanel({ projectId, projectName, canManage }: Props) {
  const { user } = useAuth()
  const members = useProjectMembers(projectId)
  const pending = usePendingInvitations(projectId, canManage)
  const invite  = useInviteMember(projectId)
  const remove  = useRemoveMember(projectId)
  const cancel  = useCancelInvitation(projectId)
  const changeRole = useChangeMemberRole(projectId)
  const setMemberRole = changeRole.mutate
  const changingId = changeRole.isPending ? changeRole.variables?.userId : undefined

  const [email,    setEmail]    = useState('')
  const [role,     setRole]     = useState<ProjectRole>('project_member')
  const [sentTo,   setSentTo]   = useState<string | null>(null)
  const [removing, setRemoving] = useState<ProjectMember | null>(null)
  const resetRemove = remove.reset

  const rows = useMemo(
    () => (members.data ?? []).toSorted((a, b) => displayName(a).localeCompare(displayName(b))),
    [members.data],
  )

  const columns = useMemo<Column<ProjectMember>[]>(() => {
    const cols: Column<ProjectMember>[] = [
      {
        key: 'name', label: 'Name', hideable: false, getValue: displayName,
        render: m => (
          <span style={{ fontWeight: 500 }}>
            {displayName(m)}
            {m.id === user?.id && <span style={{ ...MUTED, marginLeft: '0.4rem', fontSize: '0.75rem', fontWeight: 400 }}>(you)</span>}
          </span>
        ),
      },
      { key: 'email', label: 'Email', hideable: false, cellStyle: MUTED },
      {
        key: 'role', label: 'Role', hideable: false, getValue: m => ROLE_LABELS[m.role] ?? m.role,
        render: m => canManage && m.id !== user?.id
          ? <RoleSelect member={m} busy={changingId === m.id} onChange={r => setMemberRole({ userId: m.id, role: r })} />
          : <RoleChip role={m.role} />,
      },
      {
        key: 'granted_at', label: 'Added', hideable: false, cellStyle: { ...MUTED, fontSize: '0.75rem' },
        render: m => new Date(m.granted_at).toLocaleDateString(),
      },
    ]
    if (canManage) {
      cols.push({
        key: '_remove', label: '', sortable: false, hideable: false,
        render: m => m.id !== user?.id && (
          <button style={BTN_DANGER} onClick={() => { resetRemove(); setRemoving(m) }} title="Remove from project">
            Remove
          </button>
        ),
      })
    }
    return cols
  }, [canManage, user?.id, resetRemove, changingId, setMemberRole])

  const handleInvite = (e: React.FormEvent) => {
    e.preventDefault()
    setSentTo(null)
    invite.mutate(
      { email, role, members: members.data ?? [] },
      { onSuccess: () => { setSentTo(normaliseEmail(email)); setEmail('') } },
    )
  }

  const confirmRemove = () => {
    if (!removing) return
    remove.mutate(removing.id, { onSuccess: () => setRemoving(null) })
  }

  const inputStyle: React.CSSProperties = {
    padding: '0.4rem 0.5rem', fontSize: '0.8125rem',
    border: '1px solid var(--border)', borderRadius: 'var(--radius)',
    backgroundColor: 'var(--surface)', color: 'var(--text-color)',
  }

  return (
    <div>
      {members.isError && (
        <p style={{ color: 'var(--error)', fontSize: '0.875rem' }}>Could not load project members. {members.error.message}</p>
      )}

      {members.isLoading ? (
        <p style={{ ...MUTED, fontSize: '0.875rem' }}>Loading members…</p>
      ) : members.data && (
        <div style={{ marginBottom: '1.5rem' }}>
          <DataTable<ProjectMember>
            columns={columns}
            rows={rows}
            rowKey={m => m.id}
            searchable={rows.length > 10}
            searchPlaceholder="Search members…"
            emptyMessage="No members."
            pageSize={20}
          />
          {changeRole.isError && <p style={ERROR_TEXT}>{changeRole.error.message}</p>}
        </div>
      )}

      {canManage && (pending.data?.length ?? 0) > 0 && (
        <div style={{ marginBottom: '1.5rem' }}>
          <div style={{ fontWeight: 600, fontSize: '0.875rem', marginBottom: '0.5rem' }}>Pending invitations</div>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8125rem' }}>
            <thead>
              <tr>{['Email', 'Role', 'Sent', 'Expires', ''].map(h => <th key={h} style={TH}>{h}</th>)}</tr>
            </thead>
            <tbody>
              {pending.data!.map(inv => (
                <tr key={inv.id} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={{ padding: '0.5rem' }}>{inv.invitee_email}</td>
                  <td style={{ padding: '0.5rem' }}><RoleChip role={inv.role} /></td>
                  <td style={{ padding: '0.5rem', ...MUTED, fontSize: '0.75rem' }}>{new Date(inv.created_at).toLocaleDateString()}</td>
                  <td style={{ padding: '0.5rem', ...MUTED, fontSize: '0.75rem' }}>{new Date(inv.expires_at).toLocaleDateString()}</td>
                  <td style={{ padding: '0.5rem' }}>
                    <button
                      style={BTN_DANGER}
                      disabled={cancel.isPending}
                      onClick={() => cancel.mutate(inv.id)}
                      title="Withdraw this invitation"
                    >
                      Cancel
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {cancel.isError && <p style={ERROR_TEXT}>{cancel.error.message}</p>}

      {canManage && (
        <div style={{ borderTop: '1px solid var(--border)', paddingTop: '1rem' }}>
          <div style={{ fontWeight: 600, fontSize: '0.875rem', marginBottom: '0.75rem' }}>Invite a member</div>
          <form onSubmit={handleInvite} style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'flex-end' }}>
            <label style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem', fontSize: '0.8125rem' }}>
              <span style={MUTED}>Email address</span>
              <input
                type="email"
                value={email}
                onChange={e => { setEmail(e.target.value); setSentTo(null) }}
                placeholder="member@example.com"
                required
                style={{ ...inputStyle, minWidth: '220px' }}
              />
            </label>
            <label style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem', fontSize: '0.8125rem' }}>
              <span style={MUTED}>Role</span>
              <select
                value={role}
                onChange={e => setRole(e.target.value as ProjectRole)}
                style={{ ...inputStyle, cursor: 'pointer' }}
              >
                {OFFERED_ROLES.map(r => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
              </select>
            </label>
            <button
              type="submit"
              disabled={invite.isPending || !email.trim()}
              style={{
                ...BTN,
                backgroundColor: 'var(--primary)', color: '#fff',
                border: 'none', padding: '0.5rem 1rem', fontSize: '0.875rem',
                opacity: invite.isPending ? 0.6 : 1,
              }}
            >
              {invite.isPending ? 'Sending…' : 'Send invitation'}
            </button>
          </form>
          {invite.isError && <p style={ERROR_TEXT}>{invite.error.message}</p>}
          {sentTo && (
            <p role="status" style={{ fontSize: '0.8125rem', marginTop: '0.5rem' }}>
              Invitation sent. If {sentTo} has a Wildlife Watcher account, they will receive the
              invitation. If not, they need to create an account with that email address first.
            </p>
          )}
        </div>
      )}

      <ConfirmDialog
        open={!!removing}
        title="Remove member"
        confirmLabel="Remove"
        busyLabel="Removing…"
        busy={remove.isPending}
        error={remove.error?.message}
        onConfirm={confirmRemove}
        onCancel={() => setRemoving(null)}
      >
        <p style={{ margin: 0 }}>
          Remove <strong>{removing ? displayName(removing) : ''}</strong> from {projectName}? They lose access to its data.
        </p>
      </ConfirmDialog>
    </div>
  )
}
