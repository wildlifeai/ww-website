/**
 * UploadQuotaBanner: soft warning shown when the signed-in user is over their
 * upload quota. Uploads are never blocked (soft enforcement); this just nudges
 * the user to contact an admin. Backed by the `my_upload_usage` RPC.
 */
/* eslint-disable react-hooks/set-state-in-effect */
import { useEffect, useState } from 'react'
import { supabase } from '../config/supabase'
import { useAuth } from '../hooks/useAuth'
import { Banner } from './ui/Banner'

interface MyUsage {
  photos_uploaded: number
  max_photos: number | null
  over_quota: boolean
}

export function UploadQuotaBanner() {
  const { user } = useAuth()
  const [usage, setUsage] = useState<MyUsage | null>(null)
  const [dismissed, setDismissed] = useState(false)

  useEffect(() => {
    if (!user) { setUsage(null); return }
    let cancelled = false
    supabase.rpc('my_upload_usage').then(({ data }) => {
      if (cancelled) return
      const row = Array.isArray(data) ? data[0] : data
      setUsage(row ? (row as MyUsage) : null)
    })
    return () => { cancelled = true }
  }, [user])

  if (!usage?.over_quota || dismissed) return null

  return (
    <Banner tone="warning" onClose={() => setDismissed(true)}>
      You've exceeded a usage limit
      {usage.max_photos != null && usage.photos_uploaded > usage.max_photos && <> (<strong>{usage.photos_uploaded.toLocaleString()}</strong> / {usage.max_photos.toLocaleString()} photos)</>}.
      Everything still works, but please contact an administrator to review your quota.
    </Banner>
  )
}
