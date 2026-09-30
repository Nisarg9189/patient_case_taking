# Load tests

## Stage 1 – fake providers

Finds how many simultaneous interviews one backend process can handle, without calling
Gemini, OpenAI, Ollama Cloud or Kafka (no cost, no quota).

- `fake_backend.py` runs the real `backend/app.py` with `fake_providers.py` swapped in
  for the external services. Every fake waits as long as the real service did in measured
  runs, awaiting like the real (async) clients.
  It also prints server stats every 5 s (`📊` lines, and `server_stats.csv`):
  open interviews, event-loop lag, worker threads (and how many calls are queued for
  one), CPU and memory.
- `locustfile.py` is the virtual patient: one Locust user = one patient going through a
  whole interview over `/ws/interview`, playing questions and streaming microphone audio
  in real time like the browser app.

## Setup (once)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

## Run

1. Fake backend on port 8001 (the real one can stay on 8000):

   ```bash
   ../patient-nlp/.venv/bin/uvicorn fake_backend:app --port 8001 --log-level warning
   ```

2. Virtual patients, either with the web UI (http://localhost:8089, choose the number of
   users and the spawn rate there):

   ```bash
   .venv/bin/locust -f locustfile.py VirtualPatient --host http://localhost:8001
   ```

   or as fixed steps, each result tagged with its step (`patient wait [20 users]`):

   ```bash
   mkdir -p results && STEPS="10,20,30,40,60,80" STEP_SECONDS=150 .venv/bin/locust -f locustfile.py \
     VirtualPatient --host http://localhost:8001 --headless --only-summary --csv results/step
   ```

## What to look at

| Result | Healthy | Means |
|---|---|---|
| `patient wait` | ~3 s, flat as patients are added | end of answer → next question audio |
| `whole interview` failures | 0 | aborted / dropped interviews |
| `queued` (server stats) | 0 | calls waiting for a free worker thread |
| `loop_lag_p99_ms` | < 50 ms | the event loop is keeping up with audio |
| `cpu_percent` | < ~80 % of one core | the backend is one Python process: one core |

The first of these to go wrong, and at how many patients, is the limit of one process.

## Knobs

- `FAKE_LATENCY_SCALE=2` (backend): every external call twice as slow, e.g. a bad day at a provider.
- `FAKE_GEMINI_STALL_RATE=0.2` (backend): 20 % of Gemini sessions go silent mid-answer,
  to load the watchdog + fallback transcription path.
- `SPAWN_RATE` (Locust, stepped mode): patients added per second (default 1).

## Stage 2 – real providers, deployed server

Real Gemini (and, only if Gemini fails mid-answer, OpenAI transcription): every virtual
patient costs about as much as a real interview and counts against the providers' rate
limits, so ramp up slowly.

`stage2.py` runs the virtual patients (asyncio + websockets; Locust's WebSocket client
breaks on wss://). Each patient speaks recorded answers (`answers/`, macOS voice Rishi,
made by `make_answers.py`) and the run reports, per step: patient wait, time to the first
question, interviews completed or failed, and whether the server heard each answer right.

Run it from a machine with a good uplink, e.g. the server itself (a slow uplink delivers
the patients' audio in bursts and measures the network, not the app):

```bash
rsync -az --include "answers/***" --include "answer_bank.py" --include "stage2.py" --exclude "*" \
  -e "ssh -i ~/.ssh/<key>.pem" ./ ubuntu@<server-ip>:~/loadtest/
ssh -i ~/.ssh/<key>.pem ubuntu@<server-ip> 'sudo docker run --rm --network host -v ~/loadtest:/lt -w /lt \
  python:3.14-slim sh -c "pip install -q websockets==16.1.1 numpy==2.5.3 && \
  python -u stage2.py https://<server-address> --steps 5,10,20 --step-seconds 180"'
```

Watch the server (CPU, memory, planning queue and retries, failed extractions, fallbacks,
rate limits) in another terminal:

```bash
./watch_server.sh ubuntu@<server-ip> ~/.ssh/<key>.pem
```
