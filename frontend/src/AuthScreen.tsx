import { useState } from 'react'
import { authClient, refreshSession, type Invitation } from './auth'

const ROLE_NAME: Record<string, string> = {
  patient: 'patient',
  doctor: 'doctor',
  nurse: 'nurse',
  front_desk: 'front desk',
  clinic_admin: 'clinic admin',
}

type Mode = 'sign-in' | 'sign-up' | 'verify'

// Sign in, create an account, or verify the email address with the 6-digit code Neon Auth
// emails (Neon Auth: email + password, "verify at sign-up" with verification codes).
// `verifyEmail` is set for a signed-in user whose address is not verified yet.
export function AuthScreen({
  onSignedIn,
  invitation,
  verifyEmail,
}: {
  onSignedIn: () => void
  invitation?: Invitation | null
  verifyEmail?: string
}) {
  const [mode, setMode] = useState<Mode>(verifyEmail ? 'verify' : invitation ? 'sign-up' : 'sign-in')
  const [name, setName] = useState('')
  const [email, setEmail] = useState(verifyEmail ?? invitation?.email ?? '')
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [info, setInfo] = useState<string | null>(
    verifyEmail ? `Verify ${verifyEmail} to continue: send a code, then enter it here.` : null,
  )

  const sendCode = async () => {
    setError(null)
    const { error } = await authClient.emailOtp.sendVerificationOtp({ email, type: 'email-verification' })
    if (error) setError(error.message ?? 'Could not send the code. Please try again.')
    else setInfo(`We emailed a 6-digit code to ${email}. It expires in 15 minutes.`)
  }

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      if (mode === 'sign-up') {
        const { data, error } = await authClient.signUp.email({ email, password, name })
        if (error) return setError(error.message ?? 'Could not create the account.')
        if (data?.user && !data.user.emailVerified) {
          setMode('verify')
          setInfo(`We emailed a 6-digit code to ${email}. Enter it below to finish creating your account.`)
          return
        }
        return onSignedIn()
      }

      if (mode === 'sign-in') {
        const { error } = await authClient.signIn.email({ email, password })
        if (!error) return onSignedIn()
        if (error.status === 403 || /verif/i.test(error.message ?? '')) {
          setMode('verify')
          await sendCode()
          return
        }
        return setError(error.message ?? 'Could not sign in. Please try again.')
      }

      // verify
      const { error } = await authClient.emailOtp.verifyEmail({ email, otp: code.trim() })
      if (error) return setError(error.message ?? 'That code did not work. Check it or send a new one.')
      if (password) {
        const signedIn = await authClient.signIn.email({ email, password })
        if (signedIn.error) return setError(signedIn.error.message ?? 'Verified. Please sign in.')
      } else {
        await refreshSession() // a signed-in user: get a token that says "verified"
      }
      onSignedIn()
    } finally {
      setBusy(false)
    }
  }

  const switchMode = () => {
    setMode((current) => (current === 'sign-in' ? 'sign-up' : 'sign-in'))
    setError(null)
    setInfo(null)
  }

  return (
    <div className="auth-page">
      <div className="card auth-card">
        <h1>Patient intake</h1>
        <p className="subtitle">
          {mode === 'sign-in' ? 'Sign in to continue' : mode === 'sign-up' ? 'Create your account' : 'Verify your email'}
        </p>

        {invitation && mode !== 'verify' && (
          <div className="notice notice-ok">
            You are invited as <strong>{ROLE_NAME[invitation.role] ?? invitation.role}</strong> at{' '}
            <strong>{invitation.org_name}</strong>. Create an account or sign in with {invitation.email}.
          </div>
        )}
        {info && <div className="notice notice-ok">{info}</div>}

        <form className="auth-form" onSubmit={submit}>
          {mode === 'sign-up' && (
            <label>
              Full name
              <input value={name} onChange={(e) => setName(e.target.value)} required autoComplete="name" />
            </label>
          )}
          {mode !== 'verify' && (
            <>
              <label>
                Email
                <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="email" />
              </label>
              <label>
                Password
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  minLength={8}
                  autoComplete={mode === 'sign-in' ? 'current-password' : 'new-password'}
                />
              </label>
            </>
          )}
          {mode === 'verify' && (
            <label>
              6-digit code
              <input
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                inputMode="numeric"
                autoComplete="one-time-code"
                pattern="\d{6}"
                required
              />
            </label>
          )}
          {error && (
            <p className="review-note review-error" role="alert">
              {error}
            </p>
          )}
          <button className="primary" type="submit" disabled={busy}>
            {busy ? 'Please wait…' : mode === 'sign-in' ? 'Sign in' : mode === 'sign-up' ? 'Create account' : 'Verify'}
          </button>
        </form>

        {mode === 'verify' ? (
          <button className="link-button" type="button" onClick={() => void sendCode()}>
            Send a new code
          </button>
        ) : (
          <button className="link-button" type="button" onClick={switchMode}>
            {mode === 'sign-in' ? 'New here? Create an account' : 'Already have an account? Sign in'}
          </button>
        )}
      </div>
    </div>
  )
}
