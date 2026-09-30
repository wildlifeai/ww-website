/**
 * Observation removal and the right to edit, checked against the database.
 *
 * RLS turns a delete the caller may not make into "0 rows" with no error, so
 * MediaDetail used to report "Removed ✓" for a delete that never happened
 * (#183). ww-backend#222 lets a project member or above delete an observation
 * on a live deployment, the same rule as editing it; `canEditObservations`
 * asks the database that question through `has_project_role`, which knows the
 * whole role hierarchy (project, organisation, system), instead of restating it.
 *
 * Each function takes the Supabase client as an argument so the integration
 * tests can call it as different signed-in users.
 */
import type { SupabaseClient } from '@supabase/supabase-js'

export const NOT_REMOVED = 'Not removed: only project members can remove observations.'

/** Deletes one observation, and throws when nothing was deleted. */
export async function deleteObservation(db: SupabaseClient, obsId: string): Promise<void> {
  const { data, error } = await db.from('observations').delete().eq('id', obsId).select('id')
  if (error) throw new Error(error.message)
  if (!data || data.length === 0) throw new Error(NOT_REMOVED)
}

/** Whether the user may edit and remove observations on this deployment. */
export async function canEditObservations(
  db: SupabaseClient, userId: string, deploymentId: string,
): Promise<boolean> {
  const { data: deployment, error } = await db
    .from('deployments').select('project_id, deleted_at').eq('id', deploymentId).maybeSingle()
  if (error) throw new Error(error.message)
  if (!deployment || deployment.deleted_at) return false
  const { data: allowed, error: roleErr } = await db.rpc('has_project_role', {
    user_id: userId, project_id: deployment.project_id, required_role: 'project_member',
  })
  if (roleErr) throw new Error(roleErr.message)
  return allowed === true
}
