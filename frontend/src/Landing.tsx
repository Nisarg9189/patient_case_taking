import type { ReactNode } from 'react'
import './Landing.css'
import { BRAND } from './brand'
import {
  ArrowRightIcon, CalendarIcon, ClipboardIcon, FileTextIcon, HospitalIcon, LogoIcon, MicIcon, PillIcon,
  SearchIcon, ShareIcon, ShieldIcon, UsersIcon,
} from './icons'
import { VoiceStatus } from './VoiceStatus'

// The public page a visitor sees before signing in: what the product does, in the app's own
// purple. "Get started" and "Sign in" lead to the sign-in screen (Shell.tsx).

// The sample interview shown in the page's mock phone: what the assistant and the patient say.
const SAMPLE = { ai: 'Hello, what brings you in today?', me: 'I have had a fever for two days.', ai2: 'I see. How bad is it, from 0 to 10?' }

const go = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })

function Card({ icon, title, children, small }: { icon: ReactNode; title: string; children: ReactNode; small?: boolean }) {
  return (
    <div className={small ? 'l-card l-card-feature' : 'l-card'}>
      <span className="l-icon">{icon}</span>
      <h3>{title}</h3>
      {children}
    </div>
  )
}

function Head({ eyebrow, title, children }: { eyebrow: string; title: string; children: ReactNode }) {
  return (
    <div className="l-head">
      <span className="l-eyebrow">{eyebrow}</span>
      <h2>{title}</h2>
      <p>{children}</p>
    </div>
  )
}

export function Landing({ onStart, onSignIn }: { onStart: () => void; onSignIn: () => void }) {
  return (
    <div className="landing">
      <nav className="l-nav" aria-label="Main">
        <a className="l-brand" href="#top" onClick={(e) => { e.preventDefault(); window.scrollTo({ top: 0, behavior: 'smooth' }) }}>
          <LogoIcon size={28} /> {BRAND}
        </a>
        <div className="l-links">
          <button onClick={() => go('why')}>Why us</button>
          <button onClick={() => go('platform')}>Platform</button>
          <button onClick={() => go('features')}>Features</button>
          <button onClick={() => go('how')}>How it works</button>
        </div>
        <div className="l-nav-actions">
          <button className="l-ghost" onClick={onSignIn}>Sign in</button>
          <button className="l-btn" onClick={onStart}>
            Get started <span className="l-btn-arrow"><ArrowRightIcon size={18} /></span>
          </button>
        </div>
      </nav>

      <main className="l-wrap">
        <header className="l-hero" id="top">
          <span className="l-badge"><i /> AI-assisted consultations</span>
          <h1>
            Your story, <span className="l-mark">understood</span> before you walk in.
          </h1>
          <p className="l-lead">
            Patients talk to an AI assistant. The hospital gets a clear summary, their past reports and a
            booked visit, within minutes.
          </p>
          <div className="l-cta">
            <button className="l-btn" onClick={onStart}>
              Get started <span className="l-btn-arrow"><ArrowRightIcon size={18} /></span>
            </button>
            <button className="l-btn-outline" onClick={() => go('how')}>See how it works</button>
          </div>
          <div className="l-stage">
            <div className="l-stage-grid">
              <div className="l-mock">
                <h3>Voice interview</h3>
                <VoiceStatus state="listening" />
                <div className="l-bubbles">
                  <div className="l-bubble l-bubble-ai">{SAMPLE.ai}</div>
                  <div className="l-bubble l-bubble-me">{SAMPLE.me}</div>
                  <div className="l-bubble l-bubble-ai">{SAMPLE.ai2}</div>
                </div>
              </div>
              <div className="l-mock">
                <h3>Summary for the clinician <span className="l-english">in English</span></h3>
                <div className="l-flag" role="note">
                  <ShieldIcon size={20} />
                  <div><strong>Needs attention</strong>Chest tightness on climbing stairs</div>
                </div>
                <dl className="l-rows">
                  <div><dt>Complaint</dt><dd>Fever for 2 days, 5/10</dd></div>
                  <div><dt>Allergies</dt><dd>Penicillin (rash)</dd></div>
                  <div><dt>Medicines</dt><dd>Metformin 500 mg, twice a day</dd></div>
                  <div><dt>Latest report</dt><dd>HbA1c <span className="l-high">6.8 % (H)</span></dd></div>
                </dl>
                <span className="l-sample">Illustrative example</span>
              </div>
            </div>
          </div>
        </header>

        <section className="l-section" id="why">
          <Head eyebrow="Why us" title={`Why ${BRAND}?`}>Because a good first conversation is what makes a visit work.</Head>
          <div className="l-grid l-grid-3">
            <Card icon={<MicIcon />} title="Speak, don’t type">
              <p>Patients answer out loud. No forms to fill in and nothing to type.</p>
            </Card>
            <Card icon={<ClipboardIcon />} title="Doctors start informed">
              <p>A clear summary before the visit: urgent symptoms first, and allergies and medicines are always asked.</p>
            </Card>
            <Card icon={<FileTextIcon />} title="Nothing gets lost">
              <p>Past lab reports and prescriptions are read, dated and shared with the right doctor, only if the patient chooses.</p>
            </Card>
          </div>
        </section>

        <section className="l-section" id="platform">
          <Head eyebrow="Platform" title="Explore the platform">One connected journey for patients and hospitals.</Head>
          <div className="l-grid l-grid-3">
            <Card icon={<MicIcon />} title="AI voice interview">
              <p>A natural conversation that the patient can interrupt at any time.</p>
              <ul>
                <li>Answers are saved as the patient speaks</li>
                <li>Urgent symptoms are flagged at once</li>
                <li>Ends with a read-back the patient confirms</li>
              </ul>
            </Card>
            <Card icon={<FileTextIcon />} title="Reports and history">
              <p>Photograph a lab report or an old prescription and it becomes a dated entry.</p>
              <ul>
                <li>Read by OCR and summarised for the doctor</li>
                <li>Prescriptions doctors write join the history</li>
                <li>The patient decides what each doctor sees</li>
              </ul>
            </Card>
            <Card icon={<HospitalIcon />} title="Hospitals and booking">
              <p>Find the right place and the right doctor, then book a time.</p>
              <ul>
                <li>Search by state and district</li>
                <li>Best rated first, with the consultation fee</li>
                <li>Choose the doctor your interview goes to</li>
              </ul>
            </Card>
          </div>
        </section>

        <section className="l-section" id="features">
          <Head eyebrow="Features" title="Everything the visit needs">From the first symptom to the signed prescription.</Head>
          <div className="l-grid l-grid-4">
            <Card small icon={<MicIcon size={20} />} title="Natural conversation">
              <p>Patients can interrupt the assistant at any time, like talking to a person.</p>
            </Card>
            <Card small icon={<ShieldIcon size={20} />} title="Red-flag alerts">
              <p>Chest pain, breathlessness and other urgent symptoms are flagged first.</p>
            </Card>
            <Card small icon={<PillIcon size={20} />} title="Medicine and allergy safety">
              <p>These are always asked before an interview can be saved.</p>
            </Card>
            <Card small icon={<FileTextIcon size={20} />} title="Report reading">
              <p>Lab reports and prescriptions turn into dated, summarised entries.</p>
            </Card>
            <Card small icon={<CalendarIcon size={20} />} title="Health timeline">
              <p>A date-wise history that the patient shares by choice, visit by visit.</p>
            </Card>
            <Card small icon={<SearchIcon size={20} />} title="Hospital finder">
              <p>Ratings and consultation fees at a glance, for every hospital in a district.</p>
            </Card>
            <Card small icon={<PillIcon size={20} />} title="Digital prescriptions">
              <p>Doctors write, sign and print prescriptions with diagnosis and medicines.</p>
            </Card>
            <Card small icon={<UsersIcon size={20} />} title="Teams and referrals">
              <p>Doctors, nurses, front desk and admins each see what they need, and visits can be referred on.</p>
            </Card>
          </div>
        </section>

        <section className="l-section" id="how">
          <Head eyebrow="Get started" title="How it works">Five steps from search to seat.</Head>
          <ol className="l-steps">
            {[
              ['Choose where', 'Pick your state and district.'],
              ['Pick a hospital and doctor', 'Best rated first, with the fee shown.'],
              ['Add past reports', 'Photograph reports and choose what to share.'],
              ['Talk to the assistant', 'A short voice conversation.'],
              ['Book a time', 'A slot with the doctor you chose.'],
            ].map(([title, text], i) => (
              <li key={title} className="l-step">
                <span className="l-step-n">{i + 1}</span>
                <h3>{title}</h3>
                <p>{text}</p>
              </li>
            ))}
          </ol>
        </section>

        <section className="l-band" aria-labelledby="l-final">
          <h2 id="l-final">From first symptom to booked visit. And everything in between.</h2>
          <p>Create your account in a minute. The assistant never diagnoses: every medical decision stays with your doctor.</p>
          <div className="l-cta">
            <button className="l-btn-light" onClick={onStart}>
              Get started <span className="l-btn-arrow"><ArrowRightIcon size={18} /></span>
            </button>
            <button className="l-btn-ghost-light" onClick={onSignIn}>Sign in</button>
          </div>
        </section>

        <footer className="l-footer">
          <div>
            <span className="l-brand"><LogoIcon size={24} /> {BRAND}</span>
            <p className="l-note"><ShareIcon size={14} /> Your reports are shared only with the doctors you choose.</p>
          </div>
          <div className="l-footer-links">
            <button onClick={() => go('why')}>Why us</button>
            <button onClick={() => go('platform')}>Platform</button>
            <button onClick={() => go('features')}>Features</button>
            <button onClick={() => go('how')}>How it works</button>
            <button onClick={onSignIn}>Sign in</button>
          </div>
          <span>© {new Date().getFullYear()} {BRAND}</span>
        </footer>
      </main>
    </div>
  )
}
