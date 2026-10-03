// Shapes of the hospital API (backend/hospitals.py).

export interface Location {
  state: string
  districts: string[]
}

export interface HospitalSummary {
  org_id: string
  name: string
  state: string
  district: string
  address: string
  consultation_fee: number | null // rupees
  doctor_count: number
  specialties: string[]
  rating: number | null // average of 1-5, none until someone rates it
  rating_count: number
}

export interface DoctorInfo {
  doctor_id: string
  name: string
  qualification: string
  specialty: string
}

export interface HospitalDetail extends HospitalSummary {
  phone: string
  email: string
  registration_number: string
  description: string
  doctors: DoctorInfo[]
  reviews: { rating: number; comment: string; at: string }[]
  my_rating: { rating: number; comment: string } | null
  can_rate: boolean // the patient has a visit booked there
}

// the hospital and doctor a patient chose: the interview goes to them and the booking is with them
export interface Target {
  orgId: string
  hospital: string
  doctorId: string
  doctor: string
  documentIds?: string[] // the past documents the patient chose to share with this doctor
}

export function rupees(fee: number | null) {
  return fee === null ? null : fee === 0 ? 'Free' : `₹${fee.toLocaleString('en-IN')}`
}
