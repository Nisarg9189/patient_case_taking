# Patient intake – frontend

React + Vite app for the spoken patient interview. It talks to `../backend/app.py`
over a WebSocket: the browser streams the patient's microphone while the backend is
listening, and plays the question audio Gemini produces.

## Run

1. Backend (port 8000), using the `patient-nlp` virtualenv:

   ```bash
   cd ../backend && ../patient-nlp/.venv/bin/uvicorn app:app --port 8000
   ```

2. Frontend (port 5173); the dev server forwards `/ws` and `/api` to the backend:

   ```bash
   npm install
   npm run dev
   ```

3. Open http://localhost:5173 and allow microphone access.

`jev_wroker.py` is optional: JEV's relevance check is advisory and the interview does
not wait for it.

## Build

```bash
npm run build
```
