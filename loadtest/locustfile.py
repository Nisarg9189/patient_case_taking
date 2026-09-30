"""Virtual patients for load testing the interview backend.

Each Locust user is one patient going through a whole interview over /ws/interview,
behaving like the browser app: it plays each question in real time before sending
playback_done, then streams microphone audio (PCM16 16 kHz, 100 ms chunks, in real
time): a short pause, speech, then quiet until the server stops listening.

The "speech" is loud noise, not words: the fake Gemini only needs to hear that someone
is talking (it transcribes a canned answer), and the real backend's cost depends on how
much audio arrives, not what it says. This is stage 1 (fake providers); stage 2 against
the deployed server with real providers is stage2.py (Locust's WebSocket client breaks
on wss:// when one greenlet sends audio while another reads).

Reported in Locust (type "interview"):
  patient wait          end of the patient's speech -> first audio of the next question
  first question        connected -> first audio of the opening question
  whole interview       connected -> done
  failures              aborted interviews, error events, dropped connections, timeouts
Counted as events (response time 0): "status: reconnecting/recording/transcribing".

Stepped load (optional): STEPS="10,20,40" STEP_SECONDS=150 holds each number of patients
for STEP_SECONDS, adding patients at SPAWN_RATE per second (default 1), and tags every
result with its step ("patient wait [20 users]") so the levels can be compared.
"""
import json
import os
import random
import time

import gevent
import numpy as np
import websocket
from locust import LoadTestShape, User, between, events, task

CHUNK_SECONDS = 0.1
SAMPLE_RATE = 16000
QUESTION_BYTES_PER_SECOND = 24000 * 2
SPEECH_SECONDS = (1.5, 4.0)
THINKING_SECONDS = (0.3, 1.2)
INTERVIEW_TIMEOUT_SECONDS = 600

_rng = np.random.default_rng()


def _chunk(level):
    samples = _rng.normal(0, level, int(SAMPLE_RATE * CHUNK_SECONDS)) if level else np.zeros(int(SAMPLE_RATE * CHUNK_SECONDS))
    return np.clip(samples, -32768, 32767).astype("<i2").tobytes()


SPEECH_CHUNK = _chunk(3000)    # RMS ~3000, like normal speech
QUIET_CHUNK = _chunk(100)      # background noise


STEPS = [int(n) for n in os.getenv("STEPS", "").split(",") if n.strip()]
STEP_SECONDS = float(os.getenv("STEP_SECONDS", "150"))
SPAWN_RATE = float(os.getenv("SPAWN_RATE", "1"))
_current_step = {"users": None}


class VirtualPatient(User):
    wait_time = between(2, 5)  # before starting the next interview

    def on_start(self):
        self.ws_url = self.host.replace("http", "ws", 1).rstrip("/") + "/ws/interview"

    def report(self, name, started_at=None, exception=None, value_ms=None):
        if _current_step["users"] is not None:
            name = f"{name} [{_current_step['users']} users]"
        events.request.fire(
            request_type="interview",
            name=name,
            response_time=value_ms if value_ms is not None else (time.time() - started_at) * 1000,
            response_length=0,
            exception=exception,
            context={},
        )

    @task
    def interview(self):
        connected_at = time.time()
        try:
            ws = websocket.create_connection(self.ws_url, timeout=30)
        except Exception as e:
            self.report("connect", connected_at, exception=e)
            return
        self.report("connect", connected_at)

        state = {
            "speaker": None,          # greenlet streaming the microphone
            "listening": False,
            "speech_ended_at": None,  # end of the last answer's speech
            "question_audio_at": None,
            "question_bytes": 0,
            "first_question": True,
        }

        def microphone():
            gevent.sleep(random.uniform(*THINKING_SECONDS))
            speech_chunks = int(random.uniform(*SPEECH_SECONDS) / CHUNK_SECONDS)
            next_at = time.time()
            sent = 0
            while state["listening"]:
                chunk = SPEECH_CHUNK if sent < speech_chunks else QUIET_CHUNK
                ws.send(chunk, opcode=websocket.ABNF.OPCODE_BINARY)
                sent += 1
                if sent == speech_chunks:
                    state["speech_ended_at"] = time.time()
                next_at += CHUNK_SECONDS
                gevent.sleep(max(0, next_at - time.time()))

        try:
            ws.settimeout(INTERVIEW_TIMEOUT_SECONDS)
            while True:
                opcode, data = ws.recv_data()
                if opcode == websocket.ABNF.OPCODE_BINARY:
                    if state["question_audio_at"] is None:
                        state["question_audio_at"] = time.time()
                        if state["first_question"]:
                            self.report("first question", connected_at)
                            state["first_question"] = False
                        elif state["speech_ended_at"]:
                            self.report("patient wait", state["speech_ended_at"])
                            state["speech_ended_at"] = None
                    state["question_bytes"] += len(data)
                    continue
                if opcode == websocket.ABNF.OPCODE_CLOSE:
                    self.report("whole interview", connected_at, exception=ConnectionError("server closed the socket"))
                    return

                event = json.loads(data)
                kind = event["type"]
                if kind == "question":
                    state["question_audio_at"], state["question_bytes"] = None, 0
                elif kind == "question_audio_end":
                    # like the browser: all audio has arrived; wait until it has finished playing
                    if state["question_audio_at"]:
                        plays_until = state["question_audio_at"] + state["question_bytes"] / QUESTION_BYTES_PER_SECOND
                        gevent.sleep(max(0, plays_until - time.time()))
                    ws.send(json.dumps({"type": "playback_done"}))
                elif kind == "listening":
                    state["listening"] = True
                    state["speaker"] = gevent.spawn(microphone)
                elif kind == "stopped_listening":
                    state["listening"] = False
                    if state["speaker"] is not None:
                        state["speaker"].join(timeout=1)
                elif kind == "review_case":
                    # the patient checks the finished case and presses Save (no corrections)
                    ws.send(json.dumps({"type": "review_confirmed", "sections": {}}))
                elif kind == "status":
                    self.report(f"status: {event['state']}", value_ms=0)
                elif kind == "error":
                    self.report("error event", value_ms=0, exception=RuntimeError(event.get("text")))
                elif kind == "done":
                    if event.get("aborted"):
                        self.report("whole interview", connected_at,
                                    exception=RuntimeError(f"aborted: {event.get('reason')}"))
                    else:
                        self.report("whole interview", connected_at)
                    return
        except Exception as e:
            self.report("whole interview", connected_at, exception=e)
        finally:
            state["listening"] = False
            if state["speaker"] is not None:
                state["speaker"].kill(block=False)
            try:
                ws.close()
            except Exception:
                pass


if STEPS:
    class StepLoad(LoadTestShape):
        """Hold each entry of STEPS for STEP_SECONDS, then stop."""

        def tick(self):
            step = int(self.get_run_time() // STEP_SECONDS)
            if step >= len(STEPS):
                return None
            _current_step["users"] = STEPS[step]
            return STEPS[step], SPAWN_RATE
