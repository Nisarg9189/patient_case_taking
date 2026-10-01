"""Web backend for the voice interview.

Run from this folder, with the patient-nlp virtualenv:

    ../patient-nlp/.venv/bin/uvicorn app:app --port 8000

One interview per WebSocket connection at /ws/interview.

Browser -> server
  {"type": "auth", "token"}   first message: the patient's Neon Auth JWT (see auth.py);
                              without it, or if it is not a patient, the socket is closed
  binary                      microphone audio, PCM16 mono 16 kHz (sent while listening)
  {"type": "playback_done"}   the question has finished playing
  {"type": "review_confirmed", "sections"}  the patient checked the case and pressed Save
                              ({summary section: text}); the interview then finishes
  {"type": "stop"}            end the interview now

Server -> browser
  binary                      question audio, PCM16 mono 24 kHz
  {"type": "question", "text"}          a question is about to be spoken
  {"type": "question_audio_end"}        all question audio has been sent
  {"type": "listening"} / {"type": "stopped_listening"}
  {"type": "transcript", "text"}        the answer so far
  {"type": "answer", "question", "text"}
  {"type": "case", "case", "checklist", "remaining"}
  {"type": "done", "case", "checklist", "aborted", "reason", "case_id"}
                                        case_id: the stored case (null if there is none);
                                        the patient's edits go to PUT /api/cases/{case_id}/review
  {"type": "review_case", "case", "checklist"}   the questions are done: show the case for
                                        the patient to check (see workflow.patient_review)
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

from fastapi import FastAPI, HTTPException, WebSocket  # noqa: E402

import auth  # noqa: E402
import case_store  # noqa: E402
import db  # noqa: E402
from api import router as api_router  # noqa: E402
from booking_api import router as booking_router  # noqa: E402
from prescriptions import router as prescriptions_router  # noqa: E402
import voice_agent  # noqa: E402

import workflow  # noqa: E402
from audio_io import BrowserAudio, register_audio, unregister_audio  # noqa: E402
from gemini_voice_agent import ask_next_question_voice_agent, close_producer, close_spare_session  # noqa: E402
from langgraph.types import Command  # noqa: E402
from patient_extraction import end_conversation, start_conversation  # noqa: E402

OPENING_QUESTION = "Hello, what brings you in today?"
OPENING_ATTEMPTS = 3

# The finished case waits this long for the patient to check it and press Save (the graph is
# paused at workflow.patient_review); after that it is stored as not confirmed.
REVIEW_TIMEOUT_SECONDS = 15 * 60

# per interview waiting for the patient's review: a future the WebSocket handler resolves
_reviews: dict[str, asyncio.Future] = {}


async def _summary_worker():
    """summary_agent.py's Kafka consumer, in this process (RUN_SUMMARY_WORKER=1): for hosting
    that scales to zero, where a separate worker would have nothing to wake it up. It runs
    while the app runs, so a finished interview is summarised within seconds; cases sent
    while the app was stopped are read when it starts (the consumer group remembers)."""
    import summary_agent
    while True:
        try:
            await summary_agent.summarize_patient_case()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"\n⚠️ Summary worker stopped: {e!r}; restarting in 30 s")
            await asyncio.sleep(30)


@contextlib.asynccontextmanager
async def lifespan(app):
    worker = asyncio.create_task(_summary_worker()) if os.getenv("RUN_SUMMARY_WORKER") == "1" else None
    yield
    if worker:
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await worker
    await close_spare_session()
    await close_producer()
    await voice_agent.close()
    await db.close()


app = FastAPI(lifespan=lifespan)
app.include_router(api_router)
app.include_router(booking_router)
app.include_router(prescriptions_router)
app.include_router(voice_agent.router)


@app.get("/api/health")
async def health():
    return {"ok": True, "azure_voice_agent": voice_agent.configured()}


def _config(session_id):
    return {"configurable": {"thread_id": session_id}}


async def _final_state(session_id):
    snapshot = await workflow.chain.aget_state(_config(session_id))
    return snapshot.values if snapshot else {}


async def finish(audio, session_id, patient, stopped_reason=None):
    """Store the finished interview's case and tell the browser (the "done" event)."""
    final = await _final_state(session_id)
    aborted = bool(stopped_reason) or bool(final.get("interview_aborted"))
    reason = stopped_reason or final.get("abort_reason")
    case, case_id = final.get("extracted_data"), None
    if case:
        try:
            turns = [{"question": q, "answer": a} for q, a in workflow.question_answer_pairs(final["messages"])]
        except (KeyError, ValueError):
            turns = []
        try:
            review = final.get("patient_review") if final.get("review_confirmed") else None
            await case_store.save_original(session_id, case, final.get("checklist"), turns, aborted, reason,
                                           patient["user_id"], patient["org_id"], review)
            case_id = session_id
        except Exception as e:
            print(f"\n⚠️ Could not store case {session_id}: {e!r}")
    audio.emit({"type": "done", "case": case, "checklist": final.get("checklist"),
                "aborted": aborted, "reason": reason, "case_id": case_id,
                "review": final.get("patient_review") if final.get("review_confirmed") else None})


async def run_interview(session_id, audio, patient):
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

    # a finished interview pauses at patient_review: show the case, resume once it is saved
    snapshot = await workflow.chain.aget_state(_config(session_id))
    if "patient_review" in (snapshot.next or ()):
        waiting = asyncio.get_running_loop().create_future()
        _reviews[session_id] = waiting
        audio.emit({"type": "review_case", "case": snapshot.values.get("extracted_data"),
                    "checklist": snapshot.values.get("checklist")})
        try:
            answer = await asyncio.wait_for(asyncio.shield(waiting), REVIEW_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            answer = {"confirmed": False, "sections": None}
        finally:
            _reviews.pop(session_id, None)
        async for _ in workflow.chain.astream(Command(resume=answer), _config(session_id), stream_mode="updates"):
            pass

    await finish(audio, session_id, patient)


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


INTERVIEW_AUTH_SECONDS = 10


async def authenticate_interview(websocket):
    """The patient taking this interview. The browser's first message is
    {"type": "auth", "token": <Neon Auth JWT>}: in a message, not the URL, so the token never
    ends up in access logs."""
    message = await asyncio.wait_for(websocket.receive_json(), INTERVIEW_AUTH_SECONDS)
    if not isinstance(message, dict) or message.get("type") != "auth" or not message.get("token"):
        raise HTTPException(401, "Please sign in to start the interview.")
    user = await auth.user_from_token(message["token"])
    clinics = sorted(user.orgs_with("patient"))
    if not clinics:
        raise HTTPException(403, "Only patients can take the intake interview.")
    return {"user_id": user.id, "org_id": clinics[0]}


@app.websocket("/ws/interview")
async def interview_socket(websocket: WebSocket):
    await websocket.accept()
    session_id = str(uuid.uuid4())

    try:
        patient = await authenticate_interview(websocket)
    except Exception as e:
        refused = isinstance(e, HTTPException) and e.status_code == 403
        detail = e.detail if isinstance(e, HTTPException) else "Please sign in to start the interview."
        text = detail["message"] if isinstance(detail, dict) else detail
        with contextlib.suppress(Exception):
            await websocket.send_text(json.dumps({"type": "error", "text": text}))
            await websocket.close(code=4403 if refused else 4401)
        return
    db.audit_later(patient["user_id"], "start_interview", case_id=session_id, org_id=patient["org_id"])

    async def send(item):
        if isinstance(item, (bytes, bytearray)):
            await websocket.send_bytes(bytes(item))
        else:
            await websocket.send_text(json.dumps(item))

    audio = BrowserAudio(send)
    register_audio(session_id, audio)
    interview = asyncio.create_task(run_interview(session_id, audio, patient))
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
                elif event.get("type") == "review_confirmed":
                    sections = event.get("sections")
                    waiting = _reviews.get(session_id)
                    if isinstance(sections, dict) and waiting is not None and not waiting.done():
                        waiting.set_result({"confirmed": True, "sections": {
                            str(k)[:60]: str(v)[:case_store.MAX_SECTION_CHARS]
                            for k, v in list(sections.items())[:case_store.MAX_SECTIONS]}})
                elif event.get("type") == "stop":
                    stopped_by_patient = True
                    break

        if interview.done() and interview.exception():
            print(f"\n❌ Interview {session_id} failed: {interview.exception()!r}")
            audio.emit({"type": "error", "text": "The interview stopped because of a server error."})

    finally:
        waiting = _reviews.get(session_id)
        if waiting is not None and not waiting.done():
            # the patient left at the review step: the interview is finished, so keep the case
            waiting.set_result({"confirmed": False, "sections": None})
            stopped_by_patient = False
            with contextlib.suppress(Exception):
                await asyncio.wait_for(asyncio.shield(interview), 60)
        if not interview.done():
            interview.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await interview

        if stopped_by_patient:
            await finish(audio, session_id, patient, "Stopped by the patient.")

        await audio.flush()
        await cleanup(session_id)
        unregister_audio(session_id)
        await audio.close()
        with contextlib.suppress(Exception):
            await websocket.close()


serve_frontend()
