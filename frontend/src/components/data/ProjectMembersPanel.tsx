/**
 * ProjectMembersPanel
 *
 * Lists a project's members, invites new ones by email and removes them,
 * through the ww-backend membership RPCs (see lib/projectMembers.ts for why
 * nothing here queries `users` or `user_roles` directly).
 *
 * Adding is an invitation, not a direct grant: the invitee accepts it from
 * the banner (InvitationsBanner) or the mobile app. The confirmation never
 * says whether the email has an account, so the form can't be used to probe
 * who is registered.
 */
import { useState } from 'react'
import { useAuth } from '../../hooks/useAuth'
import {
  useInviteMember, usePendingInvitations, useProjectMembers, useRemoveMember,
} from '../../hooks/useProjectMembers'
import { ROLE_LABELS, normaliseEmail, type ProjectRole } from '../../lib/projectMembers'

interface Props {
  projectId:   string
  projectName: string
}

const BTN: React.CSSProperties = {
  padding: '0.3rem 0.625rem', fontSize: '0.75rem',
  border: '1px solid var(--border)', borderRadius: 'var(--radius)',
  backgroundColor: 'transparent', cursor: 'pointer', color: 'var(--primary)',
  whiteSpace: 'nowrap',
}

const BTN_DANGER: React.CSSProperties = { ...BTN, color: 'var(--error, #f44336)', borderColor: 'var(--error, #f44336)' }

const TH: React.CSSProperties = { textAlign: 'left', padding: '0.5rem', borderBottom: '2px solid var(--border)', fontWeight: 600, whiteSpace: 'nowrap' }

const ERROR_TEXT: React.CSSProperties = { color: 'var(--error, #f44336)', fontSize: '0.8125rem', marginTop: '0.5rem' }

function RoleChip({ role }: { role: string }) {
  return (
    <span style={{ fontSize: '0.75rem', padding: '0.2rem 0.45rem', borderRadius: '4px', backgroundColor: 'rgba(76,175,80,0.12)', color: 'var(--primary)' }}>
      {ROLE_LABELS[role] ?? role}
    </span>
  )
}

export function ProjectMembersPanel({ projectId, projectName }: Props) {
  const { user } = useAuth()
  const members = useProjectMembers(projectId)
  const isAdmin = members.data?.some(m => m.id === user?.id && m.role === 'project_admin') ?? false
  const pending = usePendingInvitations(projectId, isAdmin)
  const invite  = useInviteMember(projectId)
  const remove  = useRemoveMember(projectId)

  const [email,  setEmail]  = useState('')
  const [role,   setRole]   = useState<ProjectRole>('project_member')
  const [sentTo, setSentTo] = useState<string | null>(null)

  const handleInvite = (e: React.FormEvent) => {
    e.preventDefault()
    setSentTo(null)
    invite.mutate(
      { email, role, members: members.data ?? [] },
      { onSuccess: () => { setSentTo(normaliseEmail(email)); setEmail('') } },
    )
  }

  const handleRemove = (userId: string, name: string) => {
    if (!confirm(`Remove ${name} from ${projectName}?`)) return
    remove.mutate(userId)
  }

  const inputStyle: React.CSSProperties = {
    padding: '0.4rem 0.5rem', fontSize: '0.8125rem',
    border: '1px solid var(--border)', borderRadius: 'var(--radius)',
    backgroundColor: 'var(--surface)', color: 'var(--text-color)',
  }

  return (
    <div style={{ marginTop: '1.5rem' }}>
      <h3 style={{ margin: '0 0 1rem 0', fontSize: '1rem' }}>
        Members, {projectName}
      </h3>

      {members.isError && (
        <p style={{ color: 'var(--error)', fontSize: '0.875rem' }}>⚠ Could not load project members. {members.error.message}</p>
      )}

      {members.isLoading ? (
        <p style={{ opacity: 0.5, fontSize: '0.875rem' }}>Loading members…</p>
      ) : members.data && (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8125rem', marginBottom: '1.5rem' }}>
          <thead>
            <tr>
              {['Name', 'Email', 'Role', 'Added', ''].map(h => <th key={h} style={TH}>{h}</th>)}
            </tr>
          </thead>
          <tbody>
            {members.data.map(m => (
              <tr key={m.id} style={{ borderBottom: '1px solid var(--border)' }}>
                <td style={{ padding: '0.5rem', fontWeight: 500 }}>
                  {m.name?.trim() || m.email}
                  {m.id === user?.id && <span style={{ marginLeft: '0.4rem', opacity: 0.5, fontSize: '0.75rem' }}>(you)</span>}
                </td>
                <td style={{ padding: '0.5rem', opacity: 0.7 }}>{m.email}</td>
                <td style={{ padding: '0.5rem' }}><RoleChip role={m.role} /></td>
                <td style={{ padding: '0.5rem', opacity: 0.6, fontSize: '0.75rem' }}>
                  {new Date(m.granted_at).toLocaleDateString()}
                </td>
                <td style={{ padding: '0.5rem' }}>
                  {isAdmin && m.id !== user?.id && (
                    <button
                      style={BTN_DANGER}
                      disabled={remove.isPending}
                      onClick={() => handleRemove(m.id, m.name?.trim() || m.email)}
                      title="Remove from project"
                    >
                      Remove
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {remove.isError && <p style={ERROR_TEXT}>⚠ {remove.error.message}</p>}

      {isAdmin && (pending.data?.length ?? 0) > 0 && (
        <div style={{ marginBottom: '1.5rem' }}>
          <div style={{ fontWeight: 600, fontSize: '0.875rem', marginBottom: '0.5rem' }}>Pending invitations</div>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8125rem' }}>
            <thead>
              <tr>{['Email', 'Role', 'Sent', 'Expires'].map(h => <th key={h} style={TH}>{h}</th>)}</tr>
            </thead>
            <tbody>
              {pending.data!.map(inv => (
                <tr key={inv.id} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={{ padding: '0.5rem', opacity: 0.8 }}>{inv.invitee_email}</td>
                  <td style={{ padding: '0.5rem' }}><RoleChip role={inv.role} /></td>
                  <td style={{ padding: '0.5rem', opacity: 0.6, fontSize: '0.75rem' }}>{new Date(inv.created_at).toLocaleDateString()}</td>
                  <td style={{ padding: '0.5rem', opacity: 0.6, fontSize: '0.75rem' }}>{new Date(inv.expires_at).toLocaleDateString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {isAdmin && (
        <div style={{ borderTop: '1px solid var(--border)', paddingTop: '1rem' }}>
          <div style={{ fontWeight: 600, fontSize: '0.875rem', marginBottom: '0.75rem' }}>Invite a member</div>
          <form onSubmit={handleInvite} style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'flex-end' }}>
            <label style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem', fontSize: '0.8125rem' }}>
              <span style={{ opacity: 0.7 }}>Email address</span>
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
              <span style={{ opacity: 0.7 }}>Role</span>
              <select
                value={role}
                onChange={e => setRole(e.target.value as ProjectRole)}
                style={{ ...inputStyle, cursor: 'pointer' }}
              >
                <option value="project_member">Member</option>
                <option value="project_admin">Admin</option>
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
          {invite.isError && <p style={ERROR_TEXT}>⚠ {invite.error.message}</p>}
          {sentTo && (
            <p role="status" style={{ fontSize: '0.8125rem', marginTop: '0.5rem' }}>
              Invitation sent. If {sentTo} has a Wildlife Watcher account, they will receive the
              invitation. If not, they need to create an account with that email address first.
            </p>
          )}
        </div>
      )}
    </div>
  )
}
