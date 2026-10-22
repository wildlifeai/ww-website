// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// The projects the signed-in user can write to (project_member or above), for the controls that
// hide for a viewer (UI audit decision 1): the header Upload button, Home's upload step, the
// Toolkit's Prepare SD card and the SD card builder's project list. The demo account is a viewer.
import { useMemo } from 'react'
import { useAuth } from './useAuth'
import { useProjectRoles } from './useProjectRoles'
import { useProjectSelection, type Project } from './useProjectSelection'

export interface WritableProjects {
  /** The user's projects they can write to, in the picker's order. Empty while loading. */
  writable: Project[]
  /** At least one project accepts the user's writes. False while loading. */
  canWriteAny: boolean
  loading: boolean
}

export function useWritableProjects(): WritableProjects {
  const { isDemo } = useAuth()
  const roles = useProjectRoles()
  const { projects, isLoading } = useProjectSelection()
  const loading = roles.loading || isLoading
  const { canWrite } = roles

  const writable = useMemo(
    () => (loading || isDemo ? [] : projects.filter(p => canWrite(p.id))),
    [loading, isDemo, projects, canWrite],
  )
  return { writable, canWriteAny: writable.length > 0, loading }
}
