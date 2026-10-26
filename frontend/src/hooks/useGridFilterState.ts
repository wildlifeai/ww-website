// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// useGridFilterState: the review grid's query choices. The picked deployments, the filters (all
// run in the media query, lib/gridFilters), the Advanced filters draft (applied in one query on
// Apply), whether the option lists are wanted yet, and the order.
import { useEffect, useState } from 'react'
import { NO_FILTERS, type GridFilters } from '../lib/gridFilters'

export function useGridFilterState(initialSpecies: string | undefined, initialDeploymentId: string | undefined) {
  const [deployments, setDeployments] = useState<string[]>([])
  const [filters, setFilters] = useState<GridFilters>(() => ({ ...NO_FILTERS, species: initialSpecies ? [initialSpecies] : [] }))
  const [draft, setDraft] = useState<GridFilters | null>(null)
  const [wantSpeciesOptions, setWantSpeciesOptions] = useState(false)
  const [orderBy, setOrderBy] = useState<'' | 'review'>('')

  // Honour ?deployment=<id>[,<id>...] from the upload redirect and the dock link. One id focuses
  // that deployment; several (an upload that spanned deployments) select them all, and the
  // deployment pill reads "N deployments". A new deployment choice starts on page 1 by itself.
  useEffect(() => {
    if (!initialDeploymentId) return
    const ids = initialDeploymentId.split(',').map(s => s.trim()).filter(Boolean)
    // eslint-disable-next-line react-hooks/set-state-in-effect -- the link's choice replaces the user's
    if (ids.length) setDeployments(ids)
  }, [initialDeploymentId])

  return {
    deployments, setDeployments, filters, setFilters, draft, setDraft,
    wantSpeciesOptions, setWantSpeciesOptions, orderBy, setOrderBy,
  }
}
