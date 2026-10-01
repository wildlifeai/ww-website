/**
 * Google's own "Sign in with Google" button (Google Identity Services), for LoginPage.
 *
 * With the redirect flow (`signInWithOAuth`) Google's consent screen names the Supabase project's
 * callback domain, `<ref>.supabase.co`, because that is where the sign-in returns. With this
 * button Google hands an ID token straight to the page, which passes it to Supabase
 * (`signInWithIdToken`), so the consent screen names this site instead, and the app's name and
 * logo once Google has verified the brand. The web OAuth client must list each site origin
 * (localhost:5173, dev.ww-website.pages.dev, wildlifewatcher.ai, www.wildlifewatcher.ai,
 * ww-website.pages.dev) under Authorised JavaScript origins.
 *
 * A nonce binds the token to this sign-in: Google receives its SHA-256, Supabase the raw value,
 * and Supabase checks they match, so a token copied from elsewhere cannot be replayed.
 */

export interface GoogleCredentialResponse {
  credential: string
}

export interface GoogleIdentity {
  initialize(config: {
    client_id: string
    callback: (response: GoogleCredentialResponse) => void
    nonce?: string
    ux_mode?: 'popup' | 'redirect'
    use_fedcm_for_button?: boolean
  }): void
  renderButton(
    parent: HTMLElement,
    options: {
      type?: 'standard' | 'icon'
      theme?: 'outline' | 'filled_blue' | 'filled_black'
      size?: 'large' | 'medium' | 'small'
      text?: 'signin_with' | 'signup_with' | 'continue_with' | 'signin'
      shape?: 'rectangular' | 'pill'
      width?: number
      logo_alignment?: 'left' | 'center'
    },
  ): void
}

declare global {
  interface Window {
    google?: { accounts?: { id?: GoogleIdentity } }
  }
}

const SCRIPT_SRC = 'https://accounts.google.com/gsi/client'
let loading: Promise<GoogleIdentity> | null = null

/** Loads Google's script once per page; resolves with `google.accounts.id`. */
export function loadGoogleIdentity(): Promise<GoogleIdentity> {
  if (window.google?.accounts?.id) return Promise.resolve(window.google.accounts.id)
  loading ??= new Promise<GoogleIdentity>((resolve, reject) => {
    const script = document.createElement('script')
    script.src = SCRIPT_SRC
    script.async = true
    script.onload = () => {
      const id = window.google?.accounts?.id
      if (id) resolve(id)
      else reject(new Error('Google sign-in script loaded without google.accounts.id'))
    }
    script.onerror = () => {
      loading = null // let a later visit try again
      reject(new Error('Google sign-in could not load'))
    }
    document.head.appendChild(script)
  })
  return loading
}

/** A fresh nonce: `raw` for Supabase, `hashed` (hex SHA-256 of raw) for Google. */
export async function newNonce(): Promise<{ raw: string; hashed: string }> {
  const bytes = crypto.getRandomValues(new Uint8Array(32))
  const raw = btoa(String.fromCharCode(...bytes))
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(raw))
  const hashed = Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('')
  return { raw, hashed }
}
