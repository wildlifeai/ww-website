import { describe, expect, it, vi } from 'vitest'
import type { SupabaseClient } from '@supabase/supabase-js'
import {
  inviteMember, normaliseEmail, removeMember, toMembersError,
  type ProjectMember,
} from './projectMembers'

function fakeDb(result: { data?: unknown; error?: unknown } = { data: 'inv-1', error: null }) {
  const rpc = vi.fn().mockResolvedValue({ data: null, error: null, ...result })
  return { db: { rpc } as unknown as SupabaseClient, rpc }
}

const member = (email: string): ProjectMember => ({
  id: 'u1', name: 'Kiri Tane', email, role: 'project_member',
  granted_at: '2026-09-01T00:00:00Z', granted_by: null, granted_by_name: null,
})

describe('normaliseEmail', () => {
  it('trims and lowercases, because respond_to_invitation compares case-sensitively', () => {
    expect(normaliseEmail('  Kiri.Tane@Example.ORG ')).toBe('kiri.tane@example.org')
  })
})

describe('toMembersError', () => {
  it.each([
    [{ code: '42501', message: 'Unauthorized: p_removed_by must be the calling user' }, 'not_allowed'],
    [{ code: '23505', message: 'duplicate key value violates unique constraint "idx_unique_pending_invitation"' }, 'already_pending'],
    [{ code: '23514', message: 'Cannot remove the last project admin' }, 'last_admin'],
    [{ code: '22023', message: 'User is not a member of this project' }, 'not_a_member'],
    [{ code: 'P0001', message: 'Only project admins can send invitations' }, 'not_allowed'],
    [{ code: 'P0001', message: 'Only project admins can view invitations' }, 'not_allowed'],
    [{ code: 'P0001', message: 'Invitation not found or expired' }, 'invitation_gone'],
    [{ code: 'PGRST202', message: 'Could not find the function' }, 'unknown'],
  ])('maps %o to %s', (err, kind) => {
    expect(toMembersError(err).kind).toBe(kind)
  })

  it('keeps the server message for errors it does not recognise', () => {
    expect(toMembersError({ code: 'XX000', message: 'boom' }).message).toBe('boom')
  })
})

describe('inviteMember', () => {
  it('sends the normalised email to send_project_invitation', async () => {
    const { db, rpc } = fakeDb()
    await expect(inviteMember(db, 'p1', ' Kiri@Example.org', 'project_member')).resolves.toBe('inv-1')
    expect(rpc).toHaveBeenCalledWith('send_project_invitation', {
      p_project_id: 'p1', p_invitee_email: 'kiri@example.org', p_role: 'project_member',
    })
  })

  it('refuses an existing member without calling the server', async () => {
    const { db, rpc } = fakeDb()
    await expect(inviteMember(db, 'p1', 'KIRI@example.org', 'project_member', [member('kiri@example.org')]))
      .rejects.toMatchObject({ kind: 'already_member' })
    expect(rpc).not.toHaveBeenCalled()
  })

  it('refuses something that is not an email address', async () => {
    const { db, rpc } = fakeDb()
    await expect(inviteMember(db, 'p1', 'kiri', 'project_member')).rejects.toMatchObject({ kind: 'invalid_email' })
    expect(rpc).not.toHaveBeenCalled()
  })

  it('turns a repeat invitation into already_pending', async () => {
    const { db } = fakeDb({ data: null, error: { code: '23505', message: 'duplicate key' } })
    await expect(inviteMember(db, 'p1', 'kiri@example.org', 'project_member')).rejects.toMatchObject({ kind: 'already_pending' })
  })
})

describe('removeMember', () => {
  it('passes the caller as p_removed_by', async () => {
    const { db, rpc } = fakeDb({ data: { success: true } })
    await removeMember(db, 'p1', 'u2', 'me')
    expect(rpc).toHaveBeenCalledWith('remove_project_member', { p_project_id: 'p1', p_user_id: 'u2', p_removed_by: 'me' })
  })
})
