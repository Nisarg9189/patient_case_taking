# Patient case taking

A spoken intake interview: the patient answers questions out loud in the browser, and the
answers are turned into a structured case record (complaint, symptoms, medicines,
allergies, history, vitals, …) for a clinician.

- **Voice**: Gemini Live asks each question and transcribes the answer (Indian English).
  If Gemini stops responding mid-answer, the answer recorded on the server is transcribed
  with OpenAI `gpt-4o-transcribe` instead.
- **Planning** (which question next, is the checklist covered): Gemini
  `gemini-3.8-flash`, streamed so the next question is used before the plan is finished.
- **Fact extraction** (the case record): Gemini `gemini-3.8-flash` with a strict Pydantic
  schema, in the background.
- **Workflow**: LangGraph; code-level safety checks for allergies and current medicines.

## Layout

| Folder | What |
|---|---|
| `patient-nlp/` | The interview: workflow, voice agent, planning and extraction |
| `backend/` | FastAPI server: one interview per WebSocket at `/ws/interview` |
| `frontend/` | React + Vite page (microphone, question audio, live case summary) |
| `deploy/` | Docker + Caddy (HTTPS) deployment to one EC2 server — see its README |
| `loadtest/` | Load tests: stage 1 with fake providers (Locust), stage 2 against the deployed server with real providers — see its README |

## Run locally

API keys go in `patient-nlp/.env` (not in the repository): `GEMINI_API_KEY`,
`OPENAI_API_KEY`, `DATABASE_URL` (the Neon Postgres connection string; finished cases,
the patients' edits and the summaries are stored there), the Kafka/Redis settings used by
the optional JEV worker, and
`OLLAMA_API_KEY` only if planning is switched back to Ollama (`PLANNING_PROVIDER=ollama`).

Sign-in is Neon Auth (email + password): `NEON_AUTH_URL` (the project's Neon Auth URL) and
`PLATFORM_ADMIN_EMAILS` (comma-separated) in `patient-nlp/.env`, and the same URL as
`VITE_NEON_AUTH_URL` in `frontend/.env.local`. New accounts are patients of the default
clinic; clinic admins and platform admins give staff roles (doctor, nurse, front desk,
clinic admin) on the Clinic members page.

Appointments: on the Clinic schedule page a clinic admin sets the opening hours, the slot
length, how many patients each doctor takes per slot, how far ahead patients may book, and
each doctor's weekly hours and days off. After the interview the patient picks a hospital,
a doctor (or any), a day and a time; that hospital's doctors and nurses can then open the
interview. Staff see each day's visits on the Appointments page. A doctor can refer a visit
there to another doctor at any hospital (a free time with that doctor, and a note for them):
the receiving hospital gets the patient's interview and the visit.

Prescriptions: on a patient's case a doctor writes a prescription (generic names, strength,
dose, route, frequency, timing, duration, total quantity, investigations, advice, follow-up)
and signs it; it carries the doctor's qualification and registration number and cannot change
after signing (it can be voided). The patient sees and prints their signed prescriptions.
Medicines are searched in the Jan Aushadhi (PMBJP) list of 2110 generic medicines
(frontend/public/data/medicines.json; to update it, see tools/medicines/build.py). Clinic
admins set the clinic's address, phone, email and registration number (Clinic schedule page);
they are printed on its prescriptions. A patient's date of birth, sex, weight, phone, address
and ABHA number are saved (by the patient, or from the prescriptions doctors sign) and fill in
each new prescription.

```bash
cd patient-nlp && python -m venv .venv && .venv/bin/pip install -r requirements.txt -r ../backend/requirements.txt
cd ../backend && ../patient-nlp/.venv/bin/uvicorn app:app --port 8000
cd ../frontend && npm install && npm run dev   # http://localhost:5173
```
