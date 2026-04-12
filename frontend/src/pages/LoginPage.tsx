import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Auth } from '@supabase/auth-ui-react'
import { ThemeSupa } from '@supabase/auth-ui-shared'
import { supabase } from '../config/supabase'
import { useAuth } from '../hooks/useAuth'

export function LoginPage() {
  const { user } = useAuth()
  const navigate = useNavigate()

  useEffect(() => {
    if (user) {
      navigate('/')
    }
  }, [user, navigate])

  return (
    <div style={{ maxWidth: '400px', margin: '4rem auto', padding: '2rem', backgroundColor: 'var(--surface)', borderRadius: '8px', border: '1px solid var(--border)' }}>
      <h2 style={{ textAlign: 'center', marginBottom: '2rem' }}>Login to Wildlife Watcher</h2>
      <Auth
        supabaseClient={supabase}
        appearance={{ theme: ThemeSupa }}
        theme="light"
        providers={['github', 'google']}
        redirectTo={window.location.origin + '/'}
      />
    </div>
  )
}
