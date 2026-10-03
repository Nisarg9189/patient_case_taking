import { useEffect, useState } from 'react'
import { CaseDetails, ChecklistCard, SECTIONS, sectionTexts } from './App'
import { CaseDocuments } from './Documents'
import { PageHeader } from './PageHeader'
import { api, type Me } from './auth'
import { PrescriptionsCard } from './Prescriptions'
import type { CaseRecord, Checklist } from './types'

interface CaseRow {
  case_id: string
  finished_at: string | null
  aborted: boolean | null
  complaint: string | null
  reviewed: boolean
  has_summary: boolean
  patient_name: string | null
  patient_email: string | null
}

export interface CaseFull extends CaseRow {
  original_case: CaseRecord
  checklist: Checklist | null
  conversation: { question: string | null; answer: string }[] | null
  review_sections: Record<string, string> | null
  reviewed_at: string | null
  summary: string | null
  summary_sections: SummarySections | null
}

function when(iso: string | null) {
  return iso ? new Date(iso).toLocaleString() : '—'
}

// Doctors and nurses: the finished interviews of their clinic(s).
export function CasesView({ me }: { me: Me }) {
  const [cases, setCases] = useState<CaseRow[] | null>(null)
  const [selected, setSelected] = useState<CaseFull | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api<{ own: CaseRow[]; clinic: CaseRow[] }>('/api/cases')
      .then((result) => setCases(result.clinic))
      .catch((e) => setError(e.message))
  }, [])

  const open = async (caseId: string) => {
    setError(null)
    try {
      setSelected(await api<CaseFull>(`/api/cases/${caseId}`))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not open the case.')
    }
  }

  if (selected) return <CasePage selected={selected} onBack={() => setSelected(null)} backLabel="← All cases" meId={me.id} />

  return (
    <div className="page">
      <PageHeader tabs={[['cases', 'Clinic cases']]} active="cases" />
      <p className="page-intro">Finished intake interviews, newest first</p>
      {error && <div className="notice">{error}</div>}
      <div className="card">
        {cases === null ? (
          <p className="muted">Loading…</p>
        ) : cases.length === 0 ? (
          <p className="muted">No finished interviews yet.</p>
        ) : (
          <table className="cases-table">
            <thead>
              <tr>
                <th>Finished</th>
                <th>Patient</th>
                <th>Main complaint</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {cases.map((c) => (
                <tr key={c.case_id} onClick={() => void open(c.case_id)} tabIndex={0}
                    onKeyDown={(e) => e.key === 'Enter' && void open(c.case_id)}>
                  <td>{when(c.finished_at)}</td>
                  <td>{c.patient_name || c.patient_email || '—'}</td>
                  <td>{c.complaint || '—'}</td>
                  <td>
                    {c.has_summary && <span className="tag">summary</span>}
                    {c.reviewed && <span className="tag tag-reviewed">reviewed</span>}
                    {c.aborted && <span className="tag">stopped early</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

// One case in full (doctors and nurses): summary, the patient's corrections, the conversation.
function initials(name: string) {
  const words = name.replace(/[^\p{L}\s]/gu, ' ').split(/\s+/).filter(Boolean)
  return ((words[0]?.[0] ?? '') + (words[1]?.[0] ?? '')).toUpperCase()
}

// One case in full (doctors and nurses): who, the clinician summary, what the patient
// corrected, the record, and the conversation.
export function CasePage({
  selected,
  onBack,
  backLabel,
  meId,
}: {
  selected: CaseFull
  onBack: () => void
  backLabel: string
  meId: string
}) {
  const name = selected.patient_name || selected.patient_email || 'Patient'
  return (
    <div className="page">
      <PageHeader tabs={[['case', 'Patient case']]} active="case"
                  right={<button className="secondary" onClick={onBack}>{backLabel}</button>} />

      <section className="card patient-header">
        <span className="patient-avatar" aria-hidden="true">{initials(name)}</span>
        <div className="patient-header-text">
          <h1>{name}</h1>
          <p className="muted">
            {selected.patient_name && selected.patient_email && <>{selected.patient_email} · </>}
            Interview finished {when(selected.finished_at)}
          </p>
        </div>
        <div className="patient-header-tags">
          {selected.summary ? <span className="chip-tag chip-good">Summary ready</span> : <span className="chip-tag">Not summarised yet</span>}
          {selected.reviewed_at ? <span className="chip-tag chip-good">Checked by patient</span> : <span className="chip-tag">Not checked by patient</span>}
          {selected.aborted && <span className="chip-tag chip-warn">Stopped early</span>}
        </div>
      </section>

      <main className="layout">
        <section className="conversation">
          <ClinicianSummary text={selected.summary} sections={selected.summary_sections} />
          <CaseDocuments caseId={selected.case_id} />
          <PrescriptionsCard caseId={selected.case_id} record={selected.original_case} meId={meId} />
          {selected.review_sections && (
            <PatientReview record={selected.original_case} review={selected.review_sections} reviewedAt={selected.reviewed_at} />
          )}
          {selected.conversation && (
            <details className="card conversation-card">
              <summary>
                <h3>Conversation</h3>
                <span className="muted small">{selected.conversation.length} answers</span>
              </summary>
              <ol className="turns">
                {selected.conversation.map((turn, index) => (
                  <li key={index}>
                    <p className="turn-question">{turn.question ?? '(opening)'}</p>
                    <p className="turn-answer">{turn.answer}</p>
                  </li>
                ))}
              </ol>
            </details>
          )}
        </section>
        <aside className="summary">
          <div className="card">
            <h3>Case as recorded</h3>
            <CaseDetails record={selected.original_case} />
          </div>
          {selected.checklist && <ChecklistCard checklist={selected.checklist} />}
        </aside>
      </main>
    </div>
  )
}

// the summary worker's structured output (summary_agent.py, summary_output); stored in cases.summary_sections
interface SummarySections {
  flags: string[]
  presenting_complaint: string
  history_of_presenting_complaint: string | null
  allergies: string
  current_medicines: string
  past_medical_history: string | null
  family_history: string | null
  social_history: string | null
  travel_contacts: string | null
  vaccinations: string | null
  vitals_reported_by_patient: string | null
  pertinent_negatives: string | null
}

const SECTION_HEADINGS: [keyof SummarySections, string][] = [
  ['presenting_complaint', 'Presenting complaint'],
  ['history_of_presenting_complaint', 'History of presenting complaint'],
  ['allergies', 'Allergies'],
  ['current_medicines', 'Current medicines'],
  ['past_medical_history', 'Past medical history'],
  ['family_history', 'Family history'],
  ['social_history', 'Social history'],
  ['travel_contacts', 'Travel / contacts'],
  ['vaccinations', 'Vaccinations'],
  ['vitals_reported_by_patient', 'Vitals reported by patient'],
  ['pertinent_negatives', 'Pertinent negatives'],
]

// the structured summary in the same shape as parsed text: each flag its own warning
function fromStructured(s: SummarySections) {
  const flags = (s.flags ?? []).filter((f) => f && f.trim()).map((f) => ({ label: 'Flags', text: f }))
  const rest = SECTION_HEADINGS.filter(([key]) => typeof s[key] === 'string' && (s[key] as string).trim())
    .map(([key, label]) => ({ label, text: (s[key] as string).trim() }))
  return [...flags, ...rest]
}

// "Label: text" lines of the summary worker's text; lines without a label continue the one above
function summarySections(text: string) {
  const sections: { label: string | null; text: string }[] = []
  for (const raw of text.split('\n')) {
    const line = raw.trim()
    if (!line) continue
    const labelled = line.match(/^(?:[-•*]\s*)?\**([A-Z][A-Za-z0-9 /&'()-]{1,48})\**:\s*(.*)$/)
    if (labelled) sections.push({ label: labelled[1].trim(), text: labelled[2] })
    else if (sections.length) sections[sections.length - 1].text += `\n${line.replace(/^[-•*]\s*/, '')}`
    else sections.push({ label: null, text: line })
  }
  return sections
}

const FLAG_LABEL = /flag|urgent|alert|warning/i
const LEAD_LABEL = /^(presenting|chief|main) complaint/i
const NOTHING_FOUND = /^(none|nil|no |not |denies|n\/a)/i

// The clinician summary as labelled sections: flags first as a warning, then the complaint,
// then the rest; answers that found nothing are shown quieter.
function ClinicianSummary({ text, sections: structured }: { text: string | null; sections: SummarySections | null }) {
  if (!text && !structured) {
    return (
      <div className="card">
        <h3>Summary for the clinician</h3>
        <p className="muted">Not summarised yet. It appears here a few seconds after the interview ends.</p>
      </div>
    )
  }
  const sections = structured ? fromStructured(structured) : summarySections(text ?? '')
  const flags = sections.filter((s) => s.label && FLAG_LABEL.test(s.label) && !NOTHING_FOUND.test(s.text))
  const lead = sections.find((s) => s.label && LEAD_LABEL.test(s.label))
  const rest = sections.filter((s) => !flags.includes(s) && s !== lead && !(s.label && FLAG_LABEL.test(s.label)))

  return (
    <div className="card clinician-summary">
      <h3>Summary for the clinician</h3>
      {flags.map((f, i) => (
        <div key={i} className="summary-flag" role="note">
          <span className="summary-flag-icon" aria-hidden="true">!</span>
          <div>
            <strong>Needs attention</strong>
            <p className="pre-line">{f.text}</p>
          </div>
        </div>
      ))}
      {lead && (
        <div className="summary-lead">
          <span className="summary-label">{lead.label}</span>
          <p className="pre-line">{lead.text}</p>
        </div>
      )}
      <dl className="summary-list">
        {rest.map((s, i) => (
          <div key={i} className={NOTHING_FOUND.test(s.text) ? 'summary-item summary-item-quiet' : 'summary-item'}>
            {s.label && <dt>{s.label}</dt>}
            <dd className="pre-line">{s.text}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

const same = (a: string | undefined, b: string | undefined) =>
  (a ?? '').replace(/\s+/g, ' ').trim().toLowerCase() === (b ?? '').replace(/\s+/g, ' ').trim().toLowerCase()

// What the patient changed when they checked the record at the end of the interview.
function PatientReview({ record, review, reviewedAt }: { record: CaseRecord; review: Record<string, string>; reviewedAt: string | null }) {
  const recorded = sectionTexts(record)
  const changed = SECTIONS.filter((label) => label in review && !same(review[label], recorded[label]))
  return (
    <div className="card">
      <h3>
        Patient's check <span className="tag tag-reviewed">{when(reviewedAt)}</span>
      </h3>
      {changed.length === 0 ? (
        <p className="review-ok">✓ The patient confirmed the record without changes.</p>
      ) : (
        <>
          <p className="muted small">
            The patient changed {changed.length} section{changed.length === 1 ? '' : 's'}. Everything else was confirmed as recorded.
          </p>
          {changed.map((label) => (
            <div key={label} className="review-change">
              <span className="summary-label">{label}</span>
              <div className="review-change-grid">
                <span className="review-kind">Recorded</span>
                <p className={recorded[label] ? 'pre-line review-old' : 'pre-line muted'}>{recorded[label] || 'Nothing recorded'}</p>
                <span className="review-kind review-kind-new">Patient says</span>
                <p className={review[label] ? 'pre-line review-new' : 'pre-line muted'}>{review[label] || 'Removed by the patient'}</p>
              </div>
            </div>
          ))}
        </>
      )}
    </div>
  )
}
