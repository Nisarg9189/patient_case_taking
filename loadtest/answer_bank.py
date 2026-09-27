"""The recorded answers the stage-2 patients speak (made by make_answers.py).

answer_for(question) picks the answer by keywords; audio(answer, voice) gives it as 100 ms
PCM16 16 kHz chunks with light background noise, like a browser microphone;
heard_correctly() checks what the server transcribed against what was said.
"""
import json
import re
import wave
from pathlib import Path

import numpy as np

ANSWERS_DIR = Path(__file__).resolve().parent / "answers"
SAMPLE_RATE = 16000
CHUNK_SAMPLES = SAMPLE_RATE // 10

_rng = np.random.default_rng()
_answers = []  # answers.json entries, with "audio": {voice: [chunks]}


def _chunks(path):
    with wave.open(str(path)) as recording:
        samples = np.frombuffer(recording.readframes(recording.getnframes()), dtype="<i2")
    noisy = np.clip(samples + _rng.normal(0, 100, len(samples)), -32768, 32767).astype("<i2")
    return [noisy[i:i + CHUNK_SAMPLES].tobytes() for i in range(0, len(noisy), CHUNK_SAMPLES)]


def answers():
    if not _answers:
        for entry in json.loads((ANSWERS_DIR / "answers.json").read_text()):
            entry["audio"] = {voice: _chunks(ANSWERS_DIR / name) for voice, name in entry["files"].items()}
            _answers.append(entry)
    return _answers


def voices():
    return list(answers()[0]["audio"])


def answer_for(question):
    text = (question or "").lower()
    return next(e for e in answers() if not e["keywords"] or any(k in text for k in e["keywords"]))


def quiet_chunk():
    return np.clip(_rng.normal(0, 100, CHUNK_SAMPLES), -32768, 32767).astype("<i2").tobytes()


# numbers are left out: "one hundred and two" may come back as "102"
_NUMBERS = {"zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
            "hundred", "and"}


def _words(text):
    return {w for w in re.findall(r"[a-z']+", text.lower()) if w not in _NUMBERS}


def heard_correctly(said, heard, share=0.6):
    """At least `share` of the words said (numbers aside) are in what was heard."""
    expected = _words(said)
    return len(expected & _words(heard)) / max(1, len(expected)) >= share
