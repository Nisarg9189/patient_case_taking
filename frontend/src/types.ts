// Shapes of the case record built by patient-nlp/patient_extraction.py (merge_into_case).

export type Evidence = string | string[]

export interface Symptom {
  name: string
  status: 'present' | 'absent' | 'uncertain'
  location: string | null
  character: string | null
  severity: string | null
  duration: string | null
  onset: string | null
  frequency: string | null
  triggers: string[]
  relieving_factors: string[]
}

export interface Medication {
  name: string
  dose: string | null
  frequency: string | null
  when?: string | null
}

export interface CaseRecord {
  chief_complaint: { text: string } | null
  overall_severity: { text: string } | null
  symptoms: Symptom[]
  vital_signs: { name: string; value: string; unit: string | null; approximate: boolean; measured_when: string | null }[]
  medical_history: { condition: string; duration?: string | null }[]
  family_history: { relative: string; condition: string }[]
  social_history: { topic: string; detail: string }[]
  travel_and_contacts: { type: string; detail: string }[]
  vaccinations: { vaccine: string; detail: string }[]
  regular_medications: Medication[]
  recent_medications: Medication[]
  allergies: { status: 'reported' | 'none_reported' | 'not_mentioned'; items: { substance: string; reaction: string | null }[] }
  important_reported_symptoms: { symptom: string; reason: string }[]
  negative_answers: { item: string; answer: string }[]
  asked_without_answer: string[]
}

export type TopicStatus = 'covered' | 'unknown_or_declined' | 'not_relevant' | 'pending'
export type Checklist = Record<string, TopicStatus>

export type ServerEvent =
  | { type: 'question'; text: string }
  | { type: 'question_audio_end' }
  | { type: 'listening' }
  | { type: 'stopped_listening' }
  | { type: 'transcript'; text: string }
  | { type: 'answer'; question: string; text: string }
  | { type: 'case'; case: CaseRecord; checklist: Checklist; remaining: number }
  | { type: 'done'; case: CaseRecord | null; checklist: Checklist; aborted: boolean; reason: string | null }
  | { type: 'error'; text: string }
  | { type: 'status'; state: ConnectionState; text: string }

// reconnecting: the question is asked again on a new voice connection
// recording / transcribing: the voice connection dropped mid-answer; the answer is still
// recorded and transcribed separately, so the patient does not have to repeat it
export type ConnectionState = 'reconnecting' | 'recording' | 'transcribing'
