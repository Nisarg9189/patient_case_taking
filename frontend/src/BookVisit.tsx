import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from './auth'
import { SharedReports } from './Documents'
import {
  dayKey, formatDay, formatTime, formatVisit, isUpcoming, patientCanChange, type Appointment, type Clinic, type Slots,
} from './booking'

// After the interview: the patient picks a hospital, (optionally) a doctor, a day and a time.
// Booking again for the same interview replaces the earlier booking.
// fixed: the hospital and doctor the patient already chose (then only the day and time are left).
export function BookVisit({ caseId, fixed }: {
  caseId: string | null
  fixed?: { orgId: string; hospital: string; doctorId: string; doctor: string }
}) {
  const [clinics, setClinics] = useState<Clinic[] | null>(null)
  const [mine, setMine] = useState<Appointment[] | null>(null)
  const [changing, setChanging] = useState(false)
  const [orgId, setOrgId] = useState(fixed?.orgId ?? '')
  const [slots, setSlots] = useState<Slots | null>(null)
  const [doctorId, setDoctorId] = useState(fixed?.doctorId ?? '') // '' = any available doctor
  const [chosen, setChosen] = useState<string | null>(null) // the chosen slot's starts_at
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const loadMine = useCallback(() => api<Appointment[]>('/api/appointments').then(setMine), [])

  useEffect(() => {
    Promise.all([api<Clinic[]>('/api/clinics').then(setClinics), loadMine()]).catch((e) => setError(e.message))
  }, [loadMine])

  const loadSlots = useCallback(async (id: string) => {
    setSlots(null)
    setChosen(null)
    if (id) setSlots(await api<Slots>(`/api/clinics/${id}/slots`))
  }, [])

  useEffect(() => {
    loadSlots(orgId).catch((e) => setError(e.message))
  }, [orgId, loadSlots])

  const timeZone = slots?.timezone ?? 'UTC'
  const visible = useMemo(
    () => (slots?.slots ?? []).filter((s) => !doctorId || s.doctors.some((d) => d.doctor_id === doctorId)),
    [slots, doctorId],
  )
  const doctors = useMemo(() => doctorsIn(slots), [slots])
  const chosenSlot = visible.find((s) => s.starts_at === chosen) ?? null

  const current = caseId ? mine?.find((a) => a.case_id === caseId && isUpcoming(a)) : undefined
  // this interview's visit took place: nothing to book or change any more
  const completed = caseId && !current ? mine?.find((a) => a.case_id === caseId && a.status === 'completed') : undefined

  const book = async () => {
    if (!chosenSlot) return
    setBusy(true)
    setError(null)
    try {
      await api<Appointment>('/api/appointments', {
        method: 'POST',
        body: JSON.stringify({ org_id: orgId, starts_at: chosenSlot.starts_at, doctor_id: doctorId || null, case_id: caseId }),
      })
      await loadMine()
      setChanging(false)
      setChosen(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not book this time.')
      await loadSlots(orgId).catch(() => undefined) // someone may have taken it: show what is left
    } finally {
      setBusy(false)
    }
  }

  const cancel = async (appointment: Appointment) => {
    setBusy(true)
    setError(null)
    try {
      await api(`/api/appointments/${appointment.appointment_id}`, { method: 'DELETE' })
      await loadMine()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not cancel.')
    } finally {
      setBusy(false)
    }
  }

  if (clinics === null || mine === null) {
    return (
      <div className="card">
        <h3>Book your visit</h3>
        {error ? <p className="review-note review-error">{error}</p> : <p className="muted">Loading…</p>}
      </div>
    )
  }

  if (completed) {
    return (
      <div className="card booking-done">
        <h3>
          Your visit is completed <span className="tag tag-reviewed">Completed ✓</span>
        </h3>
        <p className="booking-when">{formatVisit(completed)}</p>
        <p>
          {completed.clinic_name}
          {completed.doctor_name && <> · {completed.doctor_name}</>}
        </p>
        <p className="muted small">Your prescription, if the doctor wrote one, is under "Your prescriptions".</p>
      </div>
    )
  }

  if (current && !changing) {
    const canChange = patientCanChange(current)
    return (
      <div className="card booking-done">
        <h3>
          Your visit is booked <span className="tag tag-reviewed">Booked</span>
        </h3>
        <p className="booking-when">{formatVisit(current)}</p>
        <p>
          {current.clinic_name}
          {current.doctor_name && <> · {current.doctor_name}</>}
        </p>
        {current.referred_from && (
          <p className="referral-line">
            Referred by {current.referred_from.doctor_name} ({current.referred_from.clinic_name})
          </p>
        )}
        <p className="muted small">Your answers from this interview are shared with this hospital's doctors.</p>
        {current.case_id && <SharedReports caseId={current.case_id} />}
        {canChange ? (
          <div className="buttons">
            <button className="edit-button" onClick={() => setChanging(true)} disabled={busy}>
              Change
            </button>
            <button className="link-button" onClick={() => void cancel(current)} disabled={busy}>
              Cancel visit
            </button>
          </div>
        ) : (
          <p className="review-note">Your visit has started. To change it, please speak to the clinic.</p>
        )}
        {error && <p className="review-note review-error">{error}</p>}
      </div>
    )
  }

  return (
    <div className="card booking">
      <h3>{current ? 'Change your visit' : 'Book your visit'}</h3>
      {clinics.length === 0 ? (
        <p className="muted">No hospital takes online bookings yet. The clinic will contact you about your visit.</p>
      ) : (
        <>
          {fixed ? (
            <div className="edit-row">
              <span className="row-label">Hospital</span>
              <strong>{fixed.hospital}</strong>
            </div>
          ) : (
            <label className="edit-row">
              <span className="row-label">Hospital</span>
              <select value={orgId} onChange={(e) => setOrgId(e.target.value)}>
                <option value="">Choose a hospital…</option>
                {clinics.map((c) => (
                  <option key={c.org_id} value={c.org_id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </label>
          )}

          {orgId && slots === null && <p className="muted">Loading free times…</p>}
          {slots && slots.slots.length === 0 && (
            <p className="muted">
              No free times in the next {clinics.find((c) => c.org_id === orgId)?.days_ahead ?? 14} days.
              {fixed ? ' The clinic will contact you about your visit, or you can start again with another hospital.' : ' Please choose another hospital.'}
            </p>
          )}

          {slots && slots.slots.length > 0 && (
            <>
              {fixed?.doctorId ? (
                <div className="edit-row">
                  <span className="row-label">Doctor</span>
                  <strong>{fixed.doctor}</strong>
                </div>
              ) : (
                <label className="edit-row">
                  <span className="row-label">Doctor</span>
                  <select value={doctorId} onChange={(e) => { setDoctorId(e.target.value); setChosen(null) }}>
                    <option value="">Any available doctor</option>
                    {doctors.map(([id, name]) => (
                      <option key={id} value={id}>
                        {name}
                      </option>
                    ))}
                  </select>
                </label>
              )}

              <SlotPicker slots={slots} doctorId={doctorId} chosen={chosen} onChoose={setChosen} />
              <p className="muted small">Times are the hospital's local time ({timeZone}).</p>
            </>
          )}

          <div className="buttons">
            {current && (
              <button className="edit-button" onClick={() => setChanging(false)} disabled={busy}>
                Keep my booking
              </button>
            )}
            <button className="save-button" onClick={() => void book()} disabled={!chosenSlot || busy}>
              {busy ? 'Booking…' : chosenSlot ? `Book ${formatDay(chosenSlot.starts_at, timeZone)}, ${formatTime(chosenSlot.starts_at, timeZone)}` : 'Book'}
            </button>
          </div>
        </>
      )}
      {error && <p className="review-note review-error" role="alert">{error}</p>}
    </div>
  )
}

// The patient's upcoming visits (on the interview page before an interview starts).
export function MyAppointments() {
  const [mine, setMine] = useState<Appointment[]>([])
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => api<Appointment[]>('/api/appointments').then(setMine), [])
  useEffect(() => {
    load().catch((e) => setError(e.message))
  }, [load])

  const upcoming = mine.filter(isUpcoming)
  if (upcoming.length === 0 && !error) return null

  const cancel = async (a: Appointment) => {
    setError(null)
    try {
      await api(`/api/appointments/${a.appointment_id}`, { method: 'DELETE' })
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not cancel.')
    }
  }

  return (
    <div className="card">
      <h3>Your upcoming visits</h3>
      <ul className="plain">
        {upcoming.map((a) => (
          <li key={a.appointment_id} className="visit">
            <div>
              <strong>{formatVisit(a)}</strong>
              <div className="muted small">
                {a.clinic_name}
                {a.doctor_name && <> · {a.doctor_name}</>}
                {a.referred_from && <> · referred by {a.referred_from.doctor_name} ({a.referred_from.clinic_name})</>}
              </div>
            </div>
            {patientCanChange(a) ? (
              <button className="link-button" onClick={() => void cancel(a)}>
                Cancel
              </button>
            ) : (
              <span className="muted small">In progress</span>
            )}
            {a.case_id && <SharedReports caseId={a.case_id} />}
          </li>
        ))}
      </ul>
      {error && <p className="review-note review-error">{error}</p>}
    </div>
  )
}

// the doctors who have a free slot, sorted by name: [[doctor_id, name]]
export function doctorsIn(slots: Slots | null) {
  const byId = new Map<string, string>()
  for (const s of slots?.slots ?? []) for (const d of s.doctors) byId.set(d.doctor_id, d.name)
  return [...byId].sort((a, b) => a[1].localeCompare(b[1]))
}

// Day chips, then the free times of the chosen day (only this doctor's, if one is chosen).
export function SlotPicker({
  slots,
  doctorId,
  chosen,
  onChoose,
}: {
  slots: Slots
  doctorId: string // '' = any doctor
  chosen: string | null // the chosen slot's starts_at
  onChoose: (startsAt: string | null) => void
}) {
  const [day, setDay] = useState<string | null>(null)
  const timeZone = slots.timezone
  const visible = slots.slots.filter((s) => !doctorId || s.doctors.some((d) => d.doctor_id === doctorId))
  const days = [...new Set(visible.map((s) => dayKey(s.starts_at, timeZone)))]
  const shownDay = day && days.includes(day) ? day : days[0] ?? null
  const times = visible.filter((s) => dayKey(s.starts_at, timeZone) === shownDay)

  if (visible.length === 0) return <p className="muted">No free times in the booking window.</p>

  return (
    <>
      <div className="row-label">Day</div>
      <div className="chips">
        {days.map((d) => {
          const first = visible.find((s) => dayKey(s.starts_at, timeZone) === d)!
          return (
            <button key={d} type="button" className={d === shownDay ? 'chip chip-selected' : 'chip'}
                    onClick={() => { setDay(d); onChoose(null) }}>
              {formatDay(first.starts_at, timeZone)}
            </button>
          )
        })}
      </div>

      <div className="row-label">Time</div>
      <div className="chips">
        {times.map((s) => {
          const places = s.doctors
            .filter((d) => !doctorId || d.doctor_id === doctorId)
            .reduce((sum, d) => sum + d.places_left, 0)
          return (
            <button key={s.starts_at} type="button" className={s.starts_at === chosen ? 'chip chip-selected' : 'chip'}
                    onClick={() => onChoose(s.starts_at)} title={`${places} place${places === 1 ? '' : 's'} left`}>
              {formatTime(s.starts_at, timeZone)}
              <span className="chip-note">{places} left</span>
            </button>
          )
        })}
      </div>
    </>
  )
}
