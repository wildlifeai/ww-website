import { useEffect, useState } from 'react'
import { supabase } from '../config/supabase'
import { apiClient } from '../lib/apiClient'
import { nextAuthUser } from '../lib/authUser'
import type { User } from '@supabase/supabase-js'

/** Mint a session for the shared read-only demo account and sign in.
 *
 * The backend does the actual sign-in (the demo password never ships in the
 * bundle); each call creates a fresh Supabase session, so concurrent demo
 * visitors don't share a refresh-token family. Once setSession() runs, the
 * onAuthStateChange listener below fires and the app behaves exactly as if
 * the user had logged in normally.
 */
export async function loginAsDemo() {
  const res = await apiClient.post('/api/auth/demo-session')
  const { access_token, refresh_token } = res.data
  const { error } = await supabase.auth.setSession({ access_token, refresh_token })
  if (error) throw error
}

export function useAuth() {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    supabase.auth.getSession().then(({ data: { session } }) => {
      setUser(prev => nextAuthUser(prev, session?.user ?? null))
      setLoading(false)
    })

    // Token refreshes re-send the same user; keep the object so effects keyed on it stay put (#154).
    const { data: { subscription } } = supabase.auth.onAuthStateChange((event, session) => {
      setUser(prev => nextAuthUser(prev, session?.user ?? null, event))
    })

    return () => subscription.unsubscribe()
  }, [])

  const logout = async () => {
    await supabase.auth.signOut()
  }

  // The demo account is flagged via app_metadata by scripts/seed_demo.py.
  // Used only for UI affordances (banner, hiding upload); enforcement is RLS.
  const isDemo = user?.app_metadata?.is_demo === true

  return { user, loading, logout, isDemo }
}
