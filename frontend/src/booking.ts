// Shapes of the appointment API (backend/booking_api.py) and date/time helpers.
// Times are shown in the clinic's own time zone, whatever the viewer's is.

export interface Clinic {
  org_id: string
  name: string
  timezone: string
  days_ahead: number
}

export interface Slot {
  starts_at: string
  ends_at: string
  doctors: { doctor_id: string; name: string; places_left: number }[]
}

export interface Slots {
  timezone: string
  slot_minutes: number
  slots: Slot[]
}

export interface Appointment {
  appointment_id: string
  org_id: string
  clinic_name: string
  timezone: string
  doctor_id: string
  doctor_name: string | null
  patient_user_id: string
  patient_name: string | null
  patient_email: string | null
  case_id: string | null
  starts_at: string
  ends_at: string
  status: 'booked' | 'cancelled' | 'referred' | 'completed'
  completed_at?: string | null
  complaint?: string | null // the interview's main complaint (doctors and nurses only)
  // this visit came from a doctor's referral (the note is only sent to doctors and nurses)
  referred_from: { clinic_name: string; doctor_name: string | null; note?: string; at: string } | null
  // this visit was referred on to another doctor
  referred_to: { clinic_name: string; doctor_name: string | null; starts_at: string; timezone: string; at: string } | null
}

export type Hours = [string, string]

export interface Schedule {
  timezone: string
  slot_minutes: number
  patients_per_slot: number
  days_ahead: number
  opening_hours: (Hours | null)[] // Monday first
}

export interface DoctorAvailability {
  doctor_id: string
  name: string | null
  email: string
  weekly: Hours[][] // Monday first
  days_off: string[] // YYYY-MM-DD
}

export const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

export function formatTime(iso: string, timeZone: string) {
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', timeZone })
}

export function formatDay(iso: string, timeZone: string) {
  return new Date(iso).toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short', timeZone })
}

export function formatVisit(a: Pick<Appointment, 'starts_at' | 'ends_at' | 'timezone'>) {
  return `${formatDay(a.starts_at, a.timezone)}, ${formatTime(a.starts_at, a.timezone)} – ${formatTime(a.ends_at, a.timezone)}`
}

// YYYY-MM-DD of this moment in the clinic's time zone (for grouping slots by day)
export function dayKey(iso: string, timeZone: string) {
  return new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(iso))
}

export function isUpcoming(a: Appointment) {
  return a.status === 'booked' && new Date(a.ends_at).getTime() > Date.now()
}

// a patient may cancel or change a visit only before it starts (the server checks this too)
export function patientCanChange(a: Appointment) {
  return a.status === 'booked' && new Date(a.starts_at).getTime() > Date.now()
}
