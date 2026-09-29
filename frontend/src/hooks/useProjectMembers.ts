/**
 * TanStack Query wrappers over lib/projectMembers. The panel and the
 * invitation banner share these keys, so accepting an invitation refreshes
 * any open member list.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { supabase } from '../config/supabase'
import { useAuth } from './useAuth'
import {
  inviteMember, listMembers, listMyInvitations, listPendingInvitations,
  removeMember, respondToInvitation,
  type ProjectMember, type ProjectRole,
} from '../lib/projectMembers'

const keys = {
  members:       (projectId: string) => ['projectMembers', projectId] as const,
  pending:       (projectId: string) => ['projectInvitations', projectId] as const,
  myInvitations: (userId: string | undefined) => ['myInvitations', userId] as const,
}

export function useProjectMembers(projectId: string) {
  return useQuery({
    queryKey: keys.members(projectId),
    queryFn:  () => listMembers(supabase, projectId),
    enabled:  !!projectId,
  })
}

/** Only project admins may list pending invitations, so pass `isAdmin`. */
export function usePendingInvitations(projectId: string, isAdmin: boolean) {
  return useQuery({
    queryKey: keys.pending(projectId),
    queryFn:  () => listPendingInvitations(supabase, projectId),
    enabled:  !!projectId && isAdmin,
  })
}

export function useInviteMember(projectId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ email, role, members }: { email: string; role: ProjectRole; members: ProjectMember[] }) =>
      inviteMember(supabase, projectId, email, role, members),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.pending(projectId) }),
  })
}

export function useRemoveMember(projectId: string) {
  const qc = useQueryClient()
  const { user } = useAuth()
  return useMutation({
    mutationFn: (userId: string) => removeMember(supabase, projectId, userId, user?.id ?? ''),
    // Refetch rather than filter locally: the list shows what the database
    // holds, not what the panel hoped happened.
    onSettled: () => qc.invalidateQueries({ queryKey: keys.members(projectId) }),
  })
}

export function useMyInvitations() {
  const { user } = useAuth()
  return useQuery({
    queryKey: keys.myInvitations(user?.id),
    queryFn:  () => listMyInvitations(supabase),
    enabled:  !!user,
  })
}

export function useRespondToInvitation(onAccepted?: () => void) {
  const qc = useQueryClient()
  const { user } = useAuth()
  return useMutation({
    mutationFn: ({ id, accept }: { id: string; projectId: string; accept: boolean }) =>
      respondToInvitation(supabase, id, accept),
    onSuccess: (_data, { projectId, accept }) => {
      if (accept) {
        qc.invalidateQueries({ queryKey: keys.members(projectId) })
        onAccepted?.()
      }
    },
    onSettled: () => qc.invalidateQueries({ queryKey: keys.myInvitations(user?.id) }),
  })
}
