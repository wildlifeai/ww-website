// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// The review grid's pager (UI audit 02 #5, #7, #24). The total is counted once per filter set,
// not on every page turn or refresh, so it can lag rows that arrived since (an upload in
// progress): the pager never reads less than the page shows.

/** Photos per page. */
export const PAGE_SIZE = 100

export interface PageWindow {
  /** First and last photo on the page, 1-based; 0 and 0 for an empty page. */
  from: number
  to: number
  /** The total to show, or null before the first count. */
  total: number | null
  hasPrev: boolean
  hasNext: boolean
}

export function pageWindow(page: number, shown: number, total: number | null, pageSize = PAGE_SIZE): PageWindow {
  const start = page * pageSize
  const to = start + shown
  const known = total === null ? null : Math.max(total, to)
  return {
    from: shown > 0 ? start + 1 : 0,
    to,
    total: known,
    hasPrev: page > 0,
    hasNext: known === null ? shown >= pageSize : to < known,
  }
}
