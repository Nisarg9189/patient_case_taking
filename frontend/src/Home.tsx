import { useEffect, useState } from 'react'
import { MyAppointments } from './BookVisit'
import { ConsultationFlow, Pipeline } from './Consultation'
import { MyDocuments } from './Documents'
import { LogoutIcon } from './icons'
import { MyDetails, MyPrescriptions } from './Prescriptions'
import type { Me } from './auth'

// The patient's home: a welcome, the profile icon, the pipeline from choosing a hospital to
// booking, and the patient's own visits, reports, prescriptions and details.

function initials(me: Me) {
  const words = (me.name || me.email).split(/[\s@.]+/).filter(Boolean)
  return ((words[0]?.[0] ?? '') + (words[1]?.[0] ?? '')).toUpperCase()
}

function greeting() {
  const hour = new Date().getHours()
  return hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening'
}

// the round profile icon at the top right, and what opens from it
function ProfileMenu({ me, onSignOut, onGo }: { me: Me; onSignOut: () => void; onGo: (id: string) => void }) {
  const [open, setOpen] = useState(false)

  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && setOpen(false)
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  const goTo = (id: string) => {
    setOpen(false)
    onGo(id)
  }

  return (
    <div className="profile">
      <button className="profile-button" onClick={() => setOpen((o) => !o)} aria-label="Your profile" aria-expanded={open}>
        {initials(me)}
      </button>
      {open && (
        <>
          <div className="profile-backdrop" onClick={() => setOpen(false)} />
          <div className="profile-menu" role="menu">
            <div className="profile-who">
              <span className="profile-button profile-button-big" aria-hidden="true">{initials(me)}</span>
              <div>
                <strong>{me.name || me.email}</strong>
                {me.name && <span className="muted small">{me.email}</span>}
              </div>
            </div>
            <button role="menuitem" onClick={() => goTo('home-details')}>Your details</button>
            <button role="menuitem" onClick={() => goTo('home-visits')}>Your visits</button>
            <button role="menuitem" onClick={() => goTo('home-reports')}>Your reports</button>
            <button role="menuitem" onClick={() => goTo('home-prescriptions')}>Your prescriptions</button>
            <button role="menuitem" className="profile-signout" onClick={onSignOut}>
              <LogoutIcon size={18} /> Sign out
            </button>
          </div>
        </>
      )}
    </div>
  )
}

export function PatientHome({ me, onSignOut }: { me: Me; onSignOut: () => void }) {
  const [consulting, setConsulting] = useState(false)
  const first = (me.name || me.email.split('@')[0]).split(/\s+/)[0]

  // a profile menu link: back to the home page (leaving a consultation), then down to that card
  const go = (id: string) => {
    setConsulting(false)
    setTimeout(() => document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 60)
  }

  return (
    <div className="page home">
      <header className="home-top">
        <div>
          <p className="home-hello">{greeting()},</p>
          <h1>{first}</h1>
        </div>
        <ProfileMenu me={me} onSignOut={onSignOut} onGo={go} />
      </header>

      {consulting ? (
        <ConsultationFlow onExit={() => setConsulting(false)} />
      ) : (
        <>
          <section className="card home-hero">
            <div className="home-hero-text">
              <span className="eyebrow">AI-assisted consultation</span>
              <h2>See the right doctor, already understood.</h2>
              <p>
                Choose a hospital and a doctor, tell our assistant what is wrong, and book a time.
                Your doctor reads your story before you walk in.
              </p>
              <button className="primary big" onClick={() => setConsulting(true)}>Start a consultation</button>
            </div>
            <Pipeline current={-1} />
          </section>

          <section className="home-grid">
            <div id="home-visits"><MyAppointments /></div>
            <div id="home-reports"><MyDocuments /></div>
            <div id="home-prescriptions"><MyPrescriptions /></div>
            <div id="home-details"><MyDetails /></div>
          </section>
        </>
      )}
    </div>
  )
}
