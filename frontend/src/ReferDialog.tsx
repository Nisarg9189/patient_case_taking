import { useEffect, useMemo, useState } from 'react'
import { api } from './auth'
import { doctorsIn, SlotPicker } from './BookVisit'
import { formatDay, formatTime, formatVisit, type Appointment, type Clinic, type Slots } from './booking'

// A doctor sends a patient's visit, and with it the interview, to another doctor (usually
// at another hospital) at a free time there, with a note for that doctor.
export function ReferDialog({
  appointment,
  onClose,
  onReferred,
}: {
  appointment: Appointment
  onClose: () => void
  onReferred: (message: string) => void
}) {
  const [clinics, setClinics] = useState<Clinic[] | null>(null)
  const [orgId, setOrgId] = useState('')
  const [slots, setSlots] = useState<Slots | null>(null)
  const [doctorId, setDoctorId] = useState('')
  const [chosen, setChosen] = useState<string | null>(null)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api<Clinic[]>('/api/clinics').then(setClinics).catch((e) => setError(e.message))
  }, [])

  const chooseClinic = (id: string) => {
    setOrgId(id)
    setSlots(null)
    setDoctorId('')
    setChosen(null)
    if (id) api<Slots>(`/api/clinics/${id}/slots`).then(setSlots).catch((e) => setError(e.message))
  }

  // not the doctor the visit is already with, at the same hospital
  const doctors = useMemo(
    () => doctorsIn(slots).filter(([id]) => !(orgId === appointment.org_id && id === appointment.doctor_id)),
    [slots, orgId, appointment],
  )
  const upcoming = new Date(appointment.ends_at).getTime() > Date.now()
  const ready = orgId && doctorId && chosen && note.trim()

  const send = async () => {
    if (!ready || !slots) return
    setBusy(true)
    setError(null)
    try {
      const result = await api<{ appointment: Appointment; moved: boolean }>(
        `/api/appointments/${appointment.appointment_id}/refer`,
        { method: 'POST', body: JSON.stringify({ org_id: orgId, doctor_id: doctorId, starts_at: chosen, note }) },
      )
      const to = result.appointment
      onReferred(
        `${appointment.patient_name || appointment.patient_email} referred to ${to.doctor_name} at ${to.clinic_name}, ${formatVisit(to)}.`,
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not refer.')
      if (orgId) api<Slots>(`/api/clinics/${orgId}/slots`).then(setSlots).catch(() => undefined)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="modal-backdrop">
      <div className="card modal" role="dialog" aria-modal="true" aria-labelledby="refer-title">
        <h2 id="refer-title">Refer {appointment.patient_name || appointment.patient_email}</h2>
        <p className="muted">
          Visit {formatVisit(appointment)} at {appointment.clinic_name}. The receiving doctor gets the patient's interview
          and your note.{' '}
          {upcoming
            ? 'This visit moves to the new doctor and time.'
            : 'This visit has taken place and stays as it is; a new visit is booked.'}
        </p>

        {clinics === null ? (
          <p className="muted">Loading…</p>
        ) : (
          <div className="booking">
            <label className="edit-row">
              <span className="row-label">Hospital</span>
              <select value={orgId} onChange={(e) => chooseClinic(e.target.value)}>
                <option value="">Choose a hospital…</option>
                {clinics.map((c) => (
                  <option key={c.org_id} value={c.org_id}>
                    {c.name}
                    {c.org_id === appointment.org_id ? ' (this hospital)' : ''}
                  </option>
                ))}
              </select>
            </label>

            {orgId && slots === null && <p className="muted">Loading free times…</p>}
            {slots && doctors.length === 0 && <p className="muted">No doctor there has a free time. Choose another hospital.</p>}
            {slots && doctors.length > 0 && (
              <>
                <label className="edit-row">
                  <span className="row-label">Doctor</span>
                  <select value={doctorId} onChange={(e) => { setDoctorId(e.target.value); setChosen(null) }}>
                    <option value="">Choose a doctor…</option>
                    {doctors.map(([id, name]) => (
                      <option key={id} value={id}>
                        {name}
                      </option>
                    ))}
                  </select>
                </label>
                {doctorId && <SlotPicker slots={slots} doctorId={doctorId} chosen={chosen} onChoose={setChosen} />}
              </>
            )}

            <label className="edit-row">
              <span className="row-label">Note for the doctor</span>
              <textarea rows={3} maxLength={2000} value={note} onChange={(e) => setNote(e.target.value)}
                        placeholder="Why you are referring, and what to look at" />
            </label>
            <p className="muted small">The patient sees who referred them and where, but not this note.</p>
          </div>
        )}

        {error && <p className="review-note review-error" role="alert">{error}</p>}
        <div className="buttons">
          <button className="edit-button" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button className="save-button" onClick={() => void send()} disabled={!ready || busy}>
            {busy
              ? 'Sending…'
              : chosen && slots
                ? `Refer for ${formatDay(chosen, slots.timezone)}, ${formatTime(chosen, slots.timezone)}`
                : 'Refer'}
          </button>
        </div>
      </div>
    </div>
  )
}
