"""Web backend for the voice interview.

Run from this folder, with the patient-nlp virtualenv:

    ../patient-nlp/.venv/bin/uvicorn app:app --port 8000

One interview per WebSocket connection at /ws/interview.

Browser -> server
  binary                      microphone audio, PCM16 mono 16 kHz (sent while listening)
  {"type": "playback_done"}   the question has finished playing
  {"type": "stop"}            end the interview now

Server -> browser
  binary                      question audio, PCM16 mono 24 kHz
  {"type": "question", "text"}          a question is about to be spoken
  {"type": "question_audio_end"}        all question audio has been sent
  {"type": "listening"} / {"type": "stopped_listening"}
  {"type": "transcript", "text"}        the answer so far
  {"type": "answer", "question", "text"}
  {"type": "case", "case", "checklist", "remaining"}
  {"type": "done", "case", "checklist", "aborted", "reason"}
  {"type": "error", "text"}
  {"type": "status", "state", "text"}   the voice connection failed: "recording" / "transcribing"
                                        (the answer is kept and transcribed separately) or
                                        "reconnecting" (the question is asked again)
"""
import asyncio
import contextlib
import json
import os
import sys
import uuid
from pathlib import Path

PATIENT_NLP = Path(__file__).resolve().parent.parent / "patient-nlp"
sys.path.insert(0, str(PATIENT_NLP))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PATIENT_NLP / ".env")

from fastapi import FastAPI, WebSocket  # noqa: E402

import workflow  # noqa: E402
from audio_io import BrowserAudio, register_audio, unregister_audio  # noqa: E402
from gemini_voice_agent import ask_next_question_voice_agent, close_producer, close_spare_session  # noqa: E402
from patient_extraction import end_conversation, start_conversation  # noqa: E402

OPENING_QUESTION = "Hello, what brings you in today?"
OPENING_ATTEMPTS = 3


@contextlib.asynccontextmanager
async def lifespan(app):
    yield
    await close_spare_session()
    await close_producer()


app = FastAPI(lifespan=lifespan)


@app.get("/api/health")
async def health():
    return {"ok": True}


def _config(session_id):
    return {"configurable": {"thread_id": session_id}}


async def _final_state(session_id):
    snapshot = await workflow.chain.aget_state(_config(session_id))
    return snapshot.values if snapshot else {}


async def run_interview(session_id, audio):
    # the OpenAI conversation (~1-3s to create) is ready by the time the opening is answered
    conversation = asyncio.create_task(start_conversation(session_id))

    # the opening question is asked by the same voice agent, then the graph takes over
    opening = None
    for _ in range(OPENING_ATTEMPTS):
        result = await ask_next_question_voice_agent(
            {"session_id": session_id, "patient_id": session_id, "current_question": OPENING_QUESTION, "turn_id": None},
            workflow.client,
            workflow.MODEL,
        )
        if result.get("case_taking_complete"):
            break  # no answer within the time limit
        if result.get("patient_answer"):
            opening = result
            break

    if opening is None:
        audio.emit({"type": "done", "case": None, "checklist": {}, "aborted": True,
                    "reason": "No answer to the opening question."})
        with contextlib.suppress(Exception):
            await end_conversation(await conversation)
        return

    try:
        conversation_id = await conversation
    except Exception as e:
        print(f"\n⚠️ Could not create the OpenAI conversation in advance: {e!r}")
        conversation_id = ""  # extract_data creates one

    state = {
        "messages": opening["messages"],
        "patient_id": session_id,
        "session_id": session_id,
        "openai_conversation_id": conversation_id,
        "turn_id": opening["turn_id"],
        "answered_at": opening["answered_at"],
        "current_question": None,
        "patient_answer": opening["patient_answer"],
        "extracted_data": {},
        "next_questions": [],
        "no_of_questions_remaining_to_ask": 0,
        "case_taking_complete": False,
    }

    async for update in workflow.chain.astream(state, _config(session_id), stream_mode="updates"):
        values = update.get("extract_data")
        if values:
            audio.emit({
                "type": "case",
                "case": values.get("extracted_data"),
                "checklist": values.get("checklist"),
                "remaining": values.get("no_of_questions_remaining_to_ask"),
            })

    final = await _final_state(session_id)
    audio.emit({
        "type": "done",
        "case": final.get("extracted_data"),
        "checklist": final.get("checklist"),
        "aborted": bool(final.get("interview_aborted")),
        "reason": final.get("abort_reason"),
    })


async def cleanup(session_id):
    """Release everything an interview holds; a normal finish has already done most of it."""
    workflow.forget_session(session_id)
    await close_spare_session(session_id)
    conversation_id = (await _final_state(session_id)).get("openai_conversation_id")
    if conversation_id:
        try:
            await end_conversation(conversation_id)
        except Exception as e:
            print(f"\n⚠️ Could not delete OpenAI conversation {conversation_id}: {e!r}")


def serve_frontend():
    """Serve the built React app (frontend/dist) from this server, when FRONTEND_DIST is set.

    Used in the Docker deployment, so the page and /ws/interview share one origin. Mounted
    last: routes added before it (/api/..., /ws/...) take priority over the files.
    """
    dist = os.getenv("FRONTEND_DIST")
    if dist and Path(dist).is_dir():
        from fastapi.staticfiles import StaticFiles
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")


@app.websocket("/ws/interview")
async def interview_socket(websocket: WebSocket):
    await websocket.accept()
    session_id = str(uuid.uuid4())

    async def send(item):
        if isinstance(item, (bytes, bytearray)):
            await websocket.send_bytes(bytes(item))
        else:
            await websocket.send_text(json.dumps(item))

    audio = BrowserAudio(send)
    register_audio(session_id, audio)
    interview = asyncio.create_task(run_interview(session_id, audio))
    stopped_by_patient = False

    try:
        while not interview.done():
            receive = asyncio.create_task(websocket.receive())
            done, _ = await asyncio.wait({receive, interview}, return_when=asyncio.FIRST_COMPLETED)
            if receive not in done:
                receive.cancel()
                break

            message = receive.result()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("bytes"):
                audio.feed_microphone(message["bytes"])
            elif message.get("text"):
                event = json.loads(message["text"])
                if event.get("type") == "playback_done":
                    audio.playback_done()
                elif event.get("type") == "stop":
                    stopped_by_patient = True
                    break

        if interview.done() and interview.exception():
            print(f"\n❌ Interview {session_id} failed: {interview.exception()!r}")
            audio.emit({"type": "error", "text": "The interview stopped because of a server error."})

    finally:
        if not interview.done():
            interview.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await interview

        if stopped_by_patient:
            final = await _final_state(session_id)
            audio.emit({"type": "done", "case": final.get("extracted_data"), "checklist": final.get("checklist"),
                        "aborted": True, "reason": "Stopped by the patient."})

        await audio.flush()
        await cleanup(session_id)
        unregister_audio(session_id)
        await audio.close()
        with contextlib.suppress(Exception):
            await websocket.close()


serve_frontend()
