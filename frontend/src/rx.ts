// Prescriptions (backend/prescriptions.py): shapes, plain-language labels and helpers.

export type Form = 'tablet' | 'capsule' | 'syrup' | 'suspension' | 'injection' | 'drops' | 'ointment' | 'cream' | 'gel'
  | 'inhaler' | 'sachet' | 'lotion' | 'spray' | 'other'
export type Route = 'oral' | 'sublingual' | 'intravenous' | 'intramuscular' | 'subcutaneous' | 'topical' | 'inhaled'
  | 'nasal' | 'eye' | 'ear' | 'rectal' | 'vaginal' | 'other'
export type Frequency = 'once_daily' | 'twice_daily' | 'three_times_daily' | 'four_times_daily' | 'every_6_hours'
  | 'every_8_hours' | 'at_bedtime' | 'once_weekly' | 'as_needed' | 'stat' | 'other'
export type Timing = 'after_food' | 'before_food' | 'with_food' | 'empty_stomach' | 'any'
export type DurationUnit = 'days' | 'weeks' | 'months' | 'ongoing'

export interface Medicine {
  code?: string // Jan Aushadhi (PMBJP) drug code, when picked from the medicine list
  generic_name: string
  brand_name: string
  no_substitution: boolean
  form: Form
  strength: string
  dose: string
  route: Route
  frequency: Frequency
  timing: Timing
  duration_value: number | null
  duration_unit: DurationUnit
  quantity: string
  instructions: string
}

export interface PatientDetails {
  age: string
  date_of_birth?: string | null // YYYY-MM-DD
  sex: '' | 'female' | 'male' | 'other'
  weight_kg: string
  phone?: string
  address?: string
  abha_number?: string // Ayushman Bharat Health Account number, 14 digits
}

export interface RxContent {
  patient: PatientDetails
  complaints: string
  allergies: string
  findings: string
  diagnosis: string
  diagnosis_type: 'provisional' | 'final'
  medicines: Medicine[]
  investigations: string[]
  advice: string
  follow_up_date: string | null
  follow_up_note: string
}

export interface Prescriber {
  name: string
  qualification: string
  specialty: string
  registration_number: string
  registration_council: string
  clinic_name: string
  clinic_address?: string
  clinic_phone?: string
  clinic_email?: string
  clinic_registration?: string
}

export interface Prescription {
  prescription_id: string
  case_id: string
  org_id: string
  clinic_name: string
  doctor_id: string
  doctor_name: string | null
  patient_name: string | null
  patient_email: string | null
  status: 'draft' | 'signed' | 'void'
  content: RxContent
  prescriber: Prescriber | null
  created_at: string
  updated_at: string
  signed_at: string | null
  voided_at: string | null
  void_reason: string | null
}

export interface DoctorProfile {
  qualification: string
  specialty: string
  registration_number: string
  registration_council: string
}

// "32 years", or "14 months" under 2 years (the age on the day it is written)
export function ageFrom(dateOfBirth: string, today = new Date()) {
  const born = new Date(`${dateOfBirth}T12:00:00`)
  if (Number.isNaN(born.getTime()) || born > today) return ''
  let months = (today.getFullYear() - born.getFullYear()) * 12 + today.getMonth() - born.getMonth()
  if (today.getDate() < born.getDate()) months -= 1
  return months < 24 ? `${months} month${months === 1 ? '' : 's'}` : `${Math.floor(months / 12)} years`
}

export const FORM_LABEL: Record<Form, string> = {
  tablet: 'Tablet', capsule: 'Capsule', syrup: 'Syrup', suspension: 'Suspension', injection: 'Injection',
  drops: 'Drops', ointment: 'Ointment', cream: 'Cream', gel: 'Gel', inhaler: 'Inhaler', sachet: 'Sachet',
  lotion: 'Lotion', spray: 'Spray', other: 'Other',
}

export const ROUTE_LABEL: Record<Route, string> = {
  oral: 'By mouth', sublingual: 'Under the tongue', intravenous: 'Intravenous (IV)', intramuscular: 'Intramuscular (IM)',
  subcutaneous: 'Under the skin (SC)', topical: 'On the skin', inhaled: 'Inhaled', nasal: 'In the nose',
  eye: 'In the eye', ear: 'In the ear', rectal: 'Rectal', vaginal: 'Vaginal', other: 'Other',
}

// plain words first (for the patient), the usual abbreviation in brackets (for the pharmacist)
export const FREQUENCY_LABEL: Record<Frequency, string> = {
  once_daily: 'Once a day (OD)', twice_daily: 'Twice a day (BD)', three_times_daily: 'Three times a day (TDS)',
  four_times_daily: 'Four times a day (QID)', every_6_hours: 'Every 6 hours (Q6H)', every_8_hours: 'Every 8 hours (Q8H)',
  at_bedtime: 'At bedtime (HS)', once_weekly: 'Once a week', as_needed: 'When needed (SOS)', stat: 'Once, now (STAT)',
  other: 'Other (see instructions)',
}

const DOSES_PER_DAY: Partial<Record<Frequency, number>> = {
  once_daily: 1, twice_daily: 2, three_times_daily: 3, four_times_daily: 4, every_6_hours: 4, every_8_hours: 3,
  at_bedtime: 1, once_weekly: 1 / 7,
}

export const TIMING_LABEL: Record<Timing, string> = {
  after_food: 'After food', before_food: 'Before food', with_food: 'With food', empty_stomach: 'On an empty stomach',
  any: 'Any time',
}

export const DURATION_LABEL: Record<DurationUnit, string> = { days: 'days', weeks: 'weeks', months: 'months', ongoing: 'Ongoing' }

export function newMedicine(): Medicine {
  return {
    generic_name: '', brand_name: '', no_substitution: false, form: 'tablet', strength: '', dose: '', route: 'oral',
    frequency: 'twice_daily', timing: 'after_food', duration_value: 5, duration_unit: 'days', quantity: '', instructions: '',
  }
}

export function durationText(m: Medicine) {
  if (m.frequency === 'stat') return 'once'
  if (m.duration_unit === 'ongoing') return 'ongoing'
  if (!m.duration_value) return ''
  const unit = m.duration_value === 1 ? m.duration_unit.replace(/s$/, '') : m.duration_unit
  return `for ${m.duration_value} ${unit}`
}

// the total to dispense, when it follows from dose × doses a day × days ("15 tablets", "150 ml")
export function suggestedQuantity(m: Medicine): string | null {
  const perDay = DOSES_PER_DAY[m.frequency]
  const amount = parseFloat(m.dose)
  if (!perDay || !amount || !m.duration_value || m.duration_unit === 'ongoing') return null
  const days = m.duration_value * { days: 1, weeks: 7, months: 30, ongoing: 0 }[m.duration_unit]
  const total = Math.ceil(amount * perDay * days)
  if (['tablet', 'capsule', 'sachet'].includes(m.form)) return `${total} ${m.form}${total === 1 ? '' : 's'}`
  if (/ml/i.test(m.dose)) return `${total} ml`
  return null
}

// a medicine whose name matches a known allergy ("amoxicillin" vs "penicillin" is not caught:
// this compares names only, the doctor still checks)
export function allergyClash(m: Medicine, allergies: string): string | null {
  // old and new spellings alike: "amoxycillin" (the Indian Pharmacopoeia's) is "amoxicillin"
  const spelling = (text: string) => text.toLowerCase().replace(/ph/g, 'f').replace(/y/g, 'i').replace(/ae|oe/g, 'e')
  const names = [m.generic_name, m.brand_name].map((n) => spelling(n.trim())).filter((n) => n.length > 2)
  for (const allergy of allergies.split(/[,;\n]+/).map((a) => spelling(a.replace(/\(.*?\)/g, '').trim()))) {
    if (allergy.length > 2 && !/^(none|nil|no known|not known)/.test(allergy)
        && names.some((n) => n.includes(allergy) || allergy.includes(n))) {
      return allergy
    }
  }
  return null
}
