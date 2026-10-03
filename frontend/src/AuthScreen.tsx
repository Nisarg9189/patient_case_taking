import { useState } from 'react'
import './Auth.css'
import { AuthArt } from './AuthArt'
import { authClient, refreshSession, type Invitation } from './auth'
import { BRAND } from './brand'
import {
  EyeIcon, EyeOffIcon, FileTextIcon, HospitalIcon, LockIcon, LogoIcon, MailIcon, MicIcon, ShieldIcon, UserIcon,
} from './icons'

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
  startMode,
  onBack,
}: {
  onSignedIn: () => void
  invitation?: Invitation | null
  verifyEmail?: string
  startMode?: 'sign-in' | 'sign-up'
  onBack?: () => void // back to the landing page
}) {
  const [mode, setMode] = useState<Mode>(verifyEmail ? 'verify' : invitation ? 'sign-up' : startMode ?? 'sign-in')
  const [name, setName] = useState('')
  const [email, setEmail] = useState(verifyEmail ?? invitation?.email ?? '')
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [showPassword, setShowPassword] = useState(false)
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

  const heading = mode === 'sign-in' ? 'Welcome back' : mode === 'sign-up' ? 'Create your account' : 'Verify your email'
  const sub = mode === 'sign-in' ? 'Sign in to continue your care.' : mode === 'sign-up'
    ? 'It takes a minute. No forms to fill in later: you will just talk.' : 'Enter the 6-digit code we emailed you.'

  return (
    <div className="au-shell">
      <aside className="au-side">
        <AuthArt className="au-art" />
        <div className="au-side-inner">
          <span className="au-logo"><LogoIcon size={30} /> {BRAND}</span>
          <div className="au-pitch">
            <h2>{mode === 'sign-up' ? 'Your story, understood before you walk in.' : 'Good to see you again.'}</h2>
            <p>Talk to our AI assistant in your own language, share your reports with the doctors you choose, and book your visit.</p>
            <ul className="au-points">
              <li><span><MicIcon size={20} /></span> Speak in English, Hindi, Gujarati or Marathi</li>
              <li><span><FileTextIcon size={20} /></span> Your reports and prescriptions, by date</li>
              <li><span><HospitalIcon size={20} /></span> Find a hospital and doctor, then book</li>
            </ul>
          </div>
          <p className="au-trust"><ShieldIcon size={16} /> Your information is shared only with the doctors you choose.</p>
        </div>
      </aside>

      <main className="au-main">
        <div className="au-panel">
          {onBack && (
            <button className="au-back" type="button" onClick={onBack}>
              ← Back to home
            </button>
          )}

          {mode !== 'verify' && (
            <div className="au-tabs" role="tablist" aria-label="Sign in or create an account">
              <button role="tab" type="button" aria-selected={mode === 'sign-in'} onClick={() => mode !== 'sign-in' && switchMode()}>Sign in</button>
              <button role="tab" type="button" aria-selected={mode === 'sign-up'} onClick={() => mode !== 'sign-up' && switchMode()}>Create account</button>
            </div>
          )}

          <h1>{heading}</h1>
          <p className="au-sub">{sub}</p>

          {invitation && mode !== 'verify' && (
            <div className="notice notice-ok">
              You are invited as <strong>{ROLE_NAME[invitation.role] ?? invitation.role}</strong> at{' '}
              <strong>{invitation.org_name}</strong>. Create an account or sign in with {invitation.email}.
            </div>
          )}
          {info && <div className="notice notice-ok">{info}</div>}

          <form className="au-form" onSubmit={submit}>
            {mode === 'sign-up' && (
              <label>
                Full name
                <span className="au-field">
                  <UserIcon size={18} />
                  <input value={name} onChange={(e) => setName(e.target.value)} required autoComplete="name" placeholder="Your name" />
                </span>
              </label>
            )}
            {mode !== 'verify' && (
              <>
                <label>
                  Email
                  <span className="au-field">
                    <MailIcon size={18} />
                    <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="email"
                           placeholder="you@example.com" />
                  </span>
                </label>
                <label>
                  Password
                  <span className="au-field">
                    <LockIcon size={18} />
                    <input
                      type={showPassword ? 'text' : 'password'}
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      required
                      minLength={8}
                      autoComplete={mode === 'sign-in' ? 'current-password' : 'new-password'}
                      placeholder={mode === 'sign-up' ? 'At least 8 characters' : 'Your password'}
                    />
                    <button type="button" className="au-eye" onClick={() => setShowPassword((v) => !v)}
                            aria-label={showPassword ? 'Hide password' : 'Show password'}>
                      {showPassword ? <EyeOffIcon size={18} /> : <EyeIcon size={18} />}
                    </button>
                  </span>
                </label>
              </>
            )}
            {mode === 'verify' && (
              <label>
                6-digit code
                <span className="au-field">
                  <ShieldIcon size={18} />
                  <input
                    className="au-code"
                    value={code}
                    onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    pattern="\d{6}"
                    placeholder="123456"
                    required
                  />
                </span>
              </label>
            )}
            {error && (
              <p className="review-note review-error" role="alert">
                {error}
              </p>
            )}
            <button className="au-submit" type="submit" disabled={busy}>
              {busy ? 'Please wait…' : mode === 'sign-in' ? 'Sign in' : mode === 'sign-up' ? 'Create account' : 'Verify email'}
            </button>
          </form>

          {mode === 'verify' ? (
            <button className="au-link" type="button" onClick={() => void sendCode()}>
              Send a new code
            </button>
          ) : (
            <p className="au-switch">
              {mode === 'sign-in' ? 'New here?' : 'Already have an account?'}{' '}
              <button className="au-link" type="button" onClick={switchMode}>
                {mode === 'sign-in' ? 'Create an account' : 'Sign in'}
              </button>
            </p>
          )}
        </div>
      </main>
    </div>
  )
}
