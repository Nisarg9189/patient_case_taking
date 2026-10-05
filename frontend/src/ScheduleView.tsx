import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from './auth'
import { PageHeader } from './PageHeader'
import { WEEKDAYS, type DoctorAvailability, type Hours, type Schedule } from './booking'

interface Org {
  org_id: string
  name: string
}

const SLOT_LENGTHS = [5, 10, 15, 20, 30, 45, 60, 90, 120]

function timeZones(current: string) {
  try {
    const all = Intl.supportedValuesOf('timeZone')
    return all.includes(current) ? all : [current, ...all]
  } catch {
    return [current]
  }
}

// Clinic admins: when the clinic is open, how slots work, and when each doctor sees patients.
export function ScheduleView() {
  const [orgs, setOrgs] = useState<Org[] | null>(null)
  const [orgId, setOrgId] = useState<string | null>(null)
  const [schedule, setSchedule] = useState<Schedule | null>(null)
  const [doctors, setDoctors] = useState<DoctorAvailability[] | null>(null)
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null)

  useEffect(() => {
    api<Org[]>('/api/orgs')
      .then((list) => {
        setOrgs(list)
        setOrgId(list[0]?.org_id ?? null)
      })
      .catch((e) => setMessage({ text: e.message, error: true }))
  }, [])

  const load = useCallback(async (id: string) => {
    const result = await api<{ schedule: Schedule; doctors: DoctorAvailability[] }>(`/api/orgs/${id}/schedule`)
    setSchedule(result.schedule)
    setDoctors(result.doctors)
  }, [])

  useEffect(() => {
    if (orgId) load(orgId).catch((e) => setMessage({ text: e.message, error: true }))
  }, [orgId, load])

  const run = async (action: () => Promise<unknown>, success: string) => {
    setMessage(null)
    try {
      await action()
      setMessage({ text: success, error: false })
    } catch (e) {
      setMessage({ text: e instanceof Error ? e.message : 'Could not save.', error: true })
    }
  }

  const saveSchedule = (event: React.FormEvent) => {
    event.preventDefault()
    void run(() => api(`/api/orgs/${orgId}/schedule`, { method: 'PUT', body: JSON.stringify(schedule) }),
             'Opening hours saved.')
  }

  const setHours = (index: number, hours: Hours | null) =>
    setSchedule((s) => s && { ...s, opening_hours: s.opening_hours.map((h, i) => (i === index ? hours : h)) })

  return (
    <div className="page">
      <PageHeader tabs={[['schedule', 'Clinic schedule']]} active="schedule" />
      <p className="page-intro">Opening hours, appointment slots and doctors' availability. Patients book from these.</p>
      {message && <div className={message.error ? 'notice' : 'notice notice-ok'}>{message.text}</div>}

      <div className="card">
        <label className="edit-row">
          <span className="row-label">Clinic</span>
          <select value={orgId ?? ''} onChange={(e) => setOrgId(e.target.value)}>
            {(orgs ?? []).map((o) => (
              <option key={o.org_id} value={o.org_id}>
                {o.name}
              </option>
            ))}
          </select>
        </label>

        {schedule === null ? (
          <p className="muted">Loading…</p>
        ) : (
          <form className="schedule-form" onSubmit={saveSchedule}>
            <h3>Opening hours</h3>
            <table className="hours-table">
              <tbody>
                {WEEKDAYS.map((name, index) => {
                  const hours = schedule.opening_hours[index]
                  return (
                    <tr key={name}>
                      <td>{name}</td>
                      <td>
                        <label className="check">
                          <input type="checkbox" checked={!!hours}
                                 onChange={(e) => setHours(index, e.target.checked ? ['09:00', '17:00'] : null)} />
                          Open
                        </label>
                      </td>
                      <td>
                        {hours ? (
                          <span className="time-range">
                            <input type="time" value={hours[0]} required
                                   onChange={(e) => setHours(index, [e.target.value, hours[1]])} />
                            to
                            <input type="time" value={hours[1]} required
                                   onChange={(e) => setHours(index, [hours[0], e.target.value])} />
                          </span>
                        ) : (
                          <span className="muted">Closed</span>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>

            <h3>Appointments</h3>
            <div className="settings-grid">
              <label>
                Slot length
                <select value={schedule.slot_minutes}
                        onChange={(e) => setSchedule({ ...schedule, slot_minutes: Number(e.target.value) })}>
                  {[...new Set([...SLOT_LENGTHS, schedule.slot_minutes])].sort((a, b) => a - b).map((m) => (
                    <option key={m} value={m}>
                      {m} minutes
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Patients per slot, per doctor
                <input type="number" min={1} max={50} value={schedule.patients_per_slot} required
                       onChange={(e) => setSchedule({ ...schedule, patients_per_slot: Number(e.target.value) })} />
              </label>
              <label>
                Patients can book up to
                <span className="with-unit">
                  <input type="number" min={1} max={90} value={schedule.days_ahead} required
                         onChange={(e) => setSchedule({ ...schedule, days_ahead: Number(e.target.value) })} />
                  days ahead
                </span>
              </label>
              <label>
                Time zone
                <select value={schedule.timezone} onChange={(e) => setSchedule({ ...schedule, timezone: e.target.value })}>
                  {timeZones(schedule.timezone).map((tz) => (
                    <option key={tz} value={tz}>
                      {tz}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <p className="muted small">
              A time can be booked when the clinic is open and a doctor is available. Each available doctor takes{' '}
              {schedule.patients_per_slot} patient{schedule.patients_per_slot === 1 ? '' : 's'} per slot. Changes do not
              move or cancel visits already booked.
            </p>
            <div className="buttons">
              <button className="save-button" type="submit">
                Save opening hours
              </button>
            </div>
          </form>
        )}
      </div>

      {orgId && <ClinicDetailsCard key={orgId} orgId={orgId} />}

      {doctors !== null && schedule !== null && orgId && (
        <div className="card">
          <h3>Doctors' availability</h3>
          {doctors.length === 0 ? (
            <p className="muted">This clinic has no doctors yet. Give someone the Doctor role on the Clinic members page.</p>
          ) : (
            doctors.map((doctor) => (
              <DoctorEditor key={doctor.doctor_id + JSON.stringify([doctor.weekly, doctor.days_off])} doctor={doctor} clinicHours={schedule.opening_hours}
                            onSave={(weekly, days_off) =>
                              run(async () => {
                                await api(`/api/orgs/${orgId}/doctors/${doctor.doctor_id}/availability`, {
                                  method: 'PUT',
                                  body: JSON.stringify({ weekly, days_off }),
                                })
                                await load(orgId)
                              }, `Availability of ${doctor.name || doctor.email} saved.`)} />
            ))
          )}
        </div>
      )}
    </div>
  )
}

interface ClinicDetails {
  address: string
  phone: string
  email: string
  registration_number: string
  state: string
  district: string
  consultation_fee: number | null
  description: string
  doctor_count?: number // read-only: patients only find a hospital that has a doctor
}

// the clinic's contact details, printed at the top of its prescriptions
function ClinicDetailsCard({ orgId }: { orgId: string }) {
  const [details, setDetails] = useState<ClinicDetails | null>(null)
  const [states, setStates] = useState<string[]>([])
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null)

  useEffect(() => {
    api<ClinicDetails>(`/api/orgs/${orgId}/details`).then(setDetails).catch((e) => setMessage({ text: e.message, error: true }))
    api<string[]>('/api/states').then(setStates).catch(() => undefined)
  }, [orgId])

  if (!details) return null
  const set = <K extends keyof ClinicDetails>(key: K, value: ClinicDetails[K]) => setDetails({ ...details, [key]: value })

  const save = async (event: React.FormEvent) => {
    event.preventDefault()
    setMessage(null)
    try {
      setDetails(await api<ClinicDetails>(`/api/orgs/${orgId}/details`, { method: 'PUT', body: JSON.stringify(details) }))
      setMessage({ text: 'Clinic details saved. Patients see the hospital page, and prescriptions signed from now on carry the contact details.', error: false })
    } catch (e) {
      setMessage({ text: e instanceof Error ? e.message : 'Could not save.', error: true })
    }
  }

  return (
    <form className="card" onSubmit={save}>
      <h3>Clinic details <span className="muted small">shown to patients, and printed on every prescription</span></h3>
      <div className="rx-grid rx-grid-3">
        <label className="span-2">Address<textarea rows={2} value={details.address} placeholder="Building, street, area, city, PIN"
                                             onChange={(e) => set('address', e.target.value)} /></label>
        <div className="rx-grid">
          <label>Phone<input value={details.phone} inputMode="tel" placeholder="+91 …" onChange={(e) => set('phone', e.target.value)} /></label>
          <label>Email<input type="email" value={details.email} onChange={(e) => set('email', e.target.value)} /></label>
        </div>
        <label>Clinic registration no.<input value={details.registration_number} placeholder="Clinical Establishments registration"
                                            onChange={(e) => set('registration_number', e.target.value)} /></label>
      </div>
      <h4 className="details-subhead">How patients find you</h4>
      <p className="muted small">Patients choose a state and district, then a hospital. You are listed once both are set and you have a doctor.</p>
      {(() => {
        const missing = [!details.state && 'a state', !details.district.trim() && 'a district', details.doctor_count === 0 && 'a doctor (add one under Clinic members)']
          .filter(Boolean)
        return missing.length === 0 ? (
          <p className="listing-status listing-ok" role="status">✓ Patients can find this hospital by {details.district}, {details.state}. Save after any change.</p>
        ) : (
          <p className="listing-status listing-warn" role="status">Not shown to patients yet. Still needed: {missing.join(', ')}.</p>
        )
      })()}
      <div className="rx-grid rx-grid-3">
        <label>State
          <select value={details.state} onChange={(e) => set('state', e.target.value)}>
            <option value="">Not listed</option>
            {states.map((st) => <option key={st} value={st}>{st}</option>)}
          </select>
        </label>
        <label>District<input value={details.district} placeholder="e.g. Ahmedabad" onChange={(e) => set('district', e.target.value)} /></label>
        <label>Consultation fee (₹)
          <input type="number" min={0} max={100000} inputMode="numeric" value={details.consultation_fee ?? ''} placeholder="Leave empty to hide"
                 onChange={(e) => set('consultation_fee', e.target.value === '' ? null : Number(e.target.value))} />
        </label>
        <label className="span-3">About the hospital<textarea rows={3} maxLength={600} value={details.description}
               placeholder="Specialities, facilities, timings: a few lines patients will read" onChange={(e) => set('description', e.target.value)} /></label>
      </div>
      {message && <p className={message.error ? 'review-note review-error' : 'review-note'}>{message.text}</p>}
      <div className="buttons">
        <button className="save-button" type="submit">Save clinic details</button>
      </div>
    </form>
  )
}

function DoctorEditor({
  doctor,
  clinicHours,
  onSave,
}: {
  doctor: DoctorAvailability
  clinicHours: (Hours | null)[]
  onSave: (weekly: Hours[][], daysOff: string[]) => Promise<void>
}) {
  const [weekly, setWeekly] = useState<Hours[][]>(doctor.weekly)
  const [daysOff, setDaysOff] = useState<string[]>(doctor.days_off)
  const [newDayOff, setNewDayOff] = useState('')
  const [saving, setSaving] = useState(false)
  const changed = useMemo(
    () => JSON.stringify([weekly, daysOff]) !== JSON.stringify([doctor.weekly, doctor.days_off]),
    [weekly, daysOff, doctor],
  )

  const setRanges = (day: number, ranges: Hours[]) => setWeekly(weekly.map((r, i) => (i === day ? ranges : r)))
  const today = new Date().toISOString().slice(0, 10)

  const save = async () => {
    setSaving(true)
    try {
      await onSave(weekly, daysOff)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="doctor-editor">
      <h4>
        {doctor.name || doctor.email} <span className="muted small">{doctor.name ? doctor.email : ''}</span>
      </h4>
      <table className="hours-table">
        <tbody>
          {WEEKDAYS.map((name, day) => (
            <tr key={name}>
              <td>{name}</td>
              <td>
                {weekly[day].length === 0 && (
                  <span className="muted">{clinicHours[day] ? 'Not available' : 'Clinic closed'}</span>
                )}
                {weekly[day].map((range, index) => (
                  <span key={index} className="time-range">
                    <input type="time" value={range[0]}
                           onChange={(e) => setRanges(day, weekly[day].map((r, i) => (i === index ? [e.target.value, r[1]] : r)))} />
                    to
                    <input type="time" value={range[1]}
                           onChange={(e) => setRanges(day, weekly[day].map((r, i) => (i === index ? [r[0], e.target.value] : r)))} />
                    <button type="button" className="link-button" aria-label="Remove these hours"
                            onClick={() => setRanges(day, weekly[day].filter((_, i) => i !== index))}>
                      ✕
                    </button>
                  </span>
                ))}
              </td>
              <td>
                <button type="button" className="link-button"
                        onClick={() => setRanges(day, [...weekly[day], clinicHours[day] ?? ['09:00', '13:00']])}>
                  + Add hours
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="days-off">
        <span className="row-label">Days off</span>
        <div className="chips">
          {daysOff.map((d) => (
            <span key={d} className="chip chip-static">
              {new Date(`${d}T00:00`).toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })}
              <button type="button" className="link-button" aria-label={`Remove ${d}`}
                      onClick={() => setDaysOff(daysOff.filter((x) => x !== d))}>
                ✕
              </button>
            </span>
          ))}
          <span className="time-range">
            <input type="date" min={today} value={newDayOff} onChange={(e) => setNewDayOff(e.target.value)} />
            <button type="button" className="edit-button" disabled={!newDayOff}
                    onClick={() => {
                      setDaysOff([...new Set([...daysOff, newDayOff])].sort())
                      setNewDayOff('')
                    }}>
              Add day off
            </button>
          </span>
        </div>
      </div>

      <div className="buttons">
        <button type="button" className="edit-button"
                onClick={() => setWeekly(clinicHours.map((h) => (h ? [h] : [])))}>
          Same as opening hours
        </button>
        <button type="button" className="save-button" disabled={!changed || saving} onClick={() => void save()}>
          {saving ? 'Saving…' : 'Save'}
        </button>
      </div>
    </div>
  )
}
