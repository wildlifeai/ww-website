// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Read the answer of DELETE /api/media/batch (#308). The route runs as the user, so RLS deletes a
// photo only for its uploader (while at least a project member) or a project admin. Any other id
// is skipped without an error and comes back in `skipped_ids`, so the grid removes, and Undo
// restores, only `deleted_ids`.

export interface MediaDeleteResponse {
  deleted_at?: string
  deleted_ids?: string[]
  skipped_ids?: string[]
}

export interface MediaDeleteOutcome {
  deletedIds: string[]
  skipped: number
  deletedAt: string | null
  /** For the Undo toast, or the error when nothing was deleted. */
  message: string
}

const RULE = 'only the uploader or a project admin can delete them'

const photos = (n: number) => `${n} photo${n !== 1 ? 's' : ''}`

export function mediaDeleteOutcome(requested: string[], res: MediaDeleteResponse | undefined): MediaDeleteOutcome {
  // An API without deleted_ids (before #308) deleted or skipped silently: assume all went.
  const deletedIds = res?.deleted_ids ?? requested
  const skipped = res?.skipped_ids?.length ?? 0
  const deletedAt = res?.deleted_at ?? null
  let message = `Deleted ${photos(deletedIds.length)}`
  if (deletedIds.length === 0) message = `No photos were deleted: ${RULE}`
  else if (skipped) message += `. ${photos(skipped)} not deleted: ${RULE}`
  return { deletedIds, skipped, deletedAt, message }
}
