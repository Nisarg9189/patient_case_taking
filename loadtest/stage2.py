"""Stage 2 load test: virtual patients against the deployed server, real providers.

    .venv/bin/python stage2.py https://<server-address> --steps 1,3,6,10 --step-seconds 240

Each patient goes through whole interviews over wss://.../ws/interview like the browser
app: it plays each question in real time before sending playback_done, then streams its
microphone in real time, 100 ms chunks: a short pause, a recorded answer (answer_bank.py,
one Indian-English voice per patient), then background noise until the server stops
listening. Patients are added at the start of each step and keep interviewing; every
measurement is tagged with the step it happened in.

Uses asyncio + websockets rather than Locust: Locust's WebSocket client (gevent) breaks
on wss:// when one greenlet sends audio while another reads (SSL BAD_LENGTH).

Every patient costs about as much as a real interview (Gemini, Ollama Cloud; OpenAI only
if Gemini fails mid-answer) and counts against the providers' rate limits.
"""
import argparse
import asyncio
import json
import random
import statistics
import time
from collections import Counter, defaultdict

import websockets

import answer_bank

QUESTION_BYTES_PER_SECOND = 24000 * 2  # question audio: PCM16 24 kHz
THINKING_SECONDS = (0.3, 1.2)
BETWEEN_INTERVIEWS_SECONDS = (2, 5)
STAGGER_SECONDS = 2
INTERVIEW_TIMEOUT_SECONDS = 600
PROGRESS_SECONDS = 30


class Results:
    def __init__(self):
        self.rows = []
        self.step = None  # number of patients in the current step

    def add(self, metric, seconds=None, ok=True, detail=None):
        self.rows.append({"step": self.step, "metric": metric, "seconds": seconds, "ok": ok,
                          "detail": detail, "at": time.time()})
        if not ok:
            print(f"{time.strftime('%H:%M:%S')}  ✗ {metric}: {detail}", flush=True)


async def interview(url, voice, results):
    started = time.time()
    state = {"question": None, "listening": False, "speech_ended_at": None,
             "audio_at": None, "audio_bytes": 0, "first": True, "microphone": None}

    async def microphone(ws, question):
        await asyncio.sleep(random.uniform(*THINKING_SECONDS))
        speech = answer_bank.answer_for(question)["audio"][voice]
        loop = asyncio.get_running_loop()
        next_at, sent = loop.time(), 0
        while state["listening"]:
            await ws.send(speech[sent] if sent < len(speech) else answer_bank.quiet_chunk())
            sent += 1
            if sent == len(speech):
                state["speech_ended_at"] = time.time()
            next_at += 0.1
            await asyncio.sleep(max(0.0, next_at - loop.time()))

    try:
        # no keepalive pings, like a browser (its WebSocket does not send them): on a slow
        # uplink the pings queue behind the audio and the client would hang up by itself
        async with websockets.connect(url, max_size=None, open_timeout=30, ping_interval=None) as ws:
            results.add("connect", time.time() - started)
            async for message in ws:
                if isinstance(message, bytes):  # question audio
                    if state["audio_at"] is None:
                        state["audio_at"] = time.time()
                        if state["first"]:
                            results.add("first question", state["audio_at"] - started)
                            state["first"] = False
                        elif state["speech_ended_at"]:
                            results.add("patient wait", state["audio_at"] - state["speech_ended_at"])
                            state["speech_ended_at"] = None
                    state["audio_bytes"] += len(message)
                    continue

                event = json.loads(message)
                kind = event["type"]
                if kind == "question":
                    state.update(question=event["text"], audio_at=None, audio_bytes=0)
                elif kind == "question_audio_end":
                    # like the browser: wait until the question has finished playing
                    if state["audio_at"]:
                        plays_until = state["audio_at"] + state["audio_bytes"] / QUESTION_BYTES_PER_SECOND
                        await asyncio.sleep(max(0.0, plays_until - time.time()))
                    await ws.send(json.dumps({"type": "playback_done"}))
                elif kind == "listening":
                    state["listening"] = True
                    state["microphone"] = asyncio.create_task(microphone(ws, state["question"]))
                elif kind == "stopped_listening":
                    state["listening"] = False
                elif kind == "answer":
                    said = answer_bank.answer_for(event["question"])["text"]
                    ok = answer_bank.heard_correctly(said, event["text"])
                    results.add("answer heard correctly", ok=ok,
                                detail=None if ok else f"heard {event['text']!r} for {said!r}")
                elif kind == "review_case":
                    # the patient checks the finished case and presses Save (no corrections)
                    results.add("review shown", time.time() - started)
                    await ws.send(json.dumps({"type": "review_confirmed", "sections": {}}))
                elif kind == "status":
                    results.add(f"status: {event['state']}")
                elif kind == "error":
                    results.add("error event", ok=False, detail=event.get("text"))
                elif kind == "done":
                    ok = not event.get("aborted")
                    results.add("whole interview", time.time() - started, ok=ok,
                                detail=None if ok else f"aborted: {event.get('reason')}")
                    return
            results.add("whole interview", time.time() - started, ok=False, detail="server closed the connection")
    except Exception as e:
        results.add("whole interview", time.time() - started, ok=False, detail=repr(e)[:200])
    finally:
        state["listening"] = False
        if state["microphone"] is not None:
            state["microphone"].cancel()


async def patient(url, results, stop):
    voice = random.choice(answer_bank.voices())
    while not stop.is_set():
        try:
            await asyncio.wait_for(interview(url, voice, results), INTERVIEW_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            results.add("whole interview", INTERVIEW_TIMEOUT_SECONDS, ok=False, detail="timed out")
        await asyncio.sleep(random.uniform(*BETWEEN_INTERVIEWS_SECONDS))


async def progress(results, patients):
    while True:
        await asyncio.sleep(PROGRESS_SECONDS)
        rows = [r for r in results.rows if r["step"] == results.step]
        waits = [r["seconds"] for r in rows if r["metric"] == "patient wait"]
        done = [r for r in rows if r["metric"] == "whole interview"]
        wait = f"wait median {statistics.median(waits):.1f}s" if waits else "no waits yet"
        print(f"{time.strftime('%H:%M:%S')}  {len(patients)} patients | this step: {len(waits)} answers, {wait} | "
              f"interviews {sum(r['ok'] for r in done)} ok / {sum(not r['ok'] for r in done)} failed", flush=True)


def percentile(values, share):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * share))]


def summary(results):
    by_step = defaultdict(list)
    for row in results.rows:
        by_step[row["step"]].append(row)
    print(f"\n{'patients':>8} | {'wait p50':>8} {'p95':>6} {'max':>6} {'n':>4} | {'1st question p50':>16} | "
          f"{'interviews ok/failed':>20} | {'answers heard right':>19} | statuses")
    for step in sorted(by_step):
        rows = by_step[step]
        waits = [r["seconds"] for r in rows if r["metric"] == "patient wait"]
        firsts = [r["seconds"] for r in rows if r["metric"] == "first question"]
        done = [r for r in rows if r["metric"] == "whole interview"]
        heard = [r for r in rows if r["metric"] == "answer heard correctly"]
        statuses = Counter(r["metric"][8:] for r in rows if r["metric"].startswith("status: "))
        wait_text = (f"{percentile(waits, .5):7.1f}s {percentile(waits, .95):5.1f}s {max(waits):5.1f}s {len(waits):>4}"
                     if waits else f"{'-':>8} {'-':>6} {'-':>6} {0:>4}")
        first_text = f"{percentile(firsts, .5):.1f}s" if firsts else "-"
        heard_text = f"{sum(r['ok'] for r in heard)}/{len(heard)}" if heard else "-"
        interviews_text = f"{sum(r['ok'] for r in done):>9} / {sum(not r['ok'] for r in done):<8}"
        print(f"{step:>8} | {wait_text} | {first_text:>16} | {interviews_text} | {heard_text:>19} | "
              f"{dict(statuses) or '-'}")
    problems = Counter(f"[{r['step']}] {r['metric']}: {r['detail']}" for r in results.rows if not r["ok"])
    if problems:
        print("\nproblems:")
        for text, count in problems.most_common(15):
            print(f"  {count}x {text}")


async def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("server", help="https://<server-address>")
    parser.add_argument("--steps", default="1,3,6,10", help="patients at each step, e.g. 1,3,6,10")
    parser.add_argument("--step-seconds", type=float, default=240)
    parser.add_argument("--out", help="write every measurement to this JSON file")
    args = parser.parse_args()

    url = args.server.replace("http", "ws", 1).rstrip("/") + "/ws/interview"
    steps = [int(n) for n in args.steps.split(",")]
    answer_bank.answers()  # load the recordings before any patient starts

    results, stop, patients = Results(), asyncio.Event(), []
    reporter = asyncio.create_task(progress(results, patients))
    try:
        for users in steps:
            results.step = users
            step_started = time.time()
            print(f"{time.strftime('%H:%M:%S')}  step: {users} patients", flush=True)
            while len(patients) < users:
                patients.append(asyncio.create_task(patient(url, results, stop)))
                await asyncio.sleep(STAGGER_SECONDS)
            await asyncio.sleep(max(0.0, args.step_seconds - (time.time() - step_started)))
    finally:
        stop.set()
        print(f"{time.strftime('%H:%M:%S')}  finishing the interviews in progress...", flush=True)
        await asyncio.wait(patients, timeout=INTERVIEW_TIMEOUT_SECONDS)
        reporter.cancel()
        summary(results)
        if args.out:
            with open(args.out, "w") as handle:
                json.dump(results.rows, handle, indent=1)


if __name__ == "__main__":
    asyncio.run(main())
