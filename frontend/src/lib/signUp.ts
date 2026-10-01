/**
 * Website sign-up (#116, #187): checks before calling Supabase, and the user metadata the
 * ww-backend `handle_new_user` trigger reads.
 *
 * The trigger takes `given_name` and `family_name` first (what Google sends), then `name` split
 * on its first space, then the email. Sending the first and last name separately keeps a
 * two-word first name whole. The new user also joins the General organisation there.
 */

export interface SignUpFields {
  firstName: string
  lastName: string
  email: string
  password: string
}

export const MIN_PASSWORD_LENGTH = 8

/** The first problem with the form, or null when it can be sent. */
export function signUpProblem(f: SignUpFields): string | null {
  if (!f.firstName.trim()) return 'Enter your first name.'
  if (!f.lastName.trim()) return 'Enter your last name.'
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(f.email.trim())) return 'Enter a valid email address.'
  if (f.password.length < MIN_PASSWORD_LENGTH) return `Use a password of at least ${MIN_PASSWORD_LENGTH} characters.`
  return null
}

/** `options.data` for `supabase.auth.signUp`. */
export function signUpMetadata(f: Pick<SignUpFields, 'firstName' | 'lastName'>) {
  const given = f.firstName.trim()
  const family = f.lastName.trim()
  return { given_name: given, family_name: family, name: `${given} ${family}` }
}
