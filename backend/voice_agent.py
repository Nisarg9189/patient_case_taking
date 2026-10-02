"""The intake interview by an Azure AI Foundry voice agent, at /ws/voice-agent.

The browser talks to this server, this server talks to the agent's realtime route
(PROJECT_ENDPOINT/agents/AGENT_NAME/endpoint/protocols/voice) with the server's own Azure
identity (Entra ID only), and the browser never sees Azure. The agent's model, instructions,
voice and turn detection live in Foundry and are applied when the session connects, so this
server sends no session settings, only the case_id (a system message), which the agent passes
to its tools.

The tools are an MCP server (patient-nlp/mcp_server.py, served by this app at /mcp-server/mcp):
Foundry runs them, so this server never sees a tool call. They keep the interview in Redis
(findings, checklist, flags, owner); finish_interview stores the case in Neon, queues the
summary, and sets the Redis key case:<id>:stored, which is how this server learns the
interview is over. If the patient stops early, what was collected is stored here as an aborted case.

Browser -> server
  {"type": "auth", "token", "language"}  first message: the patient's Neon Auth JWT and the
                              language they picked ("en", "hi", "gu", "mr"; see LANGUAGES)
  binary                      microphone audio, PCM16 mono 24 kHz, sent all the time (the
                              service detects speech itself and lets the patient interrupt)
  {"type": "stop"}            end now (what was collected is kept)

Server -> browser
  binary                      the agent's voice, PCM16 mono 24 kHz
  {"type": "ready"}           the agent is connected and will speak first
  {"type": "transcript", "role": "agent" | "patient", "text"}
  {"type": "interrupted"}     the patient started speaking: drop the queued agent audio
  {"type": "status", "state"} what the agent is doing besides speaking: "listening" (waiting for
                              or hearing the patient), "thinking", "saving" (one of its tools is
                              running). The browser shows "speaking" itself, while audio plays.
  {"type": "done", "case_id", "aborted", "reason"}
  {"type": "error", "text"}
"""
import asyncio
import base64
import contextlib
import json
import os
import uuid
from collections import Counter

from fastapi import APIRouter, HTTPException, WebSocket

import auth
import case_store
import db
from patient_extraction import empty_case
from redis_test import redis_db

router = APIRouter()

API_VERSION = "2025-11-15-preview"
FOUNDRY_FEATURES = {"Foundry-Features": "VoiceAgents=V1Preview"}
AUTH_SECONDS = 10
MAX_SECONDS = 20 * 60          # one interview never runs longer (the service bills by the second)
FINISH_POLL_SECONDS = 1        # how often to look for the stored case in Redis
GOODBYE_START_SECONDS = 8      # after the case is stored, how long to wait for the goodbye to start
GOODBYE_QUIET_SECONDS = 2      # the goodbye is over when no agent audio has come for this long

# the languages a patient can pick before the interview (browser code -> name the agent is told)
LANGUAGES = {"en": "English", "hi": "Hindi", "gu": "Gujarati", "mr": "Marathi"}

GREETING = ("Hello, I'm the clinic's voice assistant. I'll ask you a few questions about your health, so the "
            "doctor is ready before your visit. It takes a few minutes. What brings you in today?")

def session_tools(host):
    """The MCP server the agent should use in this session, sent in session.update (when
    VOICE_SESSION_MCP=1), instead of relying on the tool saved on the Foundry agent. The address is
    MCP_PUBLIC_URL, or this app's own address (the Host header of the browser's connection)."""
    if os.getenv("VOICE_SESSION_MCP") != "1" or not os.getenv("MCP_SECRET"):
        return None
    url = os.getenv("MCP_PUBLIC_URL") or f"https://{host}/mcp-server/mcp"
    return [{"type": "mcp", "server_label": "patient_case_taking", "server_url": url,
             "headers": {"Authorization": f"Bearer {os.environ['MCP_SECRET']}"}, "require_approval": "never"}]


_credential = None
_redis = None


def redis():
    global _redis
    if _redis is None:
        _redis = redis_db()
    return _redis


def configured():
    return all(os.getenv(name) for name in ("PROJECT_ENDPOINT", "AGENT_NAME", "REDIS_HOST", "REDIS_PASSWORD"))


def realtime_url():
    """The WebSocket address of the existing agent's realtime route."""
    from urllib.parse import quote, urlparse, urlunparse
    parsed = urlparse(os.environ["PROJECT_ENDPOINT"])
    path = parsed.path.rstrip("/") + f"/agents/{quote(os.environ['AGENT_NAME'], safe='')}/endpoint/protocols/voice"
    return urlunparse(("wss" if parsed.scheme == "https" else "ws", parsed.netloc, path, "",
                       f"api-version={API_VERSION}", ""))


def open_connection():
    from azure.ai.voicelive.aio import connect
    manager = connect(credential=_get_credential(), endpoint=os.environ["PROJECT_ENDPOINT"],
                      api_version=API_VERSION, headers=FOUNDRY_FEATURES)
    # the SDK builds the model/agent address itself; an agent's route is a different path
    manager._prepare_url = realtime_url
    return manager


def _get_credential():
    """DefaultAzureCredential: `az login` on a developer machine, the managed identity on Azure."""
    global _credential
    if _credential is None:
        from azure.identity.aio import DefaultAzureCredential
        _credential = DefaultAzureCredential()
    return _credential


async def close():
    global _credential, _redis
    if _redis is not None:
        with contextlib.suppress(Exception):
            await _redis.r.aclose()
        _redis = None
    if _credential is not None:
        with contextlib.suppress(Exception):
            await _credential.close()
        _credential = None


class Interview:
    """One interview: whose it is, what was said, and what to do when it ends without the agent
    having stored it."""

    def __init__(self, session_id, patient, language=None, tools=None):
        self.session_id = session_id
        self.patient = patient
        self.tools = tools         # MCP tools for session.update, or None
        self.language = language   # name of the language the patient picked, or None
        self.last_audio = 0.0     # when the agent's voice last arrived (loop time)
        self.done_sent = False
        self.case_id = None
        self.turns = []           # [{"question": agent's words, "answer": patient's words}]
        self._agent_said = ""
        self.events = Counter()   # kinds of Voice Live events seen (for the log, never their content)

    def agent_said(self, text):
        self._agent_said = f"{self._agent_said} {text}".strip()

    def patient_said(self, text):
        self.turns.append({"question": self._agent_said, "answer": text})
        self._agent_said = ""

    async def store_partial(self, reason):
        """The interview ended without the agent's finish_interview: keep what it had collected as
        an aborted case. If its tools saved nothing, the case holds the conversation itself and the
        summary agent writes the summary from it. Nothing is stored when nothing was said, or when
        the agent finished (or another stop stored it) first."""
        sid = self.session_id
        record, flags, checklist = await asyncio.gather(
            redis().get_record_notes(sid), redis().get_flags(sid), redis().get_checklist(sid))
        collected = bool(record.findings or flags)
        if not (collected or self.turns):
            return
        if not await redis().r.set(f"case:{sid}:finished", "1", nx=True, ex=3600):
            return
        notes = {}
        for finding in record.findings:
            notes.setdefault(finding.section, []).append(finding.text)

        case = empty_case()
        complaint = "; ".join(notes.get("presenting_complaint", []))
        if complaint:
            case["chief_complaint"] = {"text": complaint, "evidence": complaint}
        case["interview_notes"] = notes
        case["important_reported_symptoms"] = flags
        if not collected:
            case["interview_transcript"] = self.turns
        await case_store.save_original(sid, case, checklist, self.turns, True, reason,
                                       self.patient["user_id"], self.patient["org_id"])
        self.case_id = sid

        if collected:
            summary = {key: "; ".join(notes[key])[:case_store.MAX_SECTION_CHARS] if key in notes else None
                       for key, _ in case_store.SUMMARY_SECTIONS if key != "flags"}
            summary["flags"] = [f"{f['symptom']}: {f['evidence']}" for f in flags]
            summary["presenting_complaint"] = summary["presenting_complaint"] or "Not recorded"
            summary["allergies"] = summary["allergies"] or "Not asked"
            summary["current_medicines"] = summary["current_medicines"] or "Not asked"
            await case_store.save_summary(sid, summary)
        else:
            from kafka_client import create_producer
            producer = create_producer()
            await producer.start()
            try:
                await producer.send_and_wait("case-summary", json.dumps(
                    {"session_id": sid, "patient_id": sid, "case": case, "review": None}).encode("utf-8"))
            finally:
                await producer.stop()


async def _authenticate(websocket):
    message = await asyncio.wait_for(websocket.receive_json(), AUTH_SECONDS)
    if not isinstance(message, dict) or message.get("type") != "auth" or not message.get("token"):
        raise HTTPException(401, "Please sign in to start the interview.")
    user = await auth.user_from_token(message["token"])
    clinics = sorted(user.orgs_with("patient"))
    if not clinics:
        raise HTTPException(403, "Only patients can take the intake interview.")
    return {"user_id": user.id, "org_id": clinics[0]}, LANGUAGES.get(message.get("language"))


async def _from_browser(websocket, connection, interview):
    """The patient's microphone (and stop) to Voice Live."""
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return
        if message.get("bytes"):
            await connection.input_audio_buffer.append(audio=base64.b64encode(message["bytes"]).decode())
        elif message.get("text"):
            if json.loads(message["text"]).get("type") == "stop":
                return


async def _to_browser(websocket, connection, interview):
    """Voice Live's events to the browser."""
    from azure.ai.voicelive.models import (InputTextContentPart, ItemType, MCPApprovalResponseRequestItem,
                                           ServerEventType, SystemMessageItem)

    async def send(item):
        if isinstance(item, (bytes, bytearray)):
            await websocket.send_bytes(bytes(item))
        else:
            await websocket.send_text(json.dumps(item))

    speaks_first = os.getenv("AGENT_SPEAKS_FIRST", "0") == "1"
    ready = False
    state = None          # last status sent to the browser
    spoke = False         # the current response has produced audio

    async def set_state(new):
        nonlocal state
        if new != state:
            state = new
            await send({"type": "status", "state": new})

    async def become_ready():
        nonlocal ready
        if ready:
            return
        ready = True
        if interview.tools:
            await connection.send({"type": "session.update",
                                   "session": {"type": "realtime", "tools": interview.tools, "tool_choice": "auto"}})
            print(f"Voice agent interview {interview.session_id[:8]}: sent the MCP tool in session.update")
        # the agent passes this id to every tool call (its instructions say so)
        text = f"The case_id for this interview is {interview.session_id}."
        if interview.language and interview.language != "English":   # English: the message is just the case id, as before
            text += f" Speak with the patient in {interview.language} for the whole interview, from your first words."
            if speaks_first:   # the Foundry greeting is off: open with ours, in the patient's language
                text += f' Open the conversation now by saying this greeting in {interview.language}: "{GREETING}"'
        await connection.conversation.item.create(item=SystemMessageItem(content=[InputTextContentPart(text=text)]))
        await send({"type": "ready"})
        await set_state("listening")
        if speaks_first:
            await connection.response.create()   # the agent's instructions open the interview

    async for event in connection:
        kind = event.type
        label = str(getattr(kind, "value", kind))
        if kind == ServerEventType.CONVERSATION_ITEM_CREATED:
            label += f":{getattr(event.item.type, 'value', event.item.type)}"
        interview.events[label] += 1
        if interview.events[label] == 1:    # the first of each kind, as it happens (never any content)
            print(f"Voice agent interview {interview.session_id[:8]}: first {label}")
        if kind in (ServerEventType.SESSION_CREATED, ServerEventType.SESSION_UPDATED) or kind == "conversation.created":
            await become_ready()
        elif kind == ServerEventType.RESPONSE_AUDIO_DELTA:
            spoke = True
            interview.last_audio = asyncio.get_running_loop().time()
            delta = event.delta
            await send(base64.b64decode(delta) if isinstance(delta, str) else delta)
        elif kind == ServerEventType.RESPONSE_AUDIO_TRANSCRIPT_DONE:
            text = (event.get("transcript") or "").strip()
            if text:
                interview.agent_said(text)
                await send({"type": "transcript", "role": "agent", "text": text})
        elif kind == ServerEventType.CONVERSATION_ITEM_INPUT_AUDIO_TRANSCRIPTION_COMPLETED:
            text = (event.get("transcript") or "").strip()
            if text:
                interview.patient_said(text)
                await send({"type": "transcript", "role": "patient", "text": text})
        elif kind == ServerEventType.INPUT_AUDIO_BUFFER_SPEECH_STARTED:
            await send({"type": "interrupted"})
            await set_state("listening")
        elif kind == ServerEventType.INPUT_AUDIO_BUFFER_SPEECH_STOPPED:
            await set_state("thinking")
        elif kind == ServerEventType.RESPONSE_CREATED:
            spoke = False
            await set_state("thinking")
        elif kind == ServerEventType.CONVERSATION_ITEM_CREATED:
            if event.item.type == ItemType.MCP_APPROVAL_REQUEST:
                # the agent's tool is set to "ask first": nobody here could answer, so say yes (it is our own tool)
                await connection.conversation.item.create(
                    item=MCPApprovalResponseRequestItem(approval_request_id=event.item.id, approve=True))
                print(f"Voice agent interview {interview.session_id[:8]}: approved a tool call")
        elif kind == ServerEventType.RESPONSE_DONE:
            if spoke:     # a response that only called a tool is followed by another one
                await set_state("listening")
        elif kind in (ServerEventType.RESPONSE_MCP_CALL_IN_PROGRESS, "response.foundry_agent_call.in_progress"):
            await set_state("saving")
        elif kind in (ServerEventType.RESPONSE_MCP_CALL_COMPLETED, ServerEventType.RESPONSE_MCP_CALL_FAILED,
                      "response.foundry_agent_call.completed", "response.foundry_agent_call.failed"):
            await set_state("thinking")
        elif kind == ServerEventType.ERROR:
            print(f"\n⚠️ Voice Live error: {event.error.message}")
            await send({"type": "error", "text": "The voice agent had a problem. Please try again."})
            return


async def _watch_stored(websocket, interview):
    """Wait until finish_interview (run by the agent through the MCP server) has stored the case,
    let the agent's goodbye be spoken, then tell the browser it is done."""
    loop = asyncio.get_running_loop()
    while not await redis().is_stored(interview.session_id):
        await asyncio.sleep(FINISH_POLL_SECONDS)
    stored_at = loop.time()
    while True:       # wait for the goodbye to start, then until the agent has been quiet for a moment
        await asyncio.sleep(0.5)
        now = loop.time()
        if interview.last_audio > stored_at:
            if now - interview.last_audio > GOODBYE_QUIET_SECONDS:
                break
        elif now - stored_at > GOODBYE_START_SECONDS:
            break
    interview.case_id = interview.session_id
    interview.done_sent = True
    await websocket.send_text(json.dumps(
        {"type": "done", "case_id": interview.case_id, "aborted": False, "reason": None}))


@router.websocket("/ws/voice-agent")
async def voice_agent_socket(websocket: WebSocket):
    await websocket.accept()

    async def refuse(text, code):
        with contextlib.suppress(Exception):
            await websocket.send_text(json.dumps({"type": "error", "text": text}))
            await websocket.close(code=code)

    try:
        patient, language = await _authenticate(websocket)
    except HTTPException as e:
        detail = e.detail["message"] if isinstance(e.detail, dict) else e.detail
        print(f"Voice agent: sign-in refused ({e.status_code})")
        return await refuse(detail, 4403 if e.status_code == 403 else 4401)
    except Exception as e:
        print(f"Voice agent: sign-in step failed: {e!r}")
        return await refuse("Please sign in to start the interview.", 4401)
    if not configured():
        print("Voice agent: PROJECT_ENDPOINT, AGENT_NAME or the Redis settings are missing")
        return await refuse("The Azure voice agent is not set up on this server.", 4500)

    session_id = str(uuid.uuid4())

    try:
        await redis().set_owner(session_id, patient["user_id"], patient['org_id'])
    except Exception as e:
        print(f"Voice agent: could not save the owner in Redis: {e!r}")
        return await refuse("The interview could not be started. Please try again.", 4500)
    print(f"Voice agent interview {session_id[:8]} started (language {language})")
    
    host = websocket.headers.get("x-forwarded-host") or websocket.headers.get("host") or ""
    interview = Interview(session_id, patient, language, session_tools(host))
    db.audit_later(patient["user_id"], "start_voice_agent_interview", case_id=session_id, org_id=patient["org_id"])

    try:
        async with open_connection() as connection:
            print(f"Voice agent interview {session_id[:8]}: connected to Foundry")
            tasks = {asyncio.create_task(_from_browser(websocket, connection, interview), name="browser"),
                     asyncio.create_task(_to_browser(websocket, connection, interview), name="foundry"),
                     asyncio.create_task(_watch_stored(websocket, interview), name="stored")}
            done, pending = await asyncio.wait(tasks, timeout=MAX_SECONDS, return_when=asyncio.FIRST_COMPLETED)
            print(f"Voice agent interview {session_id[:8]}: ended by {[t.get_name() for t in done] or 'the time limit'}")
            for task in pending:
                task.cancel()
            for task in pending:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            for task in done:
                if task.exception():
                    raise task.exception()
    except Exception as e:
        print(f"\n❌ Voice agent interview {session_id} failed: {e!r}")
        with contextlib.suppress(Exception):
            await websocket.send_text(json.dumps({"type": "error", "text": "The voice agent could not be reached."}))
    finally:
        print(f"Voice agent interview {session_id[:8]} events: {dict(interview.events)}")
        if not interview.done_sent:
            try:
                await interview.store_partial("Stopped before the agent finished.")
            except Exception as e:
                print(f"\n⚠️ Could not store case {session_id}: {e!r}")
            with contextlib.suppress(Exception):
                await websocket.send_text(json.dumps(
                    {"type": "done", "case_id": interview.case_id, "aborted": True,
                     "reason": "Stopped before the agent finished."}))
        with contextlib.suppress(Exception):
            await websocket.close()
