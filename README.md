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
`OPENAI_API_KEY`, the Kafka/Redis settings used by the optional JEV worker, and
`OLLAMA_API_KEY` only if planning is switched back to Ollama (`PLANNING_PROVIDER=ollama`).

```bash
cd patient-nlp && python -m venv .venv && .venv/bin/pip install -r requirements.txt -r ../backend/requirements.txt
cd ../backend && ../patient-nlp/.venv/bin/uvicorn app:app --port 8000
cd ../frontend && npm install && npm run dev   # http://localhost:5173
```
