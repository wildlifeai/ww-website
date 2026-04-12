import { useEffect, useState } from 'react'
import { supabase } from '../config/supabase'
import type { User } from '@supabase/supabase-js'

export function useAuth() {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    supabase.auth.getSession().then(({ data: { session } }) => {
      setUser(session?.user ?? null)
      setLoading(false)
    })

    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, session) => {
      setUser(session?.user ?? null)
    })

    return () => subscription.unsubscribe()
  }, [])

  const login = async () => {
    // For now we'll just redirect to the Supabase hosted UI or use magic link/OAuth
    // In a real setup, you might want to ask for email. We'll use magic link with prompt
    const email = window.prompt("Enter your email to login via Magic Link:")
    if (email) {
      const { error } = await supabase.auth.signInWithOtp({ email })
      if (error) alert("Error: " + error.message)
      else alert("Check your email for the login link!")
    }
  }

  const logout = async () => {
    await supabase.auth.signOut()
  }

  return { user, loading, login, logout }
}
