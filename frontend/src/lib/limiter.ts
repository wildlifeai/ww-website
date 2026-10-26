// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later

/**
 * A queue that runs at most `limit` tasks at a time, in the order they were given; the rest wait
 * their turn. Unlike `settleWithLimit` it is shared by separate calls, so tasks started from
 * different places (the review grid's Retry, its automatic retry, the viewer's Retry) share one
 * cap. A task's result or error is its own promise's.
 */
export function createLimiter(limit: number): <T>(task: () => Promise<T>) => Promise<T> {
  let running = 0
  const waiting: (() => void)[] = []
  const next = () => {
    if (running >= Math.max(1, limit)) return
    const start = waiting.shift()
    if (start) start()
  }
  return task => new Promise((resolve, reject) => {
    waiting.push(() => {
      running++
      task().then(resolve, reject).finally(() => {
        running--
        next()
      })
    })
    next()
  })
}
