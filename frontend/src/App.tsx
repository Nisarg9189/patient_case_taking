import './App.css'
import type { CaseRecord, Checklist, Symptom, TopicStatus } from './types'

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

// the same details for reading: severity out of 10, no doubled "for"/"since", no repeats
// (symptomDetails stays as it is: the patient's saved review texts were made from it)
export function symptomLine(symptom: Symptom) {
  const prefixed = (prefix: string, text: string | null) =>
    text && (/^(for|since|from|about|around|over|after|on|in|last|this)\b/i.test(text) ? text : `${prefix} ${text}`)
  const severity = symptom.severity && (/^\d+(\.\d+)?$/.test(symptom.severity.trim()) ? `severity ${symptom.severity.trim()}/10` : symptom.severity)
  const parts = [
    symptom.character,
    severity,
    prefixed('for', symptom.duration),
    prefixed('since', symptom.onset),
    symptom.location,
    symptom.frequency,
    ...symptom.triggers.map((t) => `when ${t}`),
  ].filter((part): part is string => Boolean(part))
  const seen = new Set<string>()
  return parts.filter((part) => !seen.has(part.toLowerCase()) && seen.add(part.toLowerCase())).join(' · ')
}

// the summary sections, in the order shown; after the interview the patient can edit them
export const SECTIONS = [
  'Main complaint', 'Overall severity', 'Allergies', 'Symptoms', 'Medicines', 'Vital signs',
  'Medical history', 'Family history', 'Lifestyle', 'Travel / contacts', 'Vaccinations', 'Said no to',
]

function allergyText(record: CaseRecord) {
  if (record.allergies.status === 'reported') {
    return record.allergies.items.map((a) => (a.reaction ? `${a.substance} (${a.reaction})` : a.substance)).join(', ')
  }
  return record.allergies.status === 'none_reported' ? 'None reported' : ''
}

// each section as plain text (one item per line), the starting point for the patient's edits
export function sectionTexts(record: CaseRecord): Record<string, string> {
  const medicines = [...record.regular_medications, ...record.recent_medications]
  return {
    'Main complaint': record.chief_complaint?.text ?? '',
    'Overall severity': record.overall_severity?.text ?? '',
    Allergies: allergyText(record),
    Symptoms: record.symptoms
      .map((s) => [s.status === 'present' ? s.name : `${s.name} (${s.status})`, symptomDetails(s)].filter(Boolean).join(': '))
      .join('\n'),
    Medicines: medicines.map((m) => [m.name, m.dose, m.frequency].filter(Boolean).join(' ')).join('\n'),
    'Vital signs': record.vital_signs
      .map((v) => `${v.name} ${v.approximate ? '~' : ''}${v.value}${v.measured_when ? ` (${v.measured_when})` : ''}`)
      .join('\n'),
    'Medical history': record.medical_history.map((c) => (c.duration ? `${c.condition} (${c.duration})` : c.condition)).join('\n'),
    'Family history': record.family_history.map((f) => `${f.relative}: ${f.condition}`).join('\n'),
    Lifestyle: record.social_history.map((s) => `${s.topic}: ${s.detail}`).join('\n'),
    'Travel / contacts': record.travel_and_contacts.map((t) => t.detail).join('\n'),
    Vaccinations: record.vaccinations.map(vaccinationText).join('\n'),
    'Said no to': record.negative_answers.map((n) => TOPIC_LABEL[n.item] ?? n.item).join(', '),
  }
}

// the case as the interview recorded it
export function CaseDetails({ record }: { record: CaseRecord }) {
  const medicines = [...record.regular_medications, ...record.recent_medications]
  const saidNo = record.negative_answers.map((n) => TOPIC_LABEL[n.item] ?? n.item)

  return (
    <>
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
                {symptomLine(s) && <div className="muted small">{symptomLine(s)}</div>}
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
    </>
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

export function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="row">
      <div className="row-label">{label}</div>
      <div className="row-value">{children}</div>
    </div>
  )
}

export function ChecklistCard({ checklist }: { checklist: Checklist }) {
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
