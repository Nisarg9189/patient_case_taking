import { useEffect, useState } from 'react'
import { AzureVoice } from './AzureVoice'
import { api } from './auth'
import { MyDocuments, type PatientDocument } from './Documents'
import { HospitalIcon, MapPinIcon, PhoneIcon, CheckIcon } from './icons'
import {
  rupees, type DoctorInfo, type HospitalDetail, type HospitalSummary, type Location, type Target,
} from './hospitals'

// The patient's path to a consultation, as a pipeline: where (state, district) -> which hospital
// (best rated first, with its fee) -> which doctor -> the AI interview, which goes to that
// doctor -> a time with that doctor.

const STEPS = ['Location', 'Hospital', 'Doctor', 'Reports', 'AI interview', 'Book a time']

// the pipeline line: done steps are filled and checked, the current one is marked
export function Pipeline({ current }: { current: number }) {
  return (
    <ol className="pipeline" aria-label="Your consultation, step by step">
      {STEPS.map((label, i) => (
        <li key={label} className={i < current ? 'pipe-done' : i === current ? 'pipe-current' : undefined}
            aria-current={i === current ? 'step' : undefined}>
          <span className="pipe-dot">{i < current ? <CheckIcon size={16} /> : i + 1}</span>
          <span className="pipe-label">{label}</span>
        </li>
      ))}
    </ol>
  )
}

// five stars filled to the rating, with the number and how many patients rated
export function Stars({ value, count }: { value: number | null; count: number }) {
  if (value === null) return <span className="muted small">New · no ratings yet</span>
  return (
    <span className="rating" title={`${value} out of 5`}>
      <span className="stars" style={{ ['--pct' as string]: `${(value / 5) * 100}%` }} aria-hidden="true">★★★★★</span>
      <strong>{value.toFixed(1)}</strong>
      <span className="muted small">({count})</span>
    </span>
  )
}

function initials(name: string) {
  const words = name.replace(/^dr\.?\s+/i, '').split(/\s+/).filter(Boolean)
  return ((words[0]?.[0] ?? '') + (words[1]?.[0] ?? '')).toUpperCase()
}

const doctorName = (name: string) => (/^dr\.?\s/i.test(name) ? name : `Dr. ${name}`)

type Step = 'location' | 'hospitals' | 'hospital' | 'reports' | 'interview'

export function ConsultationFlow({ onExit }: { onExit: () => void }) {
  const [step, setStep] = useState<Step>('location')
  const [state, setState] = useState('')
  const [district, setDistrict] = useState('')
  const [hospitalId, setHospitalId] = useState<string | null>(null)
  const [target, setTarget] = useState<Target | null>(null)
  const [booking, setBooking] = useState(false) // the interview is done: the booking is next
  // the past documents: everything is shared with the chosen doctor unless the patient unticks it
  const [allDocs, setAllDocs] = useState<string[]>([])
  const [unticked, setUnticked] = useState<Set<string>>(new Set())

  const back = () => {
    if (step === 'location') onExit()
    else if (step === 'hospitals') setStep('location')
    else if (step === 'hospital') setStep('hospitals')
    else if (step === 'reports') setStep('hospital')
    else if (!booking) setStep('reports')
  }
  const current = step === 'location' ? 0 : step === 'hospitals' ? 1 : step === 'hospital' ? 2 : step === 'reports' ? 3 : booking ? 5 : 4

  return (
    <section className="flow">
      <div className="flow-top">
        <button className="back-link" onClick={back}>← {step === 'location' ? 'Home' : 'Back'}</button>
        <h2>Start a consultation</h2>
      </div>
      <div className="card flow-pipeline"><Pipeline current={current} /></div>

      {step === 'location' && (
        <LocationStep state={state} district={district} setState={setState} setDistrict={setDistrict}
                      onNext={() => setStep('hospitals')} />
      )}
      {step === 'hospitals' && (
        <HospitalsStep state={state} district={district} onChoose={(id) => { setHospitalId(id); setStep('hospital') }} />
      )}
      {step === 'hospital' && hospitalId && (
        <HospitalStep orgId={hospitalId} onStart={(t) => { setTarget(t); setBooking(false); setStep('reports') }} />
      )}
      {step === 'reports' && target && (
        <>
          <div className="card target-note">
            <HospitalIcon size={22} />
            <span>Your consultation is with <strong>{doctorName(target.doctor)}</strong> at <strong>{target.hospital}</strong>.</span>
          </div>
          <MyDocuments heading="Your past reports and prescriptions"
                       intro="Choose which of your past reports and prescriptions to send to this doctor, and add any you still have. Your doctor sees only what is ticked, with this consultation."
                       share={{
                         who: doctorName(target.doctor),
                         isShared: (id) => !unticked.has(id),
                         toggle: (id) => setUnticked((old) => { const next = new Set(old); if (!next.delete(id)) next.add(id); return next }),
                         onDocs: (docs: PatientDocument[]) => setAllDocs(docs.map((d) => d.document_id)),
                       }} />
          <div className="buttons">
            <button className="primary" onClick={() => { setTarget({ ...target, documentIds: allDocs.filter((id) => !unticked.has(id)) }); setStep('interview') }}>
              Continue to the interview
            </button>
          </div>
        </>
      )}
      {step === 'interview' && target && (
        <>
          <div className="card target-note">
            <HospitalIcon size={22} />
            <span>Your interview goes to <strong>{doctorName(target.doctor)}</strong> at <strong>{target.hospital}</strong>.</span>
          </div>
          <AzureVoice target={target} onDone={() => setBooking(true)} />
        </>
      )}
    </section>
  )
}

// ---- 1. where

function LocationStep({ state, district, setState, setDistrict, onNext }: {
  state: string; district: string; setState: (s: string) => void; setDistrict: (d: string) => void; onNext: () => void
}) {
  const [locations, setLocations] = useState<Location[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api<Location[]>('/api/hospitals/locations').then(setLocations).catch((e) => setError(e.message))
  }, [])

  const districts = locations?.find((l) => l.state === state)?.districts ?? []

  return (
    <div className="card flow-card">
      <h3><MapPinIcon size={20} /> Where do you want to see a doctor?</h3>
      {error && <p className="review-note review-error">{error}</p>}
      {locations === null && !error && <p className="muted">Loading…</p>}
      {locations?.length === 0 && <p className="muted">No hospital is listed yet. Please check again soon.</p>}
      {locations && locations.length > 0 && (
        <>
          <div className="rx-grid rx-grid-3">
            <label>State
              <select value={state} onChange={(e) => { setState(e.target.value); setDistrict('') }}>
                <option value="">Choose a state…</option>
                {locations.map((l) => <option key={l.state} value={l.state}>{l.state}</option>)}
              </select>
            </label>
            <label>District
              <select value={district} onChange={(e) => setDistrict(e.target.value)} disabled={!state}>
                <option value="">{state ? 'Choose a district…' : 'Choose a state first'}</option>
                {districts.map((d) => <option key={d} value={d}>{d}</option>)}
              </select>
            </label>
          </div>
          <div className="buttons">
            <button className="primary" onClick={onNext} disabled={!state || !district}>Find hospitals</button>
          </div>
        </>
      )}
    </div>
  )
}

// ---- 2. which hospital

function HospitalsStep({ state, district, onChoose }: { state: string; district: string; onChoose: (id: string) => void }) {
  const [sort, setSort] = useState<'rating' | 'fee'>('rating')
  const [list, setList] = useState<HospitalSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    api<HospitalSummary[]>(`/api/hospitals?state=${encodeURIComponent(state)}&district=${encodeURIComponent(district)}&sort=${sort}`)
      .then((rows) => live && setList(rows))
      .catch((e) => live && setError(e.message))
    return () => { live = false }
  }, [state, district, sort])

  return (
    <div className="flow-card">
      <div className="flow-row">
        <h3>Hospitals in {district}, {state}</h3>
        <div className="chips">
          <button className={sort === 'rating' ? 'chip chip-selected' : 'chip'} onClick={() => { setList(null); setSort('rating') }}>Top rated</button>
          <button className={sort === 'fee' ? 'chip chip-selected' : 'chip'} onClick={() => { setList(null); setSort('fee') }}>Lowest fee</button>
        </div>
      </div>
      {error && <p className="review-note review-error">{error}</p>}
      {list === null && !error && <p className="muted">Loading…</p>}
      {list?.length === 0 && <div className="card"><p className="muted">No hospital is listed in this district yet. Try another district.</p></div>}
      <ul className="hospital-list">
        {list?.map((h, i) => (
          <li key={h.org_id}>
            <button className="card hospital-card" onClick={() => onChoose(h.org_id)}>
              <span className="hospital-icon" aria-hidden="true"><HospitalIcon size={28} /></span>
              <span className="hospital-main">
                <span className="hospital-name">
                  {h.name}
                  {sort === 'rating' && i === 0 && h.rating !== null && <span className="chip-tag chip-good">Top rated</span>}
                </span>
                <Stars value={h.rating} count={h.rating_count} />
                <span className="muted small">
                  {h.district}{h.address ? ` · ${h.address}` : ''} · {h.doctor_count} doctor{h.doctor_count === 1 ? '' : 's'}
                </span>
                {h.specialties.length > 0 && (
                  <span className="hospital-tags">{h.specialties.map((s) => <span key={s} className="chip-tag">{s}</span>)}</span>
                )}
              </span>
              <span className="hospital-fee">
                <strong>{rupees(h.consultation_fee) ?? '—'}</strong>
                <span className="muted small">{h.consultation_fee === null ? 'fee not shown' : 'consultation'}</span>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

// ---- 3. the hospital's page, and choosing its doctor

function HospitalStep({ orgId, onStart }: { orgId: string; onStart: (target: Target) => void }) {
  const [hospital, setHospital] = useState<HospitalDetail | null>(null)
  const [doctor, setDoctor] = useState<DoctorInfo | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = () => api<HospitalDetail>(`/api/hospitals/${orgId}`).then(setHospital).catch((e) => setError(e.message))
  useEffect(() => {
    void load()
  }, [orgId]) // eslint-disable-line react-hooks/exhaustive-deps

  if (error) return <p className="review-note review-error">{error}</p>
  if (!hospital) return <p className="muted">Loading…</p>

  return (
    <div className="flow-card hospital-page">
      <div className="card hospital-head">
        <span className="hospital-icon hospital-icon-big" aria-hidden="true"><HospitalIcon size={36} /></span>
        <div className="hospital-head-text">
          <h3>{hospital.name}</h3>
          <Stars value={hospital.rating} count={hospital.rating_count} />
          <p className="muted small"><MapPinIcon size={14} /> {[hospital.address, hospital.district, hospital.state].filter(Boolean).join(', ')}</p>
        </div>
        <div className="hospital-fee hospital-fee-big">
          <strong>{rupees(hospital.consultation_fee) ?? '—'}</strong>
          <span className="muted small">{hospital.consultation_fee === null ? 'fee not shown' : 'consultation fee'}</span>
        </div>
      </div>

      {hospital.description && <div className="card"><h3>About</h3><p className="pre-line">{hospital.description}</p></div>}

      <div className="card">
        <h3>Contact</h3>
        <dl className="summary-list">
          {hospital.phone && <div className="summary-item"><dt>Phone</dt><dd><PhoneIcon size={14} /> {hospital.phone}</dd></div>}
          {hospital.email && <div className="summary-item"><dt>Email</dt><dd>{hospital.email}</dd></div>}
          {hospital.registration_number && <div className="summary-item"><dt>Registration no.</dt><dd>{hospital.registration_number}</dd></div>}
          {!hospital.phone && !hospital.email && !hospital.registration_number && <p className="muted">No contact details added.</p>}
        </dl>
      </div>

      <div className="card">
        <h3>Choose your doctor</h3>
        <ul className="doctor-list">
          {hospital.doctors.map((d) => (
            <li key={d.doctor_id}>
              <button className={doctor?.doctor_id === d.doctor_id ? 'doctor-card doctor-selected' : 'doctor-card'}
                      onClick={() => setDoctor(d)} aria-pressed={doctor?.doctor_id === d.doctor_id}>
                <span className="doctor-avatar" aria-hidden="true">{initials(d.name)}</span>
                <span className="doctor-text">
                  <strong>{doctorName(d.name)}</strong>
                  <span className="muted small">{[d.qualification, d.specialty].filter(Boolean).join(' · ') || 'Doctor'}</span>
                </span>
                {doctor?.doctor_id === d.doctor_id && <CheckIcon size={20} />}
              </button>
            </li>
          ))}
        </ul>
        <p className="muted small">
          Next, our assistant asks you a few questions about your health. Your answers go to the doctor you choose,
          and then you pick a time with them.
        </p>
        <div className="buttons">
          <button className="primary" disabled={!doctor}
                  onClick={() => doctor && onStart({ orgId: hospital.org_id, hospital: hospital.name, doctorId: doctor.doctor_id, doctor: doctor.name })}>
            {doctor ? `Start consultation with ${doctorName(doctor.name)}` : 'Choose a doctor to continue'}
          </button>
        </div>
      </div>

      <RatingCard hospital={hospital} onSaved={load} />
      {hospital.reviews.length > 0 && (
        <div className="card">
          <h3>What patients say</h3>
          <ul className="plain review-list">
            {hospital.reviews.map((r, i) => (
              <li key={i}>
                <Stars value={r.rating} count={0} />
                <p>{r.comment}</p>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

// rate the hospital once a visit is booked there
function RatingCard({ hospital, onSaved }: { hospital: HospitalDetail; onSaved: () => Promise<unknown> }) {
  const [rating, setRating] = useState(hospital.my_rating?.rating ?? 0)
  const [comment, setComment] = useState(hospital.my_rating?.comment ?? '')
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null)
  if (!hospital.can_rate) return null

  const save = async () => {
    setMessage(null)
    try {
      await api(`/api/hospitals/${hospital.org_id}/rating`, { method: 'PUT', body: JSON.stringify({ rating, comment }) })
      setMessage({ text: 'Thank you for rating this hospital.', error: false })
      await onSaved()
    } catch (e) {
      setMessage({ text: e instanceof Error ? e.message : 'Could not save your rating.', error: true })
    }
  }

  return (
    <div className="card">
      <h3>Rate this hospital</h3>
      <div className="star-input" role="radiogroup" aria-label="Your rating">
        {[1, 2, 3, 4, 5].map((n) => (
          <button key={n} type="button" role="radio" aria-checked={rating === n} aria-label={`${n} star${n === 1 ? '' : 's'}`}
                  className={n <= rating ? 'star-on' : undefined} onClick={() => setRating(n)}>★</button>
        ))}
      </div>
      <label className="field-stack">Comment <span className="muted small">(optional)</span>
        <textarea rows={2} maxLength={500} value={comment} onChange={(e) => setComment(e.target.value)} />
      </label>
      {message && <p className={message.error ? 'review-note review-error' : 'review-note'} role="status">{message.text}</p>}
      <div className="buttons">
        <button className="save-button" disabled={rating === 0} onClick={() => void save()}>Save rating</button>
      </div>
    </div>
  )
}
