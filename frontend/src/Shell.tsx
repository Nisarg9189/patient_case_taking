import { useCallback, useEffect, useState } from 'react'
import { InterviewView } from './App'
import { AuthScreen } from './AuthScreen'
import {
  api, ApiError, authClient, describeInvitation, forgetInvite, takeInviteFromUrl, type Invitation, type Me,
} from './auth'
import { AppointmentsView } from './AppointmentsView'
import { CasesView } from './CasesView'
import { MembersView } from './MembersView'
import { ScheduleView } from './ScheduleView'
import { CalendarIcon, ClipboardIcon, ClockIcon, LogoIcon, LogoutIcon, MicIcon, UsersIcon } from './icons'

type View = 'interview' | 'cases' | 'appointments' | 'schedule' | 'members'

const VIEW_LABEL: Record<View, string> = {
  interview: 'My interview',
  cases: 'Clinic cases',
  appointments: 'Appointments',
  schedule: 'Clinic schedule',
  members: 'Clinic members',
}

const VIEW_ICON: Record<View, () => React.ReactNode> = {
  interview: () => <MicIcon />,
  cases: () => <ClipboardIcon />,
  appointments: () => <CalendarIcon />,
  schedule: () => <ClockIcon />,
  members: () => <UsersIcon />,
}

function initials(me: Me) {
  const words = (me.name || me.email).split(/[\s@.]+/).filter(Boolean)
  return ((words[0]?.[0] ?? '') + (words[1]?.[0] ?? '')).toUpperCase()
}

// the pages a user's roles give them, in this order
function viewsFor(me: Me): View[] {
  const roles = new Set(me.memberships.map((m) => m.role))
  const views: View[] = []
  const admin = roles.has('clinic_admin') || me.platform_admin
  if (roles.has('doctor') || roles.has('nurse')) views.push('cases')
  if (admin || roles.has('doctor') || roles.has('nurse') || roles.has('front_desk')) views.push('appointments')
  if (admin) views.push('schedule', 'members')
  if (roles.has('patient')) views.push('interview')
  return views
}

// The app's root: sign-in, then the pages the user's roles allow.
export default function Shell() {
  const [status, setStatus] = useState<'loading' | 'signed-out' | 'verify' | 'signed-in'>('loading')
  const [me, setMe] = useState<Me | null>(null)
  const [view, setView] = useState<View | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [verifyEmail, setVerifyEmail] = useState<string | undefined>()
  const [inviteToken] = useState(() => takeInviteFromUrl())
  const [invitation, setInvitation] = useState<Invitation | null>(null)
  const [menuOpen, setMenuOpen] = useState(false)

  // an invitation link was opened: show what it is for on the sign-in screen
  useEffect(() => {
    if (!inviteToken) return
    describeInvitation(inviteToken)
      .then(setInvitation)
      .catch((e) => {
        forgetInvite()
        setError(e instanceof Error ? e.message : 'This invitation is not valid.')
      })
  }, [inviteToken])

  const showProfile = (profile: Me) => {
    setMe(profile)
    setView(viewsFor(profile)[0] ?? null)
    setStatus('signed-in')
  }

  const load = useCallback(async () => {
    setError(null)
    const { data } = await authClient.getSession()
    if (!data?.session) {
      setStatus('signed-out')
      return
    }
    let profile: Me
    try {
      profile = await api<Me>('/api/me')
    } catch (e) {
      if (e instanceof ApiError && e.code === 'email_not_verified') {
        setVerifyEmail(data.user.email)
        setStatus('verify')
        return
      }
      setError(e instanceof Error ? e.message : 'Could not load your account.')
      setStatus('signed-out')
      return
    }

    const pending = takeInviteFromUrl()
    if (pending) {
      try {
        const accepted = await api<Me>('/api/invitations/accept', { method: 'POST', body: JSON.stringify({ token: pending }) })
        const role = accepted.memberships.find((m) => !profile.memberships.some((p) => p.org_id === m.org_id && p.role === m.role))
        setNotice(role ? `Invitation accepted: you are now ${role.role.replace('_', ' ')} at ${role.org_name}.` : 'Invitation accepted.')
        profile = accepted
      } catch (e) {
        setNotice(e instanceof Error ? e.message : 'Could not accept the invitation.')
      }
      forgetInvite()
    }
    showProfile(profile)
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const signOut = async () => {
    await authClient.signOut()
    setMenuOpen(false)
    setMe(null)
    setView(null)
    setStatus('signed-out')
  }

  if (status === 'loading') return <p className="muted centered">Loading…</p>
  if (status === 'verify') {
    return <AuthScreen verifyEmail={verifyEmail} onSignedIn={() => void load()} />
  }
  if (status === 'signed-out' || !me) {
    return (
      <>
        {error && <div className="notice">{error}</div>}
        <AuthScreen invitation={invitation} onSignedIn={() => void load()} />
      </>
    )
  }

  const views = viewsFor(me)
  const roles = [...new Set(me.memberships.map((m) => m.role.replace('_', ' ')))].join(', ')
  return (
    <div className="app">
      <aside className="sidebar">
        <div className="sidebar-logo" title="Patient intake">
          <LogoIcon size={30} />
        </div>
        <nav className="sidebar-nav">
          {views.map((v) => (
            <button key={v} className={v === view ? 'side-item side-item-active' : 'side-item'}
                    onClick={() => setView(v)} title={VIEW_LABEL[v]} aria-label={VIEW_LABEL[v]}>
              {VIEW_ICON[v]()}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <button className="avatar" onClick={() => setMenuOpen((open) => !open)} aria-label="Account"
                  aria-expanded={menuOpen}>
            {initials(me)}
          </button>
          {menuOpen && (
            <div className="account-menu" role="menu">
              <strong>{me.name || me.email}</strong>
              <span className="muted small">{me.email}</span>
              {roles && <span className="muted small">{me.platform_admin ? `platform admin, ${roles}` : roles}</span>}
              <button className="account-signout" role="menuitem" onClick={() => void signOut()}>
                <LogoutIcon size={18} /> Sign out
              </button>
            </div>
          )}
        </div>
      </aside>

      <div className="main">
        <nav className="topstrip">
          {views.map((v) => (
            <button key={v} className={v === view ? 'top-chip top-chip-active' : 'top-chip'} onClick={() => setView(v)}>
              {VIEW_LABEL[v]}
            </button>
          ))}
        </nav>

        <div className="panel" onClick={() => menuOpen && setMenuOpen(false)}>
          {notice && (
            <div className="panel-notice">
              <div className="notice notice-ok">{notice}</div>
            </div>
          )}
          {view === 'interview' && <InterviewView />}
          {view === 'cases' && <CasesView me={me} />}
          {view === 'appointments' && <AppointmentsView me={me} />}
          {view === 'schedule' && <ScheduleView />}
          {view === 'members' && <MembersView me={me} />}
          {view === null && (
            <div className="page">
              <div className="card">
                <h2>No access yet</h2>
                <p className="muted">Your account has no role in a clinic yet. Ask your clinic admin to add you.</p>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
