import { useCallback, useEffect, useRef, useState } from 'react'
import { sectionTexts, symptomLine } from './App'
import { api, ApiError } from './auth'
import { fromListed, MedicineSearch } from './MedicineSearch'
import {
  ageFrom, allergyClash, DURATION_LABEL, durationText, FORM_LABEL, FREQUENCY_LABEL, newMedicine, ROUTE_LABEL, suggestedQuantity,
  TIMING_LABEL, type DoctorProfile, type Medicine, type Prescription, type RxContent,
} from './rx'
import type { CaseRecord } from './types'

function when(iso: string | null) {
  return iso ? new Date(iso).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : ''
}

// "Twice a day (BD)" -> "twice a day (BD)": abbreviations stay in capitals
const lowerFirst = (text: string) => text.charAt(0).toLowerCase() + text.slice(1)

function day(isoDate: string | null) {
  return isoDate ? new Date(`${isoDate}T12:00:00`).toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' }) : ''
}

// what the interview already knows, as the starting point of a new prescription
function fromInterview(record: CaseRecord): RxContent {
  const texts = sectionTexts(record)
  return {
    // left empty: the server fills in the patient's saved details (date of birth, sex, ...)
    patient: { age: '', sex: '', weight_kg: '' },
    // the symptoms with their details, or else the main complaint
    complaints: record.symptoms.length
      ? record.symptoms
          .filter((s) => s.status !== 'absent')
          .map((s) => [s.name, symptomLine(s)].filter(Boolean).join(': '))
          .join('\n')
      : texts['Main complaint'],
    allergies: texts.Allergies,
    findings: texts['Vital signs'],
    diagnosis: '',
    diagnosis_type: 'provisional',
    medicines: [],
    investigations: [],
    advice: '',
    follow_up_date: null,
    follow_up_note: '',
  }
}

// ---- on a patient's case (doctors and nurses)

export function PrescriptionsCard({ caseId, record, meId }: { caseId: string; record: CaseRecord; meId: string }) {
  const [list, setList] = useState<Prescription[] | null>(null)
  const [canWrite, setCanWrite] = useState(false)
  const [profile, setProfile] = useState<DoctorProfile | null>(null)
  const [editing, setEditing] = useState<Prescription | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    const result = await api<{ prescriptions: Prescription[]; can_write: boolean }>(`/api/cases/${caseId}/prescriptions`)
    setList(result.prescriptions)
    setCanWrite(result.can_write)
    if (result.can_write) setProfile(await api<DoctorProfile>('/api/me/doctor-profile'))
  }, [caseId])

  useEffect(() => {
    load().catch((e) => setError(e.message))
  }, [load])

  const write = async () => {
    setError(null)
    setNotice(null)
    const draft = list?.find((p) => p.status === 'draft')
    if (draft) return setEditing(draft)
    try {
      setEditing(await api<Prescription>(`/api/cases/${caseId}/prescriptions`, { method: 'POST', body: JSON.stringify(fromInterview(record)) }))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not start a prescription.')
    }
  }

  const voidIt = async (rx: Prescription) => {
    const reason = window.prompt('Why is this prescription void? (for example: wrong dose). It stays in the record, marked void.')
    if (!reason || reason.trim().length < 3) return
    try {
      await api(`/api/prescriptions/${rx.prescription_id}/void`, { method: 'POST', body: JSON.stringify({ reason }) })
      setNotice('Prescription voided. You can write a new one.')
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not void it.')
    }
  }

  const draft = list?.find((p) => p.status === 'draft')
  const issued = (list ?? []).filter((p) => p.status !== 'draft')

  return (
    <div className="card rx-card">
      <div className="rx-card-head">
        <h3>Prescriptions</h3>
        {canWrite && (
          <button className="save-button" onClick={() => void write()}>
            {draft ? 'Continue draft' : '+ Write prescription'}
          </button>
        )}
      </div>
      {error && <p className="review-note review-error">{error}</p>}
      {notice && <p className="review-note">{notice}</p>}
      {list === null ? (
        <p className="muted">Loading…</p>
      ) : issued.length === 0 ? (
        <p className="muted">{draft ? 'A draft is in progress (only you can see it).' : 'No prescription yet.'}</p>
      ) : (
        issued.map((rx) => (
          <PrescriptionDocument key={rx.prescription_id} rx={rx}
                                onVoid={rx.status === 'signed' && rx.doctor_id === meId ? () => void voidIt(rx) : undefined} />
        ))
      )}
      {editing && profile && (
        <PrescriptionEditor
          rx={editing}
          profile={profile}
          onProfile={setProfile}
          onClose={(message) => {
            setEditing(null)
            if (message) setNotice(message)
            void load()
          }}
        />
      )}
    </div>
  )
}

// ---- the prescription as issued (also what gets printed)

export function PrescriptionDocument({ rx, onVoid }: { rx: Prescription; onVoid?: () => void }) {
  const sheet = useRef<HTMLElement>(null)
  const c = rx.content
  const who = rx.prescriber

  const print = () => {
    const element = sheet.current
    if (!element) return
    element.classList.add('print-me')
    document.body.classList.add('printing')
    const done = () => {
      element.classList.remove('print-me')
      document.body.classList.remove('printing')
      window.removeEventListener('afterprint', done)
    }
    window.addEventListener('afterprint', done)
    window.print()
  }

  return (
    <article ref={sheet} className={`rx-doc rx-${rx.status}`}>
      {rx.status === 'void' && <div className="rx-void-mark" aria-hidden="true">VOID</div>}
      <header className="rx-doc-head">
        <div>
          <strong className="rx-doctor">{who?.name ?? rx.doctor_name}</strong>
          {who && (
            <div className="rx-credentials">
              {who.qualification}
              {who.specialty && ` · ${who.specialty}`}
              <br />
              Reg. No. {who.registration_number} · {who.registration_council}
            </div>
          )}
        </div>
        <div className="rx-clinic">
          <strong>{who?.clinic_name ?? rx.clinic_name}</strong>
          {who?.clinic_address && <div className="pre-line">{who.clinic_address}</div>}
          {(who?.clinic_phone || who?.clinic_email) && (
            <div>{[who.clinic_phone && `Phone ${who.clinic_phone}`, who.clinic_email].filter(Boolean).join(' · ')}</div>
          )}
          {who?.clinic_registration && <div>Reg. {who.clinic_registration}</div>}
          <div className="rx-date">{rx.signed_at ? when(rx.signed_at) : `Draft · ${when(rx.updated_at)}`}</div>
        </div>
      </header>

      <div className="rx-patient">
        <span>
          <b>Patient:</b> {rx.patient_name || rx.patient_email || '—'}
        </span>
        {c.patient.age && <span><b>Age:</b> {c.patient.age}</span>}
        {c.patient.sex && <span><b>Sex:</b> {c.patient.sex}</span>}
        {c.patient.weight_kg && <span><b>Weight:</b> {c.patient.weight_kg} kg</span>}
        {c.patient.phone && <span><b>Phone:</b> {c.patient.phone}</span>}
        {c.patient.abha_number && <span><b>ABHA:</b> {c.patient.abha_number}</span>}
        {c.patient.address && <span className="rx-patient-address"><b>Address:</b> {c.patient.address}</span>}
      </div>

      {c.allergies && !/^none/i.test(c.allergies) && (
        <p className="rx-allergy"><b>Allergies:</b> {c.allergies}</p>
      )}
      <dl className="rx-facts">
        {c.complaints && <><dt>Complaints</dt><dd className="pre-line">{c.complaints}</dd></>}
        {c.findings && <><dt>Findings</dt><dd className="pre-line">{c.findings}</dd></>}
        {c.diagnosis && (
          <><dt>Diagnosis</dt><dd>{c.diagnosis} <span className="muted">({c.diagnosis_type})</span></dd></>
        )}
      </dl>

      {c.medicines.length > 0 && (
        <>
          <div className="rx-symbol" aria-hidden="true">℞</div>
          <ol className="rx-medicines">
            {c.medicines.map((m, i) => (
              <li key={i}>
                <div className="rx-med-name">
                  <strong>{m.generic_name.toUpperCase()}</strong> {m.strength} {FORM_LABEL[m.form].toLowerCase()}
                  {m.brand_name && <span className="muted"> ({m.brand_name}{m.no_substitution ? ', do not substitute' : ''})</span>}
                </div>
                <div className="rx-med-how">
                  {[m.dose, lowerFirst(ROUTE_LABEL[m.route]), lowerFirst(FREQUENCY_LABEL[m.frequency]),
                    m.timing !== 'any' ? lowerFirst(TIMING_LABEL[m.timing]) : '', durationText(m)].filter(Boolean).join(' · ')}
                </div>
                {m.instructions && <div className="rx-med-note">{m.instructions}</div>}
                {m.quantity && <div className="rx-med-qty">Dispense: {m.quantity}</div>}
              </li>
            ))}
          </ol>
        </>
      )}

      <dl className="rx-facts">
        {c.investigations.length > 0 && <><dt>Investigations</dt><dd>{c.investigations.join(', ')}</dd></>}
        {c.advice && <><dt>Advice</dt><dd className="pre-line">{c.advice}</dd></>}
        {(c.follow_up_date || c.follow_up_note) && (
          <><dt>Follow-up</dt><dd>{[day(c.follow_up_date), c.follow_up_note].filter(Boolean).join(' · ')}</dd></>
        )}
      </dl>

      <footer className="rx-doc-foot">
        <span className="muted small">
          {rx.status === 'void'
            ? `Voided ${when(rx.voided_at)}: ${rx.void_reason}`
            : rx.signed_at
              ? `Digitally signed by ${who?.name ?? rx.doctor_name} on ${when(rx.signed_at)}`
              : 'Not signed yet'}
        </span>
        <span className="rx-doc-actions">
          {onVoid && (
            <button className="link-button danger-link" onClick={onVoid}>
              Void
            </button>
          )}
          {rx.status === 'signed' && (
            <button className="edit-button" onClick={print}>
              Print
            </button>
          )}
        </span>
      </footer>
    </article>
  )
}

// ---- writing one

function PrescriptionEditor({
  rx,
  profile,
  onProfile,
  onClose,
}: {
  rx: Prescription
  profile: DoctorProfile
  onProfile: (profile: DoctorProfile) => void
  onClose: (message?: string) => void
}) {
  const [content, setContent] = useState<RxContent>(rx.content)
  const [busy, setBusy] = useState(false)
  const [problems, setProblems] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [editProfile, setEditProfile] = useState(!profile.registration_number || !profile.qualification)
  const [saved, setSaved] = useState<string | null>(null)

  const set = <K extends keyof RxContent>(key: K, value: RxContent[K]) => setContent((c) => ({ ...c, [key]: value }))
  const setPatient = (change: Partial<RxContent['patient']>) => setContent((c) => ({ ...c, patient: { ...c.patient, ...change } }))
  const setMedicine = (index: number, change: Partial<Medicine>) =>
    set('medicines', content.medicines.map((m, i) => (i === index ? { ...m, ...change } : m)))

  const run = async (action: () => Promise<void>) => {
    setBusy(true)
    setError(null)
    setProblems([])
    try {
      await action()
    } catch (e) {
      if (e instanceof ApiError && e.code === 'incomplete') setProblems(e.message.split(/(?<=\.)\s+/))
      else if (e instanceof ApiError && e.code === 'doctor_profile') {
        setEditProfile(true)
        setError(e.message)
      } else setError(e instanceof Error ? e.message : 'Something went wrong.')
    } finally {
      setBusy(false)
    }
  }

  // what is sent: empty investigation lines left out
  const body = () => JSON.stringify({ ...content, investigations: content.investigations.map((t) => t.trim()).filter(Boolean) })

  const saveDraft = (thenClose = false) => run(async () => {
    await api(`/api/prescriptions/${rx.prescription_id}`, { method: 'PUT', body: body() })
    if (thenClose) onClose()
    else setSaved(`Draft saved at ${new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`)
  })

  const sign = () => {
    if (!window.confirm('Sign and issue this prescription? After signing it cannot be changed (you can void it and write a new one).')) return
    void run(async () => {
      await api(`/api/prescriptions/${rx.prescription_id}/sign`, { method: 'POST', body: body() })
      onClose('Prescription signed. The patient can now see and print it.')
    })
  }

  const discard = () => {
    if (!window.confirm('Delete this draft?')) return
    void run(async () => {
      await api(`/api/prescriptions/${rx.prescription_id}`, { method: 'DELETE' })
      onClose('Draft deleted.')
    })
  }

  return (
    <div className="modal-backdrop">
      <div className="card modal rx-editor" role="dialog" aria-modal="true" aria-labelledby="rx-title">
        <div className="rx-editor-head">
          <h2 id="rx-title">Prescription for {rx.patient_name || rx.patient_email}</h2>
          <button className="edit-button" onClick={() => void saveDraft(true)} disabled={busy}>
            Save and close
          </button>
        </div>

        <DoctorDetails profile={profile} editing={editProfile} onEdit={() => setEditProfile(true)}
                       onSaved={(p) => { onProfile(p); setEditProfile(false); setError(null) }} />

        <section className="rx-section">
          <h4>
            Patient{' '}
            <span className="muted small">
              {rx.content.patient.date_of_birth || rx.content.patient.phone || rx.content.patient.sex
                ? "filled in from the patient's saved details: check them; what you sign is saved for next time"
                : 'confirm the age (Telemedicine Practice Guidelines 3.2.3); what you sign is saved for next time'}
            </span>
          </h4>
          <div className="rx-grid rx-grid-4">
            <label>Date of birth<input type="date" value={content.patient.date_of_birth ?? ''} max={new Date().toISOString().slice(0, 10)}
                   onChange={(e) => setPatient({ date_of_birth: e.target.value || null, ...(e.target.value ? { age: ageFrom(e.target.value) } : {}) })} /></label>
            <label>Age<input value={content.patient.age} placeholder="e.g. 32 years" onChange={(e) => setPatient({ age: e.target.value })} /></label>
            <label>Sex
              <select value={content.patient.sex} onChange={(e) => setPatient({ sex: e.target.value as RxContent['patient']['sex'] })}>
                <option value="">—</option><option value="female">Female</option><option value="male">Male</option><option value="other">Other</option>
              </select>
            </label>
            <label>Weight (kg)<input value={content.patient.weight_kg} inputMode="decimal" onChange={(e) => setPatient({ weight_kg: e.target.value })} /></label>
            <label>Phone<input value={content.patient.phone ?? ''} inputMode="tel" onChange={(e) => setPatient({ phone: e.target.value })} /></label>
            <label>ABHA number<input value={content.patient.abha_number ?? ''} placeholder="14 digits" inputMode="numeric"
                   onChange={(e) => setPatient({ abha_number: e.target.value })} /></label>
            <label className="span-2">Address<input value={content.patient.address ?? ''} onChange={(e) => setPatient({ address: e.target.value })} /></label>
          </div>
        </section>

        <section className="rx-section">
          <h4>Clinical notes</h4>
          <div className="rx-grid">
            <label>Complaints<textarea rows={2} value={content.complaints} onChange={(e) => set('complaints', e.target.value)} /></label>
            <label>Known allergies<textarea rows={1} value={content.allergies} onChange={(e) => set('allergies', e.target.value)} /></label>
            <label>Examination findings and vitals
              <textarea rows={2} value={content.findings} placeholder="BP, pulse, temperature, SpO2, examination" onChange={(e) => set('findings', e.target.value)} />
            </label>
            <div className="rx-diagnosis">
              <label>Diagnosis *<input value={content.diagnosis} onChange={(e) => set('diagnosis', e.target.value)} /></label>
              <div className="segmented" role="group" aria-label="Diagnosis type">
                {(['provisional', 'final'] as const).map((t) => (
                  <button key={t} type="button" className={content.diagnosis_type === t ? 'segment segment-active' : 'segment'}
                          onClick={() => set('diagnosis_type', t)}>
                    {t === 'provisional' ? 'Provisional' : 'Final'}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </section>

        <section className="rx-section">
          <h4>℞ Medicines <span className="muted small">search the Jan Aushadhi list of 2110 generic medicines, or type a generic name; add a brand only if it matters</span></h4>
          {content.medicines.map((m, i) => {
            const clash = allergyClash(m, content.allergies)
            const suggestion = suggestedQuantity(m)
            return (
              <div key={i} className="rx-med-edit">
                <div className="rx-med-edit-head">
                  <span className="rx-med-number">{i + 1}</span>
                  <button type="button" className="link-button danger-link"
                          onClick={() => set('medicines', content.medicines.filter((_, j) => j !== i))}>
                    Remove
                  </button>
                </div>
                <div className="rx-grid rx-grid-4">
                  <label className="span-2">Generic name *
                    <MedicineSearch value={m.generic_name} code={m.code}
                                    onType={(text) => setMedicine(i, { generic_name: text, code: '' })}
                                    onPick={(picked) => setMedicine(i, fromListed(picked, m))} />
                  </label>
                  <label>Brand (optional)<input value={m.brand_name} onChange={(e) => setMedicine(i, { brand_name: e.target.value })} /></label>
                  <label className="rx-check"><input type="checkbox" checked={m.no_substitution}
                         onChange={(e) => setMedicine(i, { no_substitution: e.target.checked })} /> Do not substitute</label>

                  <label>Form<select value={m.form} onChange={(e) => setMedicine(i, { form: e.target.value as Medicine['form'] })}>
                    {Object.entries(FORM_LABEL).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
                  <label>Strength *<input value={m.strength} placeholder="500 mg" onChange={(e) => setMedicine(i, { strength: e.target.value })} /></label>
                  <label>Dose *<input value={m.dose} placeholder="1 tablet / 5 ml" onChange={(e) => setMedicine(i, { dose: e.target.value })} /></label>
                  <label>Route<select value={m.route} onChange={(e) => setMedicine(i, { route: e.target.value as Medicine['route'] })}>
                    {Object.entries(ROUTE_LABEL).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>

                  <label>How often<select value={m.frequency} onChange={(e) => setMedicine(i, { frequency: e.target.value as Medicine['frequency'] })}>
                    {Object.entries(FREQUENCY_LABEL).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
                  <label>When<select value={m.timing} onChange={(e) => setMedicine(i, { timing: e.target.value as Medicine['timing'] })}>
                    {Object.entries(TIMING_LABEL).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
                  <label>Duration
                    <span className="rx-duration">
                      {m.duration_unit !== 'ongoing' && (
                        <input type="number" min={1} max={365} value={m.duration_value ?? ''}
                               onChange={(e) => setMedicine(i, { duration_value: e.target.value ? Number(e.target.value) : null })} />
                      )}
                      <select value={m.duration_unit} onChange={(e) => setMedicine(i, { duration_unit: e.target.value as Medicine['duration_unit'] })}>
                        {Object.entries(DURATION_LABEL).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                      </select>
                    </span>
                  </label>
                  <label>Total quantity *
                    <span className="rx-duration">
                      <input value={m.quantity} placeholder="15 tablets" onChange={(e) => setMedicine(i, { quantity: e.target.value })} />
                      {suggestion && suggestion !== m.quantity && (
                        <button type="button" className="link-button" onClick={() => setMedicine(i, { quantity: suggestion })}>
                          {suggestion}
                        </button>
                      )}
                    </span>
                  </label>

                  <label className="span-4">Instructions{m.frequency === 'as_needed' ? ' * (maximum dose and how often)' : ''}
                    <input value={m.instructions} placeholder={m.frequency === 'as_needed' ? 'e.g. if fever above 100 °F, not more than 4 in 24 hours' : 'e.g. complete the course'}
                           onChange={(e) => setMedicine(i, { instructions: e.target.value })} />
                  </label>
                </div>
                {clash && <p className="rx-warning">⚠ The patient reported an allergy to “{clash}”. Check before prescribing.</p>}
              </div>
            )
          })}
          <button type="button" className="edit-button" onClick={() => set('medicines', [...content.medicines, newMedicine()])}>
            + Add medicine
          </button>
        </section>

        <section className="rx-section">
          <h4>Tests, advice and follow-up</h4>
          <div className="rx-grid">
            <label>Investigations <span className="muted small">(one per line)</span>
              <textarea rows={2} value={content.investigations.join('\n')} placeholder="CBC&#10;Chest X-ray"
                        onChange={(e) => set('investigations', e.target.value.split('\n').filter((line, i, all) => line.trim() || i === all.length - 1))} />
            </label>
            <label>Advice<textarea rows={2} value={content.advice} placeholder="Diet, rest, warning signs to come back for"
                                  onChange={(e) => set('advice', e.target.value)} /></label>
            <div className="rx-grid rx-grid-3">
              <label>Follow-up on<input type="date" value={content.follow_up_date ?? ''} onChange={(e) => set('follow_up_date', e.target.value || null)} /></label>
              <label className="span-2">Follow-up note<input value={content.follow_up_note} placeholder="e.g. with CBC report" onChange={(e) => set('follow_up_note', e.target.value)} /></label>
            </div>
          </div>
        </section>

        {problems.length > 0 && (
          <div className="rx-problems" role="alert">
            <strong>Before signing:</strong>
            <ul>{problems.map((p) => <li key={p}>{p}</li>)}</ul>
          </div>
        )}
        {error && <p className="review-note review-error" role="alert">{error}</p>}
        <div className="buttons rx-editor-buttons">
          <button className="link-button danger-link" onClick={discard} disabled={busy}>Delete draft</button>
          <span className="muted small">{saved}</span>
          <button className="edit-button" onClick={() => void saveDraft()} disabled={busy}>Save draft</button>
          <button className="save-button" onClick={sign} disabled={busy}>{busy ? 'Please wait…' : 'Sign and issue'}</button>
        </div>
      </div>
    </div>
  )
}

// the doctor's own details that go on every prescription (NMC: registration number)
function DoctorDetails({
  profile,
  editing,
  onEdit,
  onSaved,
}: {
  profile: DoctorProfile
  editing: boolean
  onEdit: () => void
  onSaved: (profile: DoctorProfile) => void
}) {
  const [draft, setDraft] = useState(profile)
  const [error, setError] = useState<string | null>(null)

  if (!editing) {
    return (
      <p className="rx-me">
        On the prescription: <b>{profile.qualification}</b>
        {profile.specialty && `, ${profile.specialty}`} · Reg. No. <b>{profile.registration_number}</b> ({profile.registration_council}){' '}
        <button className="link-button" onClick={onEdit}>Change</button>
      </p>
    )
  }

  const save = async (event: React.FormEvent) => {
    event.preventDefault()
    try {
      onSaved(await api<DoctorProfile>('/api/me/doctor-profile', { method: 'PUT', body: JSON.stringify(draft) }))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save.')
    }
  }

  return (
    <form className="rx-section rx-me-form" onSubmit={save}>
      <h4>Your details <span className="muted small">printed on every prescription you sign</span></h4>
      <div className="rx-grid rx-grid-4">
        <label>Qualification *<input required value={draft.qualification} placeholder="MBBS, MD (Medicine)" onChange={(e) => setDraft({ ...draft, qualification: e.target.value })} /></label>
        <label>Specialty<input value={draft.specialty} placeholder="General medicine" onChange={(e) => setDraft({ ...draft, specialty: e.target.value })} /></label>
        <label>Registration no. *<input required value={draft.registration_number} onChange={(e) => setDraft({ ...draft, registration_number: e.target.value })} /></label>
        <label>Council *<input required value={draft.registration_council} placeholder="NMC / State Medical Council" onChange={(e) => setDraft({ ...draft, registration_council: e.target.value })} /></label>
      </div>
      {error && <p className="review-note review-error">{error}</p>}
      <div className="buttons">
        <button className="save-button" type="submit">Save my details</button>
      </div>
    </form>
  )
}

// ---- the patient's own

export function MyPrescriptions() {
  const [list, setList] = useState<Prescription[]>([])
  const [open, setOpen] = useState<string | null>(null)

  useEffect(() => {
    api<Prescription[]>('/api/prescriptions')
      .then((result) => setList(Array.isArray(result) ? result : []))
      .catch(() => setList([]))
  }, [])

  if (list.length === 0) return null
  return (
    <div className="card">
      <h3>Your prescriptions</h3>
      <ul className="plain">
        {list.map((rx) => (
          <li key={rx.prescription_id}>
            <button className="rx-row" onClick={() => setOpen(open === rx.prescription_id ? null : rx.prescription_id)}
                    aria-expanded={open === rx.prescription_id}>
              <span>
                <strong>{rx.prescriber?.name ?? rx.doctor_name}</strong> · {rx.prescriber?.clinic_name ?? rx.clinic_name}
                {rx.status === 'void' && <span className="chip-tag chip-cancelled">Void</span>}
              </span>
              <span className="muted small">{when(rx.signed_at)} {open === rx.prescription_id ? '▴' : '▾'}</span>
            </button>
            {open === rx.prescription_id && <PrescriptionDocument rx={rx} />}
          </li>
        ))}
      </ul>
    </div>
  )
}

// ---- the patient's own details (so doctors need not ask each time)

interface SavedDetails {
  date_of_birth: string | null
  sex: '' | 'female' | 'male' | 'other'
  weight_kg: string
  phone: string
  address: string
  abha_number: string
}

export function MyDetails() {
  const [details, setDetails] = useState<SavedDetails | null>(null)
  const [open, setOpen] = useState(false)
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null)

  useEffect(() => {
    api<SavedDetails>('/api/me/patient-profile').then(setDetails).catch(() => setDetails(null))
  }, [])

  if (!details) return null
  const set = (change: Partial<SavedDetails>) => setDetails({ ...details, ...change })
  const empty = !details.date_of_birth && !details.sex && !details.phone

  const save = async (event: React.FormEvent) => {
    event.preventDefault()
    setMessage(null)
    try {
      setDetails(await api<SavedDetails>('/api/me/patient-profile', { method: 'PUT', body: JSON.stringify(details) }))
      setMessage({ text: 'Saved. Your doctor will see these on your prescription.', error: false })
      setOpen(false)
    } catch (e) {
      setMessage({ text: e instanceof Error ? e.message : 'Could not save.', error: true })
    }
  }

  return (
    <div className="card">
      <div className="rx-card-head">
        <h3>Your details</h3>
        {!open && <button className="edit-button" onClick={() => setOpen(true)}>{empty ? 'Add' : 'Edit'}</button>}
      </div>
      {!open ? (
        empty ? (
          <p className="muted">Add your date of birth, phone and address once, so your doctor does not have to ask each time.</p>
        ) : (
          <p className="muted">
            {[details.date_of_birth && `${ageFrom(details.date_of_birth)} (born ${details.date_of_birth})`, details.sex,
              details.weight_kg && `${details.weight_kg} kg`, details.phone, details.abha_number && `ABHA ${details.abha_number}`]
              .filter(Boolean).join(' · ')}
          </p>
        )
      ) : (
        <form onSubmit={save}>
          <div className="rx-grid rx-grid-3">
            <label>Date of birth<input type="date" value={details.date_of_birth ?? ''} max={new Date().toISOString().slice(0, 10)}
                   onChange={(e) => set({ date_of_birth: e.target.value || null })} /></label>
            <label>Sex
              <select value={details.sex} onChange={(e) => set({ sex: e.target.value as SavedDetails['sex'] })}>
                <option value="">—</option><option value="female">Female</option><option value="male">Male</option><option value="other">Other</option>
              </select>
            </label>
            <label>Weight (kg)<input value={details.weight_kg} inputMode="decimal" onChange={(e) => set({ weight_kg: e.target.value })} /></label>
            <label>Phone<input value={details.phone} inputMode="tel" onChange={(e) => set({ phone: e.target.value })} /></label>
            <label>ABHA number <span className="muted small">(optional)</span>
              <input value={details.abha_number} placeholder="14 digits" inputMode="numeric" onChange={(e) => set({ abha_number: e.target.value })} /></label>
            <label className="span-2">Address<input value={details.address} onChange={(e) => set({ address: e.target.value })} /></label>
          </div>
          <div className="buttons">
            <button type="button" className="edit-button" onClick={() => setOpen(false)}>Cancel</button>
            <button type="submit" className="save-button">Save</button>
          </div>
        </form>
      )}
      {message && <p className={message.error ? 'review-note review-error' : 'review-note'}>{message.text}</p>}
    </div>
  )
}
