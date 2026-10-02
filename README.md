# Patient case taking

A spoken intake interview: the patient talks with a voice assistant in the browser, in the
language they pick (English, Hindi, Gujarati or Marathi), and their answers become a case
summary for a clinician.

- **Voice**: an Azure AI Foundry voice agent (speech recognition, the model, and the voice are
  set in Foundry). The browser talks to `/ws/voice-agent`; the server connects to the agent with
  its own Azure identity, so the browser never sees Azure.
- **Tools**: the agent saves the interview through an MCP server (`patient-nlp/mcp_server.py`,
  served by the app at `/mcp-server/mcp`, protected by `MCP_SECRET`): `record_answer`,
  `flag_urgent`, `get_interview_checklist`, `get_current_case_record`, `finish_interview`.
  The interview lives in Redis while it runs; `finish_interview` stores it in Neon and sends
  it to Kafka.
- **Summary**: `patient-nlp/summary_agent.py` (Gemini `gemini-3.8-flash`, strict schema) turns
  the stored interview into the clinician summary; the app runs it when `RUN_SUMMARY_WORKER=1`.
- **Safety**: `finish_interview` refuses until allergies and current medicines have been
  asked, unless a red flag was raised.

## Layout

| Folder | What |
|---|---|
| `patient-nlp/` | The MCP tools the voice agent calls, the summary agent, and the database/Kafka/Redis code |
| `backend/` | FastAPI server: the voice agent interview at `/ws/voice-agent`, the MCP server, cases, appointments, prescriptions |
| `frontend/` | React + Vite page (language picker, microphone, the agent's voice, booking) |
| `deploy/` | Docker + Caddy (HTTPS) deployment to one EC2 server, and Azure Container Apps — see their READMEs |
| `loadtest/` | Load tests of the earlier question-by-question interview (Gemini Live); they no longer run against this server |

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
