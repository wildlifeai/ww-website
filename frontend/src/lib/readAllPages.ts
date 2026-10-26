/**
 * Reads every row of a PostgREST query, page by page.
 *
 * PostgREST stops at 1,000 rows without an error, so a list that can grow past that pages with
 * `.range()` until a short page (gotchas.md). `page(from, to)` builds the query afresh for each
 * range; give it a stable order with a unique column last, or rows repeat or go missing between
 * pages. Observations have their own reader, `fetchLiveObservations`. A long id list goes to
 * the database in `chunk`s, so no request URL grows with the number of ids.
 */
import { PAGE_SIZE } from './liveObservations'

export interface PageResult<T> {
  data: T[] | null
  error: { message: string } | null
}

/** Every row, across as many pages as it takes. Throws the first page's error. */
export async function readAllPages<T>(
  page: (from: number, to: number) => PromiseLike<PageResult<T>>,
  pageSize = PAGE_SIZE,
): Promise<T[]> {
  const rows: T[] = []
  for (let from = 0; ; from += pageSize) {
    const { data, error } = await page(from, from + pageSize - 1)
    if (error) throw new Error(error.message)
    const got = data ?? []
    rows.push(...got)
    if (got.length < pageSize) return rows
  }
}

/** `items` in runs of at most `size`. */
export function chunk<T>(items: T[], size: number): T[][] {
  const out: T[][] = []
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size))
  return out
}
