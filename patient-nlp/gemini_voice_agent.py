import array
import asyncio
import io
import math
import re
import uuid
import wave

from google import genai
from google.genai import types

from audio_io import audio_for
from latency_log import now, log_step, log_since


# ============================================================
# CONFIGURATION
# ============================================================

PATIENT_TIMEOUT = 300  # 5 minutes

# When the patient's answer counts as finished. Gemini's turn_complete only arrives after it
# has generated its own (unused) spoken reply: 3.4-4.9s after the patient stops, and
# sometimes never. Gemini also sends voice_activity ACTIVITY_START / ACTIVITY_END; the end
# arrives with the last words (~1s after the patient stops) whether or not Gemini replies,
# and a new start arrives if the patient carries on after a pause.
ANSWER_PAUSE_SECONDS = 1.5      # after speech ended, wait this long for the patient to go on
ANSWER_QUIET_SECONDS = 1.5      # backup: Gemini is replying and no new words for this long
ANSWER_SILENCE_SECONDS = 5.0    # backup: no new words, speech not in progress
ANSWER_MAX_QUIET_SECONDS = 8.0  # no new words at all, even if speech detection looks stuck

# All the answer timings above and below are counted in seconds of patient audio RECEIVED,
# not clock time: if the patient's audio stops arriving (their network stalls), every
# timer pauses instead of ending the answer or blaming Gemini (seen on the deployed server:
# a 6s stall cut "No, I don't smoke and I don't drink" to "No, I don't"). When the delayed
# audio arrives, Gemini hears it normally. Only if no audio arrives at all for this long is
# the answer finished with what was heard (a closed browser ends the interview anyway).
AUDIO_STOPPED_SECONDS = 20.0

# Watchdog: a Gemini connection can go silent without closing (seen: the patient answered,
# nothing came back, and the dead link was only noticed ~58s later). If the microphone
# picked up speech and Gemini has sent nothing since, the session is treated as dead and
# the question is asked again on a new one.
SPEECH_LEVEL = 700                  # RMS of a PCM16 chunk that counts as speech, not background
SPEECH_SECONDS_BEFORE_WATCHDOG = 0.5  # this much speech with no reply arms the watchdog
GEMINI_SILENT_SECONDS = 6.0         # ...and this long after the speech started, give up
QUESTION_SILENT_SECONDS = 10.0      # no audio or text from Gemini while it should be asking

# The websocket's own dead-link check: ping every 5s, give up after 5s without a pong
# (the websockets default is 20s + 20s, so a dead link took up to 40s to notice).
KEEPALIVE_PING_INTERVAL_SECONDS = 5
KEEPALIVE_PING_TIMEOUT_SECONDS = 5
KEEPALIVE_CLOSE_TIMEOUT_SECONDS = 2  # after a missed pong, how long to wait for the close (default 10s)


# When Gemini fails while the patient is answering, the answer is not lost: the audio of
# the whole answer is kept here, the end of the answer is detected locally, and the audio is
# transcribed by OpenAI instead. gpt-4o-transcribe with no prompt: 22/24 on the Indian-English
# test voices (misses only where another voice talks first); with a word-list prompt it
# wrote the prompt's words into an answer, and a question-style prompt turned an answer
# into a question, so no prompt is given.
FALLBACK_TRANSCRIBE_MODEL = "gpt-4o-transcribe"
FALLBACK_TRANSCRIBE_TIMEOUT_SECONDS = 20
LOCAL_ANSWER_PAUSE_SECONDS = 2.2   # ~ Gemini's 700 ms speech end + ANSWER_PAUSE_SECONDS
MAX_RECORDED_ANSWER_SECONDS = 180

_transcriber = None


async def transcribe_recorded_answer(pcm):
    """Transcribe PCM16 mono 16 kHz audio with OpenAI."""
    global _transcriber
    if _transcriber is None:
        from openai import AsyncOpenAI
        _transcriber = AsyncOpenAI(timeout=FALLBACK_TRANSCRIBE_TIMEOUT_SECONDS, max_retries=1)

    wav = io.BytesIO()
    with wave.open(wav, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(pcm)

    result = await _transcriber.audio.transcriptions.create(
        model=FALLBACK_TRANSCRIBE_MODEL,
        file=("answer.wav", wav.getvalue(), "audio/wav"),
        language="en",
    )
    return result.text.strip()


class GeminiNotResponding(Exception):
    """The Gemini session stopped responding; the question is asked again on a new session."""


# ============================================================
# GEMINI CONFIG
# ============================================================

# The session-level instruction is what stops Gemini from adding a "this is not medical
# advice" disclaimer to questions about readings (tested: 0/12 with it, 1-2 of 6 without).
SPEAKER_INSTRUCTION = (
    "You are the voice of a patient intake form. Every message contains one question. "
    "Speak exactly that question, word for word, then stop. Never add anything else: no "
    "greeting, no explanation, no disclaimer, no advice. A clinician reviews the answers separately."
)

# Speech recognition hints for the patient's answers: Indian English, and words the
# transcriber got wrong in real interviews ("rush" for rash, "not major" for measured,
# "no van a" for nobody). On Indian-English test voices, en-IN + vocabulary scored
# best (85% word accuracy vs 78% with the defaults).
PATIENT_LANGUAGE_CODES = ["en-IN"]
MEDICAL_VOCABULARY = [
    # symptoms
    "fever", "cough", "dry cough", "rash", "itching", "breathless", "breathlessness", "wheezing",
    "chest pain", "headache", "dizziness", "nausea", "vomiting", "diarrhoea", "fatigue", "sputum", "phlegm",
    # measurements
    "measured", "temperature", "blood pressure", "blood sugar", "pulse", "oximeter", "pulse oximeter",
    "oxygen level",
    # conditions
    "diabetes", "asthma", "hypertension", "thyroid", "cholesterol", "tuberculosis",
    # medicines and allergies
    "metformin", "insulin", "paracetamol", "ibuprofen", "amoxicillin", "azithromycin", "penicillin",
    "aspirin", "inhaler", "antibiotics", "milligrams", "allergic", "allergy",
    # other interview words
    "vaccination", "vaccine", "booster", "flu shot", "surgery", "nobody",
]

LIVE_CONFIG = types.LiveConnectConfig(
    response_modalities=["AUDIO"],

    system_instruction=SPEAKER_INSTRUCTION,

    # We need this to receive patient's speech transcription
    input_audio_transcription=types.AudioTranscriptionConfig(
        language_codes=PATIENT_LANGUAGE_CODES,
        custom_vocabulary=MEDICAL_VOCABULARY,
    ),

    # We want transcription of Gemini's spoken question
    output_audio_transcription=types.AudioTranscriptionConfig(),

    # Gemini automatically detects when patient starts/stops speaking
    realtime_input_config=types.RealtimeInputConfig(
        automatic_activity_detection=types.AutomaticActivityDetection(
            disabled=False,
            silence_duration_ms=700,
        )
    ),
)


# ============================================================
# SHUTDOWN HELPERS
# ============================================================
# Stopping audio can hang (a stuck audio driver, or a websocket send that does not
# react to cancellation). Every step is time-limited so the answer still reaches Kafka.

STOP_TIMEOUT_SECONDS = 3


# Work that must finish but that the patient should not wait for (closing the Gemini
# session took 7-9s on some turns). References are kept so tasks are not garbage collected.
BACKGROUND_TIMEOUT_SECONDS = 30
_background_tasks = set()


def run_in_background(coro, name, turn_id=None):
    async def runner():
        started_at = now()
        try:
            await asyncio.wait_for(coro, timeout=BACKGROUND_TIMEOUT_SECONDS)
        except Exception as e:
            print(f"\n⚠️ {name} failed in background: {e!r}", flush=True)
            return
        if turn_id:
            log_step(turn_id, f"{name} (background)", now() - started_at)

    task = asyncio.create_task(runner())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


# ============================================================
# SPARE GEMINI SESSION (connected before it is needed)
# ============================================================
# Connecting took 0.7-4s per question. While one question is being asked, the next
# question's session is already connecting in the background.

SPARE_SESSION_MAX_AGE_SECONDS = 60  # an older spare may have been dropped while idle

# per interview: (task resolving to (context_manager, session), opened_at)
_spare_sessions = {}


async def _open_session(client, model):
    context_manager = client.aio.live.connect(model=model, config=LIVE_CONFIG)
    session = await context_manager.__aenter__()
    _tighten_keepalive(session)
    return context_manager, session


def _tighten_keepalive(session):
    """Shorten the websocket's ping interval/timeout on an open session.

    google-genai does not pass keepalive options to websockets (and its client options also
    go to the HTTP client, which rejects them), so they are set on the open connection and
    its keepalive loop restarted to pick them up.
    """
    ws = getattr(session, "_ws", None)
    if ws is None or not hasattr(ws, "start_keepalive") or getattr(ws, "keepalive_task", None) is None:
        print("\n⚠️ Could not shorten the Gemini keepalive; using the websockets defaults", flush=True)
        return
    ws.ping_interval = KEEPALIVE_PING_INTERVAL_SECONDS
    ws.ping_timeout = KEEPALIVE_PING_TIMEOUT_SECONDS
    ws.close_timeout = KEEPALIVE_CLOSE_TIMEOUT_SECONDS
    ws.keepalive_task.cancel()
    ws.start_keepalive()


def _close_session_in_background(opened, name, turn_id=None):
    context_manager, _ = opened
    run_in_background(context_manager.__aexit__(None, None, None), name, turn_id)


def prepare_next_session(client, model, session_id):
    """Start connecting the interview's next Gemini session (no-op if one is on its way)."""
    if session_id not in _spare_sessions:
        _spare_sessions[session_id] = (asyncio.create_task(_open_session(client, model)), now())


async def _take_session(client, model, session_id):
    """The spare session if it is usable, otherwise a newly connected one."""
    spare = _spare_sessions.pop(session_id, None)

    if spare is not None:
        task, opened_at = spare
        if now() - opened_at > SPARE_SESSION_MAX_AGE_SECONDS:
            if task.done() and not task.exception():
                _close_session_in_background(task.result(), "old spare Gemini session close")
            else:
                task.cancel()
        else:
            try:
                return await task  # if it is still connecting, only the rest of the wait remains
            except Exception as e:
                print(f"\n⚠️ Spare Gemini session failed, connecting a new one: {e!r}", flush=True)

    return await _open_session(client, model)


class pooled_session:
    """`async with` a connected Gemini session: takes the spare, starts the next spare,
    and closes the session in the background afterwards (nothing after the answer needs it)."""

    def __init__(self, client, model, session_id, turn_id=None):
        self.client, self.model, self.session_id, self.turn_id = client, model, session_id, turn_id

    async def __aenter__(self):
        self.opened = await _take_session(self.client, self.model, self.session_id)
        prepare_next_session(self.client, self.model, self.session_id)
        return self.opened[1]

    async def __aexit__(self, exc_type, exc, tb):
        _close_session_in_background(self.opened, "Gemini session close", self.turn_id)
        if exc_type is not None:
            # the spare was connected over the same (possibly failing) network: retry on a new one
            _discard_spare_session(self.session_id)
        return False


def _discard_spare_session(session_id):
    spare = _spare_sessions.pop(session_id, None)
    if spare is None:
        return
    task, _ = spare
    if task.done() and not task.cancelled() and not task.exception():
        _close_session_in_background(task.result(), "spare Gemini session close")
    else:
        task.cancel()


async def close_spare_session(session_id=None):
    """Close an interview's unused spare session (all of them when session_id is None)."""
    session_ids = list(_spare_sessions) if session_id is None else [session_id]
    for sid in session_ids:
        spare = _spare_sessions.pop(sid, None)
        if spare is None:
            continue
        task, _ = spare
        try:
            context_manager, _ = await asyncio.wait_for(task, timeout=STOP_TIMEOUT_SECONDS)
            await asyncio.wait_for(context_manager.__aexit__(None, None, None), timeout=STOP_TIMEOUT_SECONDS)
        except Exception:
            task.cancel()


async def stop_task(task, name):
    """Cancel a task without waiting forever for it to finish."""

    task.cancel()
    done, _ = await asyncio.wait({task}, timeout=STOP_TIMEOUT_SECONDS)

    if not done:
        print(f"\n⚠️ {name} did not stop within {STOP_TIMEOUT_SECONDS}s; continuing", flush=True)


# ============================================================
# KAFKA PRODUCER (one per process)
# ============================================================
# Starting a producer is a TLS + SASL handshake with the cloud broker (0.4-1.0s
# measured per answer), so it is started once and reused.

KAFKA_SEND_TIMEOUT_SECONDS = 10

_producer = None


async def get_producer():
    global _producer
    if _producer is None:
        from kafka_client import create_producer
        producer = create_producer()
        await producer.start()
        _producer = producer
    return _producer


def discard_producer():
    """Drop a producer that failed, so the next answer starts a fresh one."""
    global _producer
    producer, _producer = _producer, None
    if producer is not None:
        run_in_background(producer.stop(), "Kafka producer stop")


async def close_producer():
    """Stop the shared producer; call once when the program shuts down."""
    global _producer
    producer, _producer = _producer, None
    if producer is not None:
        await producer.stop()


async def send_to_jev(message):
    """Send one answer to Kafka for JEV; failures only cost JEV's advisory check."""
    import json

    turn_id = message["turn_id"]

    try:

        producer_ready_at = now()

        producer = await get_producer()

        log_since(turn_id, "Kafka producer ready", producer_ready_at)

        message = {**message, "kafka_sent_at": now()}

        await asyncio.wait_for(
            producer.send_and_wait(
                "patient-answers",
                json.dumps(message).encode("utf-8"),
            ),
            timeout=KAFKA_SEND_TIMEOUT_SECONDS,
        )

        print("\n📤 Sent patient answer to Kafka")

        log_since(turn_id, "Kafka send", message["kafka_sent_at"])

    except Exception as e:

        print(f"\n⚠️ Kafka send failed, JEV will not see this answer: {e!r}")

        discard_producer()


# ============================================================
# SEND MICROPHONE AUDIO TO GEMINI
# ============================================================

async def send_microphone_audio(session, audio_queue):
    """
    Take microphone PCM chunks from the queue
    and send them to Gemini Live.
    """

    try:

        while True:

            audio_data = await audio_queue.get()

            if audio_data is None:
                break

            await session.send_realtime_input(
                audio=types.Blob(
                    data=audio_data,
                    mime_type="audio/pcm;rate=16000",
                )
            )

    except asyncio.CancelledError:
        pass

    except Exception as e:

        print(
            f"\n❌ Microphone → Gemini error: {e}",
            flush=True
        )


# ============================================================
# LISTEN TO PATIENT
# ============================================================

def speech_seconds(audio_bytes):
    """Length of a PCM16 16 kHz chunk if it is loud enough to be speech, else 0."""
    samples = array.array("h", audio_bytes[: len(audio_bytes) // 2 * 2])
    if not samples:
        return 0.0
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
    return len(samples) / 16000 if rms >= SPEECH_LEVEL else 0.0


async def listen_for_patient_answer(session, audio, timing=None):
    """
    Listen to the patient through the interview's microphone (see audio_io).

    Gemini's automatic VAD determines when the patient
    starts and stops speaking.

    Returns the complete patient transcript. If `timing` (a dict) is given,
    timing["turn_complete_at"] is set when the answer is judged finished.
    """

    loop = asyncio.get_running_loop()

    # room for 60s of audio: after a network stall the delayed audio arrives all at once,
    # and all of it has to reach Gemini (the oldest chunk is dropped only beyond that)
    audio_queue = asyncio.Queue(maxsize=600)

    patient_text = ""

    callback_count = 0

    # the "audio clock": seconds of patient audio received so far; the *_at positions below
    # are on this clock (see AUDIO_STOPPED_SECONDS), last_audio_at is clock time
    heard = 0.0
    last_audio_at = now()

    # how far into that audio Gemini can have got: like Gemini, it moves at most in real
    # time, so audio that arrives in a burst after a stall is not counted before Gemini has
    # had time to listen to it. The Gemini-based timers use this one.
    processed = 0.0
    processed_at = now()

    def gemini_clock():
        nonlocal processed, processed_at
        at = now()
        processed = min(heard, processed + (at - processed_at))
        processed_at = at
        return processed

    # watchdog (see GEMINI_SILENT_SECONDS): speech heard since Gemini last sent anything
    unanswered_speech_seconds = 0.0
    unanswered_speech_at = None

    # the whole answer, kept in case Gemini fails and it has to be transcribed elsewhere
    recorded = bytearray()
    speech_total_seconds = 0.0
    last_speech_at = None

    # --------------------------------------------------------
    # Queue audio safely
    # --------------------------------------------------------

    def enqueue_audio(audio_bytes):

        # If queue is full, remove oldest chunk
        if audio_queue.full():

            try:
                audio_queue.get_nowait()

            except asyncio.QueueEmpty:
                pass

        try:

            audio_queue.put_nowait(audio_bytes)

        except asyncio.QueueFull:

            pass

    # --------------------------------------------------------
    # Audio from the shared microphone (called on the event loop)
    # --------------------------------------------------------

    def on_audio(audio_bytes, status):

        nonlocal callback_count, unanswered_speech_seconds, unanswered_speech_at
        nonlocal speech_total_seconds, last_speech_at, heard, last_audio_at

        callback_count += 1

        if len(recorded) < MAX_RECORDED_ANSWER_SECONDS * 32000:
            recorded.extend(audio_bytes)

        chunk_started = heard
        heard += len(audio_bytes) / 32000  # PCM16 mono 16 kHz
        last_audio_at = now()

        loud = speech_seconds(audio_bytes)
        if loud:
            unanswered_speech_seconds += loud
            speech_total_seconds += loud
            last_speech_at = heard
            if unanswered_speech_at is None:
                unanswered_speech_at = chunk_started

        # Print every 10 callbacks
        if callback_count % 10 == 0:

            print(
                f"\n🎤 MIC CALLBACK: {callback_count}",
                flush=True
            )

        if status:

            print(
                f"\n⚠️ MIC STATUS: {status}",
                flush=True
            )

        enqueue_audio(audio_bytes)

    # --------------------------------------------------------
    # Start listening
    # --------------------------------------------------------

    try:

        audio.start_listening(loop, on_audio)

    except Exception as e:

        print(
            f"\n❌ Could not start microphone:"
        )

        print(e)

        return ""

    print("🎤 Waiting for patient answer...")
    print("🎤 Speak normally...\n")

    # --------------------------------------------------------
    # Start microphone sending task
    # --------------------------------------------------------

    microphone_task = asyncio.create_task(
        send_microphone_audio(
            session,
            audio_queue
        )
    )

    # --------------------------------------------------------
    # Receive transcription; decide when the answer is finished
    # --------------------------------------------------------

    last_words_at = None

    gemini_replying = False

    speaking = False  # between Gemini's ACTIVITY_START and ACTIVITY_END

    speech_ended_at = None

    async def receive_events():

        nonlocal patient_text, last_words_at, gemini_replying, speaking, speech_ended_at
        nonlocal unanswered_speech_seconds, unanswered_speech_at

        # session.receive() stops after each of Gemini's turns; keep reading across them so
        # speech after a pause (which interrupts Gemini's reply) is still heard
        while True:

            received_any = False

            async for response in session.receive():

                received_any = True

                # Gemini is alive: speech so far has been heard
                unanswered_speech_seconds = 0.0
                unanswered_speech_at = None

                activity = response.voice_activity

                if activity is not None:

                    if activity.voice_activity_type == types.VoiceActivityType.ACTIVITY_START:

                        speaking = True

                        speech_ended_at = None

                    elif activity.voice_activity_type == types.VoiceActivityType.ACTIVITY_END:

                        speaking = False

                        speech_ended_at = gemini_clock()

                server_content = response.server_content

                if not server_content:

                    continue

                if server_content.input_transcription:

                    chunk = server_content.input_transcription.text

                    if chunk:

                        patient_text += chunk

                        last_words_at = gemini_clock()

                        audio.emit({"type": "transcript", "text": patient_text.strip()})

                        print(
                            f"\nPatient: {chunk}",
                            end="",
                            flush=True
                        )

                # Gemini only starts replying once its voice detection decided the turn ended
                if (server_content.model_turn
                        or server_content.generation_complete
                        or server_content.turn_complete):

                    gemini_replying = True

                # the patient talked over Gemini's reply: they are not finished yet
                if server_content.interrupted:

                    gemini_replying = False

            if not received_any:

                return  # the session has closed

    receiver = asyncio.create_task(receive_events())

    async def answer_without_gemini(failure):
        """Gemini failed mid-answer: finish recording here and transcribe with OpenAI.

        Raises GeminiNotResponding (the question is asked again) if that fails too.
        """
        print(f"\n⚠️ Switching to the answer recorded here ({failure})", flush=True)
        if timing is not None:
            timing["gemini_failed"] = True

        # nothing more goes to the dead session; the microphone keeps recording
        await stop_task(microphone_task, "Microphone sender")
        await stop_task(receiver, "Gemini receiver")
        audio.emit({"type": "status", "state": "recording",
                    "text": "The voice connection dropped. Keep talking; your answer is still being recorded."})

        # the answer ends after a pause, as with Gemini (the patient may not have started yet),
        # or when no audio has arrived for a long time
        while not (speech_total_seconds >= SPEECH_SECONDS_BEFORE_WATCHDOG
                   and (heard - last_speech_at >= LOCAL_ANSWER_PAUSE_SECONDS
                        or now() - last_audio_at >= AUDIO_STOPPED_SECONDS)):
            await asyncio.sleep(0.1)

        if timing is not None:
            timing["turn_complete_at"] = now()
        audio.stop_listening()
        audio.emit({"type": "status", "state": "transcribing", "text": "Getting your answer…"})

        started_at = now()
        try:
            text = await asyncio.wait_for(
                transcribe_recorded_answer(bytes(recorded)),
                timeout=FALLBACK_TRANSCRIBE_TIMEOUT_SECONDS,
            )
        except Exception as e:
            raise GeminiNotResponding(f"{failure}; fallback transcription failed: {e!r}") from e
        if timing is not None:
            log_since(timing.get("turn_id"), "fallback transcription (OpenAI)", started_at)
        if not text:
            raise GeminiNotResponding(f"{failure}; fallback transcription was empty")

        audio.emit({"type": "transcript", "text": text})
        print(f"\n\n✅ Patient turn complete (recorded here, transcribed by {FALLBACK_TRANSCRIBE_MODEL})")
        print(f"📝 Patient answer: {text}")
        return text

    try:

        end_reason = None

        while end_reason is None:

            if receiver.done():

                try:
                    receiver.result()
                except Exception as e:
                    return await answer_without_gemini(f"Gemini connection lost: {e!r}")

                return patient_text.strip()  # session ended by the server

            if microphone_task.done():

                return await answer_without_gemini("could not send microphone audio to Gemini")

            clock = gemini_clock()

            if unanswered_speech_seconds >= SPEECH_SECONDS_BEFORE_WATCHDOG \
                    and clock - unanswered_speech_at >= GEMINI_SILENT_SECONDS:

                return await answer_without_gemini(
                    f"Gemini stopped responding: patient spoke ({unanswered_speech_seconds:.1f}s of speech), "
                    f"then Gemini sent nothing for {clock - unanswered_speech_at:.1f}s of audio"
                )

            audio_stopped = now() - last_audio_at >= AUDIO_STOPPED_SECONDS

            if audio_stopped and not patient_text.strip() \
                    and speech_total_seconds >= SPEECH_SECONDS_BEFORE_WATCHDOG:

                # speech arrived, then the audio stopped before Gemini transcribed any of it
                return await answer_without_gemini(f"no audio for {AUDIO_STOPPED_SECONDS:.0f}s")

            if patient_text.strip() and last_words_at is not None:

                quiet = clock - last_words_at

                if audio_stopped:

                    end_reason = f"no audio for {AUDIO_STOPPED_SECONDS:.0f}s"

                elif speech_ended_at is not None and not speaking \
                        and clock - speech_ended_at >= ANSWER_PAUSE_SECONDS:

                    end_reason = "patient stopped speaking"

                elif not speaking and gemini_replying and quiet >= ANSWER_QUIET_SECONDS:

                    end_reason = "Gemini started replying"

                elif not speaking and quiet >= ANSWER_SILENCE_SECONDS:

                    end_reason = "no new words"

                elif quiet >= ANSWER_MAX_QUIET_SECONDS:

                    end_reason = "no new words for a long time"

            if end_reason is None:

                await asyncio.sleep(0.1)

        if timing is not None:
            timing["turn_complete_at"] = now()

        print(
            f"\n\n✅ Patient turn complete ({end_reason})"
        )

        print(
            f"📝 Patient answer: "
            f"{patient_text.strip()}"
        )

        return patient_text.strip()

    except (asyncio.CancelledError, GeminiNotResponding):

        raise

    except Exception as e:

        print(
            f"\n❌ Gemini receive error: {e}"
        )

        return ""

    finally:

        # ----------------------------------------------------
        # Stop microphone sending
        # ----------------------------------------------------

        # stop forwarding first; the microphone itself stays open for the next question
        audio.stop_listening()

        await stop_task(microphone_task, "Microphone sender")

        await stop_task(receiver, "Gemini receiver")

        print("\n🎤 Stopped listening (microphone stays open)")

        if timing is not None:
            log_since(timing.get("turn_id"), "answer -> listening stopped", timing.get("turn_complete_at"))

    return patient_text.strip()


# ============================================================
# ASK GEMINI QUESTION
# ============================================================

async def ask_gemini_question(
    session,
    question,
    speaker
):
    """
    Gemini speaks the question to the patient.
    """

    print(
        f"\nQuestion: {question}\n"
    )

    # Mentioning "medical advice" / "diagnosis" here made Gemini append a disclaimer to
    # the question, so the instruction only says what to say.
    prompt = (
        "Read the question below to the patient, word for word.\n"
        "Say only the question: nothing before it and nothing after it.\n"
        "No greeting, no explanation, no disclaimer, no extra question.\n\n"
        f"Question:\n{question}"
    )

    # --------------------------------------------------------
    # Send question to Gemini
    # --------------------------------------------------------

    await session.send_client_content(
        turns={
            "role": "user",

            "parts": [
                {
                    "text": prompt
                }
            ],
        },

        turn_complete=True,
    )

    # --------------------------------------------------------
    # Receive Gemini response
    # --------------------------------------------------------

    first_audio_at = None

    # a session that stops sending while asking is dead (see QUESTION_SILENT_SECONDS)
    responses = session.receive().__aiter__()

    while True:

        try:
            response = await asyncio.wait_for(anext(responses), QUESTION_SILENT_SECONDS)
        except StopAsyncIteration:
            break
        except asyncio.TimeoutError:
            raise GeminiNotResponding(
                f"Gemini sent nothing for {QUESTION_SILENT_SECONDS:.0f}s while asking the question"
            ) from None

        if not response.server_content:

            continue

        server_content = response.server_content

        # ----------------------------------------------------
        # Gemini audio
        # ----------------------------------------------------

        if server_content.model_turn:

            for part in server_content.model_turn.parts:

                if part.inline_data:

                    audio_data = part.inline_data.data

                    if audio_data:

                        if first_audio_at is None:
                            first_audio_at = now()

                        speaker.write(
                            audio_data
                        )

        # ----------------------------------------------------
        # Gemini text transcription
        # ----------------------------------------------------

        if server_content.output_transcription:

            text = (
                server_content
                .output_transcription
                .text
            )

            if text:

                print(
                    f"Agent: {text}",
                    end="",
                    flush=True
                )

        # ----------------------------------------------------
        # Gemini finished speaking
        # ----------------------------------------------------

        if server_content.turn_complete:

            print(
                "\n\n✅ Gemini finished asking."
            )

            break

    return first_audio_at


# ============================================================
# MAIN VOICE AGENT
# ============================================================

async def ask_next_question_voice_agent(
    state,
    client,
    MODEL
):
    """
    Main function called by LangGraph.

    Flow:

        LangGraph
             ↓
        current_question
             ↓
        Gemini asks question
             ↓
        patient speaks
             ↓
        Gemini transcribes
             ↓
        return patient answer
             ↓
        Kafka patient-answers
    """

    # --------------------------------------------------------
    # Get current question
    # --------------------------------------------------------

    question = state["current_question"]

    # Sometimes your question is a dictionary:
    #
    # {
    #   "item": "onset_or_progression",
    #   "suggested_question":
    #       "When did the cough start?"
    # }

    if isinstance(question, dict):

        question = question.get(
            "suggested_question",
            ""
        )

    question = str(question).strip()

    if not question:

        print(
            "❌ No current question found."
        )

        return {
            "case_taking_complete": True,
            "patient_answer": "",
        }

    # timing: logs about getting this question to the patient are tagged with the
    # previous answer's turn_id; logs about the new answer with the new one
    previous_turn_id = state.get("turn_id")
    turn_id = str(uuid.uuid4())
    timing = {"turn_id": turn_id}
    started_at = now()

    # this machine's speakers and microphone, or a browser (see audio_io)
    audio = audio_for(state["session_id"])

    audio.emit({"type": "question", "text": question})

    # --------------------------------------------------------
    # Speaker
    # --------------------------------------------------------

    speaker = audio.open_speaker()

    patient_answer = ""

    try:

        # ====================================================
        # GEMINI LIVE SESSION
        # ====================================================

        # nothing after the answer needs the session, so it closes in the background
        # usually already connected (see SPARE GEMINI SESSION); closes in the background
        async with pooled_session(client, MODEL, state["session_id"], turn_id) as session:

            connected_at = now()
            log_step(previous_turn_id, "Gemini session ready (spare or new connection)", connected_at - started_at)

            print(
                "\n=========================================="
            )

            print(
                "Gemini voice session started"
            )

            print(
                "=========================================="
            )

            # ------------------------------------------------
            # Gemini asks question
            # ------------------------------------------------

            first_audio_at = await ask_gemini_question(
                session=session,

                question=question,

                speaker=speaker,
            )

            if first_audio_at:
                log_step(previous_turn_id, "question audio starts (after connect)", first_audio_at - connected_at)
                if state.get("answered_at"):
                    log_step(previous_turn_id, "PATIENT WAITED: previous answer -> next question audio",
                             first_audio_at - state["answered_at"])

            # a browser may still be playing the question; do not listen to it
            await audio.question_finished()

            # ------------------------------------------------
            # Wait for patient
            # ------------------------------------------------

            print(
                "\nWaiting for patient answer..."
            )

            try:

                patient_answer = await asyncio.wait_for(

                    listen_for_patient_answer(
                        session,
                        audio,
                        timing,
                    ),

                    timeout=PATIENT_TIMEOUT,
                )

                if timing.get("gemini_failed"):
                    # the answer was saved by the fallback; the spare shares the failing network
                    _discard_spare_session(state["session_id"])

            except asyncio.TimeoutError:

                print(
                    "\n\n⏰ Patient did not answer "
                    "within 5 minutes."
                )

                return {
                    "case_taking_complete": True,

                    "patient_answer": "",

                        }

    except Exception as e:

        print(
            f"\n❌ Voice agent error: {e}"
        )

        audio.emit({"type": "status", "state": "reconnecting", "text": "Reconnecting to the voice service…"})

        return {
            "patient_answer": "",
            "agent_error": f"voice agent: {e}",
        }

    finally:

        await audio.close_speaker(speaker)

    log_since(turn_id, "answer -> speaker closed", timing.get("turn_complete_at"))

    # --------------------------------------------------------
    # Check answer
    # --------------------------------------------------------

    # a transcript without any words ("...", "-") is no answer: the question is asked again
    if not re.search(r"\w", patient_answer):

        print(
            "\n⚠️ No patient answer received."
        )

        return {
            "patient_answer": "",
        }

    # --------------------------------------------------------
    # Return LangGraph state
    # --------------------------------------------------------

    print(
        "\n=========================================="
    )

    print(
        "Patient answer received"
    )

    print(
        "=========================================="
    )

    print(
        f"Question: {question}"
    )

    print(
        f"Answer: {patient_answer}"
    )

    # --------------------------------------------------------
    # IMPORTANT:
    # Send to Kafka for JEV (advisory only), in the background:
    # the next question does not wait for it
    # --------------------------------------------------------

    message = {
        "session_id": state["session_id"],

        "patient_id": state.get(
            "patient_id",
            ""
        ),

        "turn_id": turn_id,

        "question": question,

        "answer": patient_answer,

        # timing, passed through JEV and Redis back to the graph
        "answered_at": timing.get("turn_complete_at"),
    }

    run_in_background(send_to_jev(message), "Kafka send")

    audio.emit({"type": "answer", "question": question, "text": patient_answer})

    # --------------------------------------------------------
    # Return LangGraph state
    # --------------------------------------------------------

    return {

        "messages": [
            {
                "role": "assistant",
                "content": question,
            },

            {
                "role": "user",
                "content": patient_answer,
            },
        ],

        "patient_answer": patient_answer,

        "current_question": question,
        
        "turn_id": turn_id,

        "answered_at": timing.get("turn_complete_at"),

    }
