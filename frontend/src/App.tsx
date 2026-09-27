import './App.css'
import type { CaseRecord, Checklist, Symptom, TopicStatus } from './types'
import { useInterview, type Phase } from './useInterview'

const TOPICS: [string, string][] = [
  ['chief_complaint', 'Main complaint'],
  ['onset_or_progression', 'Onset / progression'],
  ['severity_rating', 'Severity'],
  ['associated_symptoms', 'Other symptoms'],
  ['current_medications', 'Current medicines'],
  ['allergies', 'Allergies'],
  ['medical_history', 'Medical history'],
  ['vital_signs', 'Vital signs'],
  ['oxygen_saturation', 'Oxygen level'],
  ['smoking_or_alcohol', 'Smoking / alcohol'],
  ['recent_travel_or_sick_contacts', 'Travel / sick contacts'],
  ['family_history', 'Family history'],
  ['vaccination_status', 'Vaccinations'],
]
const TOPIC_LABEL = Object.fromEntries(TOPICS)

const STATUS_LABEL: Record<TopicStatus, string> = {
  covered: 'Covered',
  pending: 'To ask',
  unknown_or_declined: 'Unknown',
  not_relevant: 'Not relevant',
}

const PHASE_LABEL: Record<Phase, string> = {
  idle: 'Ready',
  connecting: 'Connecting…',
  speaking: 'Asking',
  listening: 'Listening',
  thinking: 'Next question…',
  reconnecting: 'Reconnecting…',
  done: 'Finished',
  error: 'Stopped',
}

export default function App() {
  const interview = useInterview()
  const { phase } = interview
  const active = ['connecting', 'speaking', 'listening', 'thinking', 'reconnecting'].includes(phase)

  return (
    <div className="page">
      <header className="header">
        <div>
          <h1>Patient intake</h1>
          <p className="subtitle">A short spoken interview before your appointment</p>
        </div>
        <span className={`phase phase-${phase}`}>{PHASE_LABEL[phase]}</span>
      </header>

      {interview.notice && <div className="notice">{interview.notice}</div>}
      {interview.connection && active && (
        <div className={`connection connection-${interview.connection.state}`} role="status">
          <span className="spinner" aria-hidden="true" />
          {interview.connection.text}
        </div>
      )}

      <main className="layout">
        <section className="conversation">
          {phase === 'idle' || phase === 'error' ? (
            <div className="card intro">
              <h2>{phase === 'error' ? 'The interview stopped' : 'Before we start'}</h2>
              <p>
                You will be asked a few questions out loud. Answer in your own words; the next question
                starts shortly after you finish speaking.
              </p>
              <p className="muted">Your browser will ask for microphone access.</p>
              <button className="primary" onClick={interview.start}>
                {phase === 'error' ? 'Start again' : 'Start interview'}
              </button>
            </div>
          ) : phase === 'done' ? (
            <div className="card intro">
              <h2>Thank you</h2>
              <p>{interview.ending ?? 'The interview is complete. Your answers are summarised under "Case so far".'}</p>
              <button className="primary" onClick={interview.start}>
                Start a new interview
              </button>
            </div>
          ) : (
            <div className={`card current current-${phase}`}>
              <div className="current-label">
                {phase === 'connecting' ? 'Connecting…' : phase === 'listening' ? 'Your answer' : 'Question'}
              </div>
              <p className="question">{interview.question || 'Preparing the first question…'}</p>
              {phase === 'listening' || phase === 'thinking' ? (
                <div className="answer-box">
                  {phase === 'listening' && <LevelMeter level={interview.level} />}
                  <p className={interview.transcript ? 'transcript' : 'transcript placeholder'}>
                    {interview.transcript || 'Speak now…'}
                  </p>
                </div>
              ) : null}
              {active && (
                <button className="secondary" onClick={interview.stop}>
                  End interview
                </button>
              )}
            </div>
          )}

          {interview.turns.length > 0 && (
            <div className="card">
              <h3>Conversation</h3>
              <ol className="turns">
                {interview.turns.map((turn, index) => (
                  <li key={index}>
                    <p className="turn-question">{turn.question}</p>
                    <p className="turn-answer">{turn.answer}</p>
                  </li>
                ))}
              </ol>
            </div>
          )}
        </section>

        <aside className="summary">
          <CaseSummary record={interview.caseRecord} />
          <ChecklistCard checklist={interview.checklist} />
        </aside>
      </main>
    </div>
  )
}

function LevelMeter({ level }: { level: number }) {
  return (
    <div className="meter" aria-label="Microphone level">
      <div className="meter-fill" style={{ width: `${Math.round(level * 100)}%` }} />
    </div>
  )
}

function symptomDetails(symptom: Symptom) {
  return [
    symptom.character,
    symptom.severity,
    symptom.duration && `for ${symptom.duration}`,
    symptom.onset && `since ${symptom.onset}`,
    symptom.location,
    symptom.frequency,
    ...symptom.triggers.map((t) => `when ${t}`),
  ]
    .filter(Boolean)
    .join(' · ')
}

function CaseSummary({ record }: { record: CaseRecord | null }) {
  if (!record) {
    return (
      <div className="card">
        <h3>Case so far</h3>
        <p className="muted">Filled in as the patient answers.</p>
      </div>
    )
  }

  const medicines = [...record.regular_medications, ...record.recent_medications]
  const saidNo = record.negative_answers.map((n) => TOPIC_LABEL[n.item] ?? n.item)

  return (
    <div className="card">
      <h3>Case so far</h3>

      {record.chief_complaint && <Row label="Main complaint">{record.chief_complaint.text}</Row>}
      {record.overall_severity && <Row label="Overall severity">{record.overall_severity.text}</Row>}

      <Row label="Allergies">
        {record.allergies.status === 'reported' ? (
          <span className="alert">
            {record.allergies.items.map((a) => (a.reaction ? `${a.substance} (${a.reaction})` : a.substance)).join(', ')}
          </span>
        ) : record.allergies.status === 'none_reported' ? (
          'None reported'
        ) : record.asked_without_answer.includes('allergies') ? (
          <span className="warn">Asked, no answer</span>
        ) : (
          <span className="muted">Not asked yet</span>
        )}
      </Row>

      {record.symptoms.length > 0 && (
        <Row label="Symptoms">
          <ul className="plain">
            {record.symptoms.map((s) => (
              <li key={s.name}>
                <strong className={s.status === 'absent' ? 'struck' : undefined}>{s.name}</strong>
                {s.status !== 'present' && <span className="tag">{s.status}</span>}
                {record.important_reported_symptoms.some((f) => f.symptom === s.name) && (
                  <span className="tag tag-flag">flag</span>
                )}
                {symptomDetails(s) && <div className="muted small">{symptomDetails(s)}</div>}
              </li>
            ))}
          </ul>
        </Row>
      )}

      {medicines.length > 0 && (
        <Row label="Medicines">
          {medicines.map((m) => [m.name, m.dose, m.frequency].filter(Boolean).join(' ')).join('; ')}
        </Row>
      )}
      {record.vital_signs.length > 0 && (
        <Row label="Vital signs">
          {record.vital_signs
            .map((v) => `${v.name} ${v.approximate ? '~' : ''}${v.value}${v.measured_when ? ` (${v.measured_when})` : ''}`)
            .join('; ')}
        </Row>
      )}
      {record.medical_history.length > 0 && (
        <Row label="Medical history">
          {record.medical_history.map((c) => (c.duration ? `${c.condition} (${c.duration})` : c.condition)).join(', ')}
        </Row>
      )}
      {record.family_history.length > 0 && (
        <Row label="Family history">
          {record.family_history.map((f) => `${f.relative}: ${f.condition}`).join('; ')}
        </Row>
      )}
      {record.social_history.length > 0 && (
        <Row label="Lifestyle">{record.social_history.map((s) => `${s.topic}: ${s.detail}`).join('; ')}</Row>
      )}
      {record.travel_and_contacts.length > 0 && (
        <Row label="Travel / contacts">{record.travel_and_contacts.map((t) => t.detail).join('; ')}</Row>
      )}
      {record.vaccinations.length > 0 && (
        <Row label="Vaccinations">
          {record.vaccinations.map(vaccinationText).join('; ')}
        </Row>
      )}
      {saidNo.length > 0 && <Row label="Said no to">{saidNo.join(', ')}</Row>}
    </div>
  )
}

// what the extractor writes when the patient did not name a vaccine ("Yes, they are up to date")
const UNNAMED_VACCINES = new Set(['unspecified', 'unknown', 'not specified', 'not named', 'general'])

function capitalize(text: string) {
  return text ? text[0].toUpperCase() + text.slice(1) : text
}

function vaccinationText(v: { vaccine: string; detail: string }) {
  return UNNAMED_VACCINES.has(v.vaccine.trim().toLowerCase())
    ? `${capitalize(v.detail)} (no specific vaccine named)`
    : `${v.vaccine}: ${v.detail}`
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="row">
      <div className="row-label">{label}</div>
      <div className="row-value">{children}</div>
    </div>
  )
}

function ChecklistCard({ checklist }: { checklist: Checklist }) {
  const covered = TOPICS.filter(([key]) => checklist[key] && checklist[key] !== 'pending').length
  return (
    <div className="card">
      <h3>
        Topics <span className="muted small">{covered}/{TOPICS.length}</span>
      </h3>
      <ul className="topics">
        {TOPICS.map(([key, label]) => {
          const status = checklist[key] ?? 'pending'
          return (
            <li key={key} className={`topic topic-${status}`}>
              <span>{label}</span>
              <span className="topic-status">{STATUS_LABEL[status]}</span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
