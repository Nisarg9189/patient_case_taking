"""Record the spoken answers RealPatient uses (stage 2, real providers), with macOS `say`.

    .venv/bin/python make_answers.py

Writes answers/<voice>_<n>.wav (PCM16 mono 16 kHz, like the browser sends) and
answers/answers.json. Each answer has the question keywords it replies to; the first
matching entry wins, so the order matters ("conditions that run in your family" must
reach the family answer before the conditions one).
"""
import json
import subprocess
from pathlib import Path

# Indian English, like the patients (en-IN). Only Rishi: the Aman and Tara voices garble
# several of these sentences (OpenAI's transcriber hears the same nonsense), which made
# the stage-2 "answer heard correctly" numbers and Gemini-stall fallbacks meaningless.
VOICES = ["Rishi"]

# (keywords, answer); the same set used in the end-to-end tests
ANSWERS = [
    (["brings you", "today"], "I have had a fever for three days and a dry cough."),
    (["scale", "severe", "how bad"], "It is about a five out of ten."),
    (["family", "parents", "siblings"], "No, nobody in my family has any illness."),
    (["allerg"], "Yes, I am allergic to penicillin, it gives me a rash."),
    (["condition", "surger", "hospital", "history"], "No, I don't have any medical conditions."),
    (["medic", "medicine", "prescription"], "No, I am not taking any medicines."),
    (["started", "start", "worse", "better", "begin"], "It started three days ago and it is getting a bit worse."),
    (["other symptom", "any other", "anything else", "along with"], "I also feel very tired."),
    (["oxygen", "oximeter"], "No, I don't have an oximeter at home."),
    # the Aman voice mangles the word "temperature" (even OpenAI's transcriber heard
    # "postappi acting mukha phone"), so the reading is said without it
    (["temperature", "blood pressure", "measured", "pulse"], "Yesterday my fever was one hundred and two."),
    (["smok", "alcohol", "vape"], "No, I don't smoke and I don't drink."),
    (["travel", "sick", "contact"], "No, I have not travelled and nobody around me is sick."),
    (["vaccin", "shot"], "Yes, I had the flu shot last year."),
    ([], "I'm not sure."),  # anything else
]

OUT = Path(__file__).resolve().parent / "answers"


def main():
    OUT.mkdir(exist_ok=True)
    manifest = []
    for n, (keywords, text) in enumerate(ANSWERS):
        files = {}
        for voice in VOICES:
            aiff, wav = OUT / f"{voice}_{n}.aiff", OUT / f"{voice}_{n}.wav"
            subprocess.run(["say", "-v", voice, "-o", str(aiff), text], check=True)
            subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(wav)], check=True)
            aiff.unlink()
            files[voice] = wav.name
        manifest.append({"keywords": keywords, "text": text, "files": files})
    (OUT / "answers.json").write_text(json.dumps(manifest, indent=1))
    print(f"{len(ANSWERS)} answers x {len(VOICES)} voices -> {OUT}")


if __name__ == "__main__":
    main()
