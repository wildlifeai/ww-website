/**
 * Project membership over the ww-backend RPCs.
 *
 * Every read and write goes through a SECURITY DEFINER function that checks
 * the caller itself, never a direct `users` or `user_roles` query: RLS lets a
 * user see only their own `users` row and their own `user_roles` rows, and it
 * silently turns an unauthorised UPDATE into "0 rows". The panel built on the
 * old direct queries showed only the caller and "removed" members it had not.
 *
 * Each function takes the Supabase client as an argument so the integration
 * tests can call it as different signed-in users.
 */
import type { SupabaseClient } from '@supabase/supabase-js'

export type ProjectRole = 'project_admin' | 'project_member' | 'project_viewer'

export const ROLE_LABELS: Record<string, string> = {
  project_admin:  'Admin',
  project_member: 'Member',
  project_viewer: 'Viewer',
}

export interface ProjectMember {
  id:              string
  name:            string
  email:           string
  role:            ProjectRole
  granted_at:      string
  granted_by:      string | null
  granted_by_name: string | null
}

export interface PendingInvitation {
  id:            string
  invitee_email: string
  role:          ProjectRole
  created_at:    string
  expires_at:    string
}

export interface MyInvitation {
  id:           string
  project_id:   string
  project_name: string
  inviter_name: string
  role:         ProjectRole
  created_at:   string
  expires_at:   string
}

export type MembersErrorKind =
  | 'not_allowed'
  | 'already_pending'
  | 'already_member'
  | 'invalid_email'
  | 'last_admin'
  | 'not_a_member'
  | 'invitation_gone'
  | 'unknown'

const MESSAGES: Record<MembersErrorKind, string> = {
  not_allowed:     'Only project admins can do that.',
  already_pending: 'An invitation to this email address is already pending.',
  already_member:  'This person is already a member of this project.',
  invalid_email:   'Enter a valid email address.',
  last_admin:      'A project needs at least one admin. Make someone else an admin first.',
  not_a_member:    'This person is no longer a member of this project.',
  invitation_gone: 'This invitation has expired or was already answered.',
  unknown:         'Something went wrong. Please try again.',
}

export class MembersError extends Error {
  readonly kind: MembersErrorKind
  constructor(kind: MembersErrorKind, detail?: string) {
    super(kind === 'unknown' && detail ? detail : MESSAGES[kind])
    this.kind = kind
  }
}

interface PgError { code?: string; message?: string }

/** Map a PostgREST error to what the user can act on, by the SQLSTATE the
 *  RPCs raise (ww-backend#216). The message fallbacks keep an older backend,
 *  whose invitation RPCs raised plain P0001, mapping the same way. */
export function toMembersError(err: PgError): MembersError {
  const message = err.message ?? ''
  switch (err.code) {
    case '42501': return new MembersError('not_allowed')
    case '23505':
      // Both "already a member" and the pending-invitation unique index raise 23505.
      return new MembersError(/already a member/i.test(message) ? 'already_member' : 'already_pending')
    case '23514': return new MembersError('last_admin')
    case 'P0002': return new MembersError('invitation_gone')
    case '22023':
      if (/not a member/i.test(message)) return new MembersError('not_a_member')
      // Other membership RPCs raise 22023 for an invalid role, so match the email case.
      if (/email/i.test(message)) return new MembersError('invalid_email')
      break
  }
  if (/only project admins/i.test(message)) return new MembersError('not_allowed')
  if (/already a member/i.test(message)) return new MembersError('already_member')
  if (/invalid email/i.test(message)) return new MembersError('invalid_email')
  if (/invitation not found or expired/i.test(message)) return new MembersError('invitation_gone')
  return new MembersError('unknown', message || undefined)
}

/** The server lowercases and trims too (ww-backend#216); doing it here keeps
 *  the confirmation and the existing-member check showing what is stored. */
export function normaliseEmail(raw: string): string {
  return raw.trim().toLowerCase()
}

const EMAIL_SHAPE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

async function call<T>(db: SupabaseClient, fn: string, args?: Record<string, unknown>): Promise<T> {
  const { data, error } = await db.rpc(fn, args)
  if (error) throw toMembersError(error)
  return data as T
}

export function listMembers(db: SupabaseClient, projectId: string): Promise<ProjectMember[]> {
  return call<ProjectMember[]>(db, 'get_project_members', { p_project_id: projectId })
    .then(rows => rows ?? [])
}

/** Project admins only; anyone else gets `not_allowed`. */
export function listPendingInvitations(db: SupabaseClient, projectId: string): Promise<PendingInvitation[]> {
  return call<PendingInvitation[]>(db, 'get_project_pending_invitations', { p_project_id: projectId })
    .then(rows => rows ?? [])
}

/**
 * Invite by email. Whether an account exists for the address is never
 * revealed: the invitation is stored either way and shows up for whoever
 * signs in with that email before it expires.
 */
export async function inviteMember(
  db: SupabaseClient,
  projectId: string,
  rawEmail: string,
  role: ProjectRole,
  currentMembers: ProjectMember[] = [],
): Promise<string> {
  // Both checks save a round trip; the server enforces them anyway.
  const email = normaliseEmail(rawEmail)
  if (!EMAIL_SHAPE.test(email)) throw new MembersError('invalid_email')
  if (currentMembers.some(m => normaliseEmail(m.email ?? '') === email)) {
    throw new MembersError('already_member')
  }
  return call<string>(db, 'send_project_invitation', {
    p_project_id:    projectId,
    p_invitee_email: email,
    p_role:          role,
  })
}

/** Project admins only. An invitation already answered, cancelled or expired
 *  gives `invitation_gone`. */
export async function cancelInvitation(db: SupabaseClient, invitationId: string): Promise<void> {
  await call(db, 'cancel_project_invitation', { p_invitation_id: invitationId })
}

/** `removedBy` must be the signed-in user; the RPC refuses anything else. */
export async function removeMember(
  db: SupabaseClient,
  projectId: string,
  userId: string,
  removedBy: string,
): Promise<void> {
  await call(db, 'remove_project_member', {
    p_project_id: projectId,
    p_user_id:    userId,
    p_removed_by: removedBy,
  })
}

export function listMyInvitations(db: SupabaseClient): Promise<MyInvitation[]> {
  return call<MyInvitation[]>(db, 'get_my_pending_invitations').then(rows => rows ?? [])
}

export async function respondToInvitation(
  db: SupabaseClient,
  invitationId: string,
  accept: boolean,
): Promise<void> {
  await call(db, 'respond_to_invitation', { p_invitation_id: invitationId, p_accept: accept })
}
