// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// useLabelChecks: the open label-check flags of the projects in view (#448), read under
// ww-backend's label_checks policy (a reader of the observation reads its flags), and
// useMarkChecked: "Checked, it's right", which a project member may set (decision 9).
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { supabase } from '../config/supabase'
import { LABEL_CHECK_SELECT, toLabelCheck, type LabelCheck, type LabelCheckRow } from '../lib/labelChecks'
import { chunk, readAllPages } from '../lib/readAllPages'
import { useAuth } from './useAuth'

const KEY = 'label-checks'

export function useLabelChecks(projectIds: readonly string[], enabled: boolean) {
  const ids = projectIds.toSorted()
  return useQuery({
    queryKey: [KEY, ids],
    queryFn: async (): Promise<LabelCheck[]> => {
      const parts = await Promise.all(chunk(ids, 50).map(part => readAllPages<LabelCheckRow>((from, to) =>
        supabase.from('label_checks').select(LABEL_CHECK_SELECT).in('project_id', part).is('outcome', null).order('id').range(from, to)
          .overrideTypes<LabelCheckRow[], { merge: false }>())))
      return parts.flat().flatMap(row => toLabelCheck(row) ?? [])
    },
    enabled: enabled && ids.length > 0,
  })
}

export function useMarkChecked() {
  const qc = useQueryClient()
  const { user } = useAuth()
  return useMutation({
    mutationFn: async (checkId: string) => {
      const { data, error } = await supabase.from('label_checks')
        .update({ outcome: 'checked', checked_by: user?.id ?? null, checked_at: new Date().toISOString() })
        .eq('id', checkId).select('id')
      if (error) throw new Error(error.message)
      // RLS refuses an update as "0 rows, no error".
      if (!data?.length) throw new Error('Only a project member can mark a label checked.')
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: [KEY] }),
  })
}
