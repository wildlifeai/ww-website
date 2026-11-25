/**
 * A camera's state on the Realtime page (/field), one function for the table, the attention list
 * and (later) the map, so ww-website#427 changes the rules here and nowhere else.
 *
 * Today: Finished once its deployment has ended (the page shows those that ended today), never
 * Silent or Stale; otherwise by the age of its newest LoRaWAN message, Fresh within 24 h, Stale
 * within 72 h, Silent after that, and Awaiting before the first.
 */

export type CameraState = 'fresh' | 'stale' | 'silent' | 'awaiting' | 'finished'

export const FRESH_HOURS = 24
export const STALE_HOURS = 72

export function cameraState(
  camera: { lastSeen: string | null; deploymentEnd: string | null },
  now = Date.now(),
): CameraState {
  if (camera.deploymentEnd && Date.parse(camera.deploymentEnd) <= now) return 'finished'
  if (!camera.lastSeen) return 'awaiting'
  const hours = (now - Date.parse(camera.lastSeen)) / 3_600_000
  if (hours <= FRESH_HOURS) return 'fresh'
  if (hours <= STALE_HOURS) return 'stale'
  return 'silent'
}

export const CAMERA_STATE: Record<CameraState, { color: string; label: string }> = {
  fresh:    { color: '#22c55e', label: 'Fresh' },
  stale:    { color: '#f59e0b', label: 'Stale' },
  silent:   { color: '#ef4444', label: 'Silent' },
  awaiting: { color: '#9ca3af', label: 'Awaiting' },
  finished: { color: '#3b82f6', label: 'Finished' },
}
