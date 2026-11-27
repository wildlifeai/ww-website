// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// A camera's LoRaWAN DevEUI, set or corrected by a ww_admin on Admin Devices (#525).
//
// ww-backend's lorawan-ingest links a message to a camera by `devices.device_eui`, matched
// case-insensitively among live cameras with `maybeSingle`, so a camera with none never reaches
// its project, and two cameras sharing one leave both unlinked. The column has no format check
// and no unique index (ww-backend `26_devices.sql`), so this file holds both rules and checks
// before writing. Only a ww_admin may change the column (`lock_device_columns`, 42501 otherwise).
// The form stored is The Things Network's: 16 hex digits, upper case, no separators. The app
// refuses the same two placeholders at registration (ww-mobile-app `cameraRegistration.ts`).
import type { SupabaseClient } from '@supabase/supabase-js'

/** The firmware's built-in DevEUI, shared by every camera not given its own. */
export const DEFAULT_DEVICE_EUI = '0080E10101010101'

export const EUI_FORMAT = 'A DevEUI is 16 hex digits, for example 70B3D57ED0012345.'
export const NOT_ALLOWED = "Only a platform admin can change a camera's DevEUI."

/** The EUI as stored, or the reason it cannot be. Colons, dashes and spaces are dropped. */
export function checkEui(input: string): { eui: string } | { error: string } {
  const eui = input.replace(/[\s:-]/g, '').toUpperCase()
  if (!/^[0-9A-F]{16}$/.test(eui)) return { error: EUI_FORMAT }
  if (/^0+$/.test(eui)) return { error: "All zeros is not a camera's own DevEUI." }
  if (eui === DEFAULT_DEVICE_EUI) return { error: `${DEFAULT_DEVICE_EUI} is the firmware's shared default, not this camera's own.` }
  return { eui }
}

/** Another live camera with this EUI, in any case, or null. */
export async function findEuiOwner(
  db: SupabaseClient, eui: string, deviceId: string,
): Promise<{ id: string; name: string } | null> {
  const { data, error } = await db.from('devices').select('id, name').ilike('device_eui', eui).neq('id', deviceId).limit(1)
  if (error) throw new Error(error.message)
  return ((data ?? [])[0] as { id: string; name: string } | undefined) ?? null
}

/**
 * Checks the EUI, refuses one another live camera has, saves it as the user and returns it as
 * stored. Throws the message to show. RLS refuses a non-admin's update as 0 rows, the trigger as
 * 42501.
 */
export async function saveDeviceEui(db: SupabaseClient, deviceId: string, input: string): Promise<string> {
  const checked = checkEui(input)
  if ('error' in checked) throw new Error(checked.error)
  const owner = await findEuiOwner(db, checked.eui, deviceId)
  if (owner) throw new Error(`${owner.name} already has this DevEUI.`)
  const { data, error } = await db.from('devices').update({ device_eui: checked.eui }).eq('id', deviceId).select('id, device_eui')
  if (error) throw new Error(error.code === '42501' ? NOT_ALLOWED : `Not saved: ${error.message}`)
  const saved = (data as { device_eui: string | null }[] | null)?.[0]
  if (!saved?.device_eui) throw new Error(NOT_ALLOWED)
  return saved.device_eui
}
