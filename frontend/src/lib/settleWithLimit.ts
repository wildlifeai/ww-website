// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later

/**
 * `Promise.allSettled(items.map(fn))` with at most `limit` calls in flight, results in input
 * order. For the pages that still make one API call per item until a batch endpoint exists
 * (cluster reassign, Dataset Health's QA reports), so 500 items are not 500 requests at once.
 */
export async function settleWithLimit<T, R>(
  items: readonly T[],
  limit: number,
  fn: (item: T) => Promise<R>,
): Promise<PromiseSettledResult<R>[]> {
  const out: PromiseSettledResult<R>[] = new Array(items.length)
  let next = 0
  const worker = async () => {
    while (next < items.length) {
      const i = next++
      try {
        out[i] = { status: 'fulfilled', value: await fn(items[i]) }
      } catch (reason) {
        out[i] = { status: 'rejected', reason }
      }
    }
  }
  await Promise.all(Array.from({ length: Math.max(1, Math.min(limit, items.length)) }, worker))
  return out
}
