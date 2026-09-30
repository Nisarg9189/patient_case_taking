"""Stand-ins for Gemini Live, OpenAI, Ollama Cloud and Kafka, for load testing the backend.

Everything of ours still runs (LangGraph, the pipelining, the watchdog, the audio
handling); only the calls that leave the machine are replaced. Each fake waits as long as
the real service did in measured runs, the same way the real client does: the OpenAI and
Ollama clients are async, so the fakes await asyncio.sleep.

Settings (environment variables):
  FAKE_LATENCY_SCALE      multiply every fake latency (default 1.0)
  FAKE_GEMINI_STALL_RATE  share of Gemini sessions that go silent once the patient
                          speaks, to exercise the watchdog + fallback (default 0)
"""
import asyncio
import os
import random
import uuid
from types import SimpleNamespace

from google.genai import types

LATENCY_SCALE = float(os.getenv("FAKE_LATENCY_SCALE", "1"))
STALL_RATE = float(os.getenv("FAKE_GEMINI_STALL_RATE", "0"))

# measured on real interviews (see the ⏱ logs)
LATENCY = {
    "gemini_connect": (0.7, 1.5),        # hidden by the spare session
    "gemini_first_audio": (0.6, 1.0),    # question sent -> first audio
    "start_conversation": (1.0, 2.0),
    "plan_next_step": (2.0, 2.8),        # streamed next_step
    "plan_rest": (0.6, 1.0),             # ...then the checklist
    "extract_facts": (1.6, 2.6),
    "end_conversation": (0.3, 0.7),
    "transcribe": (1.8, 2.8),
    "kafka_send": (0.05, 0.1),
}

QUESTION_AUDIO_SECONDS = 3.0
SPEECH_LEVEL = 700            # same idea as the real VAD: RMS of a PCM16 chunk
VAD_SILENCE_SECONDS = 0.7     # Gemini's silence_duration_ms
WORDS_PER_SECOND = 2.5


def latency(name):
    low, high = LATENCY[name]
    return random.uniform(low, high) * LATENCY_SCALE


# ------------------------------------------------------------------
# the fake interview: one canned question and answer per checklist item
# ------------------------------------------------------------------

QUESTIONS = {
    "onset_or_progression": "When did it start, and is it getting better or worse?",
    "severity_rating": "On a scale from 0 to 10, how bad is it right now?",
    "associated_symptoms": "Have you noticed any other symptoms?",
    "current_medications": "Are you taking any medicines at the moment?",
    "allergies": "Do you have any allergies to medicines, foods or anything else?",
    "medical_history": "Do you have any long-term medical conditions or past surgeries?",
    "vital_signs": "Have you checked your temperature or blood pressure recently?",
    "oxygen_saturation": "Have you checked your oxygen level with an oximeter?",
    "smoking_or_alcohol": "Do you smoke, vape or drink alcohol?",
    "recent_travel_or_sick_contacts": "Have you travelled recently or been around anyone sick?",
    "family_history": "Does anyone in your family have long-term health conditions?",
    "vaccination_status": "Are your vaccinations up to date?",
}
ANSWERS = {
    None: "I have had a fever and a dry cough for three days.",
    "onset_or_progression": "It started three days ago and it is getting worse.",
    "severity_rating": "It is about a six out of ten.",
    "associated_symptoms": "I also feel tired and have a headache.",
    "current_medications": "No, I am not taking any medicines.",
    "allergies": "No, I don't have any allergies.",
    "medical_history": "No, I don't have any medical conditions.",
    "vital_signs": "My temperature was one hundred and one yesterday.",
    "oxygen_saturation": "No, I don't have an oximeter.",
    "smoking_or_alcohol": "No, I don't smoke or drink.",
    "recent_travel_or_sick_contacts": "No travel, and nobody around me is sick.",
    "family_history": "My father has diabetes.",
    "vaccination_status": "Yes, I had all my vaccines.",
}


def item_for(question):
    """Checklist item a question is about (canned, safety or first follow-up question)."""
    from patient_extraction import _question_topic

    text = (question or "").lower()
    for item, canned in QUESTIONS.items():
        if canned.lower() == text:
            return item
    if _question_topic(question):
        return _question_topic(question)
    if "scale" in text or "how bad" in text:
        return "severity_rating"
    if "brings you" in text:
        return None
    return "associated_symptoms"


# ------------------------------------------------------------------
# Gemini Live
# ------------------------------------------------------------------

def _content(**fields):
    base = dict(model_turn=None, output_transcription=None, input_transcription=None,
                turn_complete=False, generation_complete=False, interrupted=False)
    base.update(fields)
    return SimpleNamespace(voice_activity=None, server_content=SimpleNamespace(**base))


def _activity(kind):
    return SimpleNamespace(voice_activity=SimpleNamespace(voice_activity_type=kind), server_content=None)


class _FakeWebsocket:
    """Enough of a websockets connection for _tighten_keepalive()."""
    keepalive_task = SimpleNamespace(cancel=lambda: None)
    ping_interval = ping_timeout = close_timeout = None

    def start_keepalive(self):
        pass


class FakeLiveSession:
    _CLOSED = object()

    def __init__(self):
        self._ws = _FakeWebsocket()
        self._events = asyncio.Queue()
        self._question = None
        self._stalls = random.random() < STALL_RATE
        self._reset_speech()

    def _reset_speech(self):
        self._speaking = False
        self._speech_seconds = 0.0
        self._quiet_seconds = 0.0
        self._words = []
        self._words_sent = 0

    # --- the question

    async def send_client_content(self, turns, turn_complete=True):
        prompt = turns["parts"][0]["text"]
        self._question = prompt.split("Question:\n", 1)[-1].strip()
        asyncio.create_task(self._speak(self._question))

    async def _speak(self, question):
        await asyncio.sleep(latency("gemini_first_audio"))
        chunk = bytes(9600)  # 0.2 s of 24 kHz PCM16; generated faster than real time
        chunks = int(QUESTION_AUDIO_SECONDS / 0.2)
        for index in range(chunks):
            part = SimpleNamespace(inline_data=SimpleNamespace(data=chunk))
            fields = {"model_turn": SimpleNamespace(parts=[part])}
            if index == 0:
                fields["output_transcription"] = SimpleNamespace(text=question)
            self._events.put_nowait(_content(**fields))
            await asyncio.sleep(0.1)
        self._events.put_nowait(_content(turn_complete=True, generation_complete=True))

    # --- the answer

    async def send_realtime_input(self, audio):
        if self._stalls and self._speaking:
            return  # the session has gone silent (see FAKE_GEMINI_STALL_RATE)
        data = audio.data
        seconds = len(data) / 32000
        from gemini_voice_agent import speech_seconds

        if speech_seconds(data):
            self._quiet_seconds = 0.0
            if not self._speaking:
                self._speaking = True
                self._words = ANSWERS[item_for(self._question)].split()
                self._words_sent = 0
                self._events.put_nowait(_activity(types.VoiceActivityType.ACTIVITY_START))
            self._speech_seconds += seconds
            due = min(len(self._words), int(self._speech_seconds * WORDS_PER_SECOND))
            if due - self._words_sent >= 2:
                self._send_words(due)
        elif self._speaking:
            self._quiet_seconds += seconds
            if self._quiet_seconds >= VAD_SILENCE_SECONDS:
                self._send_words(len(self._words))
                self._events.put_nowait(_activity(types.VoiceActivityType.ACTIVITY_END))
                self._reset_speech()

    def _send_words(self, upto):
        text = " ".join(self._words[self._words_sent:upto])
        self._words_sent = upto
        if text:
            self._events.put_nowait(_content(input_transcription=SimpleNamespace(text=" " + text)))

    # --- receiving (like the real one: stops after each turn_complete)

    async def receive(self):
        while True:
            event = await self._events.get()
            if event is self._CLOSED:
                return
            yield event
            if event.server_content and event.server_content.turn_complete:
                return

    def close(self):
        self._events.put_nowait(self._CLOSED)


class _FakeConnect:
    def __init__(self):
        self.session = None

    async def __aenter__(self):
        await asyncio.sleep(latency("gemini_connect"))
        self.session = FakeLiveSession()
        return self.session

    async def __aexit__(self, *exc_info):
        if self.session is not None:
            self.session.close()
        return False


class FakeGeminiClient:
    def __init__(self):
        live = SimpleNamespace(connect=lambda model, config: _FakeConnect())
        self.aio = SimpleNamespace(live=live)


# ------------------------------------------------------------------
# OpenAI planning
# ------------------------------------------------------------------

_conversations = {}


async def start_conversation(session_id=None):
    await asyncio.sleep(latency("start_conversation"))
    conversation_id = f"fake_conv_{session_id or random.getrandbits(32)}_{random.getrandbits(32)}"
    _conversations[conversation_id] = set()
    return conversation_id


async def end_conversation(conversation_id):
    await asyncio.sleep(latency("end_conversation"))
    _conversations.pop(conversation_id, None)


async def plan_turn(conversation_id, patient_answer, question=None, on_next_step=None, urgent=lambda: False):
    from patient_extraction import CHECKLIST_ITEMS, NO_ANSWER

    covered = _conversations.setdefault(conversation_id, set())
    if patient_answer != NO_ANSWER:
        covered.add(item_for(question) or "chief_complaint")
    pending = [item for item in CHECKLIST_ITEMS if item not in covered]

    await asyncio.sleep(latency("plan_next_step"))
    answer_check = {"status": "answered" if patient_answer != NO_ANSWER else "not_answered", "note": None}
    if pending:
        next_step = {"action": "ask", "item": pending[0], "question": QUESTIONS[pending[0]],
                     "reason": f"{pending[0]} pending"}
    else:
        next_step = {"action": "finish", "item": None, "question": None, "reason": "all covered"}
    if on_next_step is not None:
        on_next_step({"answer_check": answer_check, "next_step": next_step})

    await asyncio.sleep(latency("plan_rest"))
    return {
        "answer_check": answer_check,
        "next_step": next_step,
        "checklist": {item: "pending" if item in pending else "covered" for item in CHECKLIST_ITEMS},
        "later_questions": [{"item": item, "question": QUESTIONS[item]} for item in pending[1:4]],
    }


# ------------------------------------------------------------------
# Ollama Cloud fact extraction
# ------------------------------------------------------------------

async def extract_facts(patient_answer, question, earlier_turns, known_symptoms, known_conditions=()):
    await asyncio.sleep(latency("extract_facts"))
    item = item_for(question)
    evidence = patient_answer
    return {
        "chief_complaint": {"text": patient_answer, "evidence": evidence} if item is None else None,
        "overall_severity": None,
        "symptoms": [],
        "vital_signs": [],
        "medical_history": [],
        "family_history": [],
        "social_history": [],
        "travel_and_contacts": [],
        "vaccinations": [],
        "regular_medications": [],
        "recent_medications": [],
        "allergies": ({"status": "none_reported", "evidence": evidence, "items": []} if item == "allergies"
                      else {"status": "not_mentioned", "evidence": None, "items": []}),
        "respiratory_information": {"cough": None, "breathing_at_rest": None, "breathing_on_exertion": None,
                                    "sputum": None, "wheeze": None, "pain_on_breathing": None,
                                    "other": None, "evidence": []},
        "important_reported_symptoms": [],
        "negative_answers": ([{"item": "current_medications", "answer": "none", "evidence": evidence}]
                             if item == "current_medications" else []),
    }


# ------------------------------------------------------------------
# OpenAI fallback transcription and Kafka
# ------------------------------------------------------------------

async def transcribe_recorded_answer(pcm):
    await asyncio.sleep(latency("transcribe"))
    return "(fallback transcript) " + ANSWERS["associated_symptoms"]


async def send_to_jev(message):
    await asyncio.sleep(latency("kafka_send"))


# ------------------------------------------------------------------

def install(backend_app_module):
    """Swap the real providers for the fakes in every module that holds a reference."""
    import gemini_voice_agent
    import patient_extraction
    import workflow

    workflow.client = FakeGeminiClient()
    for module in (patient_extraction, workflow, backend_app_module):
        for name, fake in (("start_conversation", start_conversation), ("end_conversation", end_conversation),
                           ("plan_turn", plan_turn), ("extract_facts", extract_facts)):
            if hasattr(module, name):
                setattr(module, name, fake)
    gemini_voice_agent.transcribe_recorded_answer = transcribe_recorded_answer
    gemini_voice_agent.send_to_jev = send_to_jev

    # no sign-in and no database: every virtual patient is a new anonymous patient, and no
    # fake case or audit entry is written to the real (Neon) database
    async def any_patient(websocket):
        return {"user_id": str(uuid.uuid4()), "org_id": None}

    async def not_stored(*args, **kwargs):
        return None

    # finish_interview sends each case to the real Kafka "case-summary" topic for the summary
    # worker: a fake producer keeps load-test cases out of it
    class NoKafkaProducer:
        async def start(self):
            pass

        async def send_and_wait(self, topic, value):
            await asyncio.sleep(latency("kafka_send"))

        async def stop(self):
            pass

    workflow.create_producer = NoKafkaProducer

    backend_app_module.authenticate_interview = any_patient
    backend_app_module.case_store.save_original = not_stored
    backend_app_module.db.audit = not_stored
    backend_app_module.db.audit_later = lambda *args, **kwargs: None
    print(f"🧪 Fake providers installed (latency x{LATENCY_SCALE}, Gemini stall rate {STALL_RATE:.0%})", flush=True)
