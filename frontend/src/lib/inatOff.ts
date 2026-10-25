// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Kept apart from lib/inatPublish.ts: useINat runs on every signed-in page (InatAutoSync), so
// whatever it imports lands in the first load.

/**
 * True when the answer says iNaturalist is switched off on this server: every /api/inat route
 * answers 404 "iNaturalist integration is not enabled" while FF_INAT_ENABLED is off, and
 * apiClient keeps the message, not the status.
 */
export function isInatOff(err: unknown): boolean {
  const msg = String((err as { message?: unknown } | null)?.message ?? err)
  return (err as { code?: unknown } | null)?.code === 'HTTP_404' || msg.includes('404') || /not enabled/i.test(msg)
}
