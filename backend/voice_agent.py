"""The intake interview by an Azure AI Foundry voice agent, at /ws/voice-agent.

The browser talks to this server, this server talks to the agent's realtime route
(PROJECT_ENDPOINT/agents/AGENT_NAME/endpoint/protocols/voice) with the server's own Azure
identity (Entra ID only), and the browser never sees Azure. The agent's model, instructions,
voice and turn detection live in Foundry and are applied when the session connects, so this
server sends no session settings. Its tools are the agent's function tools (declare them in
Foundry with the schemas in _tools()); when the agent calls one, it runs here:

  record_note(section, text)  keep what the patient said under one summary heading
  flag_urgent(reason)         add a red flag (the agent then tells the patient to get help)
  finish_interview()          store the case and tell the browser

Browser -> server
  {"type": "auth", "token"}   first message: the patient's Neon Auth JWT
  binary                      microphone audio, PCM16 mono 24 kHz, sent all the time (the
                              service detects speech itself and lets the patient interrupt)
  {"type": "stop"}            end now (what was collected is kept)

Server -> browser
  binary                      the agent's voice, PCM16 mono 24 kHz
  {"type": "ready"}           the agent is connected and will speak first
  {"type": "transcript", "role": "agent" | "patient", "text"}
  {"type": "interrupted"}     the patient started speaking: drop the queued agent audio
  {"type": "done", "case_id", "aborted", "reason"}
  {"type": "error", "text"}
"""
import asyncio
import base64
import contextlib
import json
import os
import uuid

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
MAX_NOTE_CHARS = case_store.MAX_SECTION_CHARS

# the sections the agent may fill: the clinician summary's headings, without the flags
NOTE_SECTIONS = [key for key, _ in case_store.SUMMARY_SECTIONS if key != "flags"]

_credential = None

r = redis_db()

def configured():
    return all(os.getenv(name) for name in ("PROJECT_ENDPOINT", "AGENT_NAME"))


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
    global _credential
    if _credential is not None:
        with contextlib.suppress(Exception):
            await _credential.close()
        _credential = None


def _tools():
    from azure.ai.voicelive.models import FunctionTool
    return [
        FunctionTool(
            name="record_note",
            description="Save what the patient told you under one heading of the case summary. Call it after "
                        "each topic is answered, with the patient's own facts in a short plain sentence "
                        "(no diagnosis). Calling it again for the same heading adds to it.",
            parameters={
                "type": "object",
                "properties": {
                    "section": {"type": "string", "enum": NOTE_SECTIONS,
                                "description": "presenting_complaint = main complaint, onset, course, severity; "
                                               "history_of_presenting_complaint = each symptom in detail; "
                                               "allergies = named substances and reactions, or 'None known (patient-reported)'; "
                                               "current_medicines = name and dose, or 'None (patient-reported)'; "
                                               "vitals_reported_by_patient = readings as stated; "
                                               "pertinent_negatives = symptoms the patient denied"},
                    "text": {"type": "string"},
                },
                "required": ["section", "text"],
            },
        ),
        FunctionTool(
            name="flag_urgent",
            description="Call at once when the patient describes something that may be an emergency (chest pain "
                        "now, severe breathlessness, fainting, heavy bleeding, thoughts of self-harm). The clinic "
                        "sees the flag first.",
            parameters={"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]},
        ),
        FunctionTool(
            name="finish_interview",
            description="Call once, after the patient has confirmed the read-back summary (or at the end of "
                        "an urgent stop). It stores the case for the doctor.",
            parameters={"type": "object", "properties": {}, "required": []},
        ),
    ]


class Interview:
    """What one interview has collected, and how it is stored."""

    def __init__(self, session_id, patient):
        self.session_id = session_id
        self.patient = patient
        self.notes = {}
        self.flags = []
        self.turns = []          # [{"question": agent's words, "answer": patient's words}]
        self._agent_said = ""
        self.stored = False
        self.case_id = None
        self.aborted = False
        self.reason = None

    def agent_said(self, text):
        self._agent_said = f"{self._agent_said} {text}".strip()

    def patient_said(self, text):
        self.turns.append({"question": self._agent_said, "answer": text})
        self._agent_said = ""

    def call(self, name, arguments):
        """Run a tool; the result goes back to the agent as text."""
        try:
            args = json.loads(arguments) if isinstance(arguments, str) and arguments else (arguments or {})
        except ValueError:
            return {"ok": False, "error": "arguments were not valid JSON"}
        if name == "record_note":
            section, text = args.get("section"), str(args.get("text", "")).strip()
            if section not in NOTE_SECTIONS or not text:
                return {"ok": False, "error": f"section must be one of {NOTE_SECTIONS} and text must not be empty"}
            old = self.notes.get(section)
            self.notes[section] = f"{old}; {text}" if old else text
            self.notes[section] = self.notes[section][:MAX_NOTE_CHARS]
            return {"ok": True}
        if name == "flag_urgent":
            reason = str(args.get("reason", "")).strip()[:300]
            if reason and reason not in self.flags:
                self.flags.append(reason)
            return {"ok": True}
        if name == "finish_interview":
            return {"ok": True}  # stored by the caller, which knows the connection
        return {"ok": False, "error": f"unknown tool {name}"}

    async def store(self, aborted=False, reason=None):
        """Store the case once (a stop after finish_interview does nothing more). Nothing is stored
        when the patient said nothing worth keeping."""
        if self.stored:
            return
        self.stored = True
        if not (self.notes or self.flags):
            return
        self.aborted, self.reason = aborted, reason
        case = empty_case()
        complaint = self.notes.get("presenting_complaint")
        if complaint:
            case["chief_complaint"] = {"text": complaint, "evidence": complaint}
        summary = {
            "flags": self.flags,
            "allergies": self.notes.get("allergies") or "Not asked",
            "current_medicines": self.notes.get("current_medicines") or "Not asked",
            "presenting_complaint": self.notes.get("presenting_complaint") or "Not recorded",
        }
        for key in NOTE_SECTIONS:
            summary.setdefault(key, self.notes.get(key))
        await case_store.save_original(self.session_id, case, None, self.turns, aborted, reason,
                                       self.patient["user_id"], self.patient["org_id"])
        await case_store.save_summary(self.session_id, summary)
        self.case_id = self.session_id


async def _authenticate(websocket):
    message = await asyncio.wait_for(websocket.receive_json(), AUTH_SECONDS)
    if not isinstance(message, dict) or message.get("type") != "auth" or not message.get("token"):
        raise HTTPException(401, "Please sign in to start the interview.")
    user = await auth.user_from_token(message["token"])
    clinics = sorted(user.orgs_with("patient"))
    if not clinics:
        raise HTTPException(403, "Only patients can take the intake interview.")
    return {"user_id": user.id, "org_id": clinics[0]}


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
    """Voice Live's events to the browser; the agent's tool calls run here."""
    from azure.ai.voicelive.models import FunctionCallOutputItem, ItemType, ServerEventType

    async def send(item):
        if isinstance(item, (bytes, bytearray)):
            await websocket.send_bytes(bytes(item))
        else:
            await websocket.send_text(json.dumps(item))

    speaks_first = os.getenv("AGENT_SPEAKS_FIRST", "0") == "1"
    ready = False

    async def become_ready():
        nonlocal ready
        if ready:
            return
        ready = True
        await send({"type": "ready"})
        if speaks_first:
            await connection.response.create()   # the agent's instructions open the interview

    calls = {}       # call_id -> {"name", "item_id", "arguments"} until the response is done
    async for event in connection:
        kind = event.type
        if kind in (ServerEventType.SESSION_CREATED, ServerEventType.SESSION_UPDATED) or kind == "conversation.created":
            await become_ready()
        elif kind == ServerEventType.RESPONSE_AUDIO_DELTA:
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
        elif kind == ServerEventType.CONVERSATION_ITEM_CREATED:
            item = event.item
            if item.type == ItemType.FUNCTION_CALL:
                calls[item.call_id] = {"name": item.name, "item_id": item.id, "arguments": None}
        elif kind == ServerEventType.RESPONSE_FUNCTION_CALL_ARGUMENTS_DONE:
            if event.call_id in calls:
                calls[event.call_id]["arguments"] = event.arguments
        elif kind == ServerEventType.RESPONSE_DONE:
            ready = {cid: c for cid, c in calls.items() if c["arguments"] is not None}
            calls.clear()
            if not ready:
                if interview.stored and interview.case_id:
                    return       # the goodbye after finish_interview has been spoken
                continue
            for call_id, call in ready.items():
                result = interview.call(call["name"], call["arguments"])
                if call["name"] == "finish_interview":
                    await interview.store()
                    await send({"type": "done", "case_id": interview.case_id, "aborted": False, "reason": None})
                    result = {"ok": True, "stored": interview.case_id is not None}
                await connection.conversation.item.create(
                    previous_item_id=call["item_id"],
                    item=FunctionCallOutputItem(call_id=call_id, output=json.dumps(result)),
                )
            await connection.response.create()
        elif kind == ServerEventType.ERROR:
            print(f"\n⚠️ Voice Live error: {event.error.message}")
            await send({"type": "error", "text": "The voice agent had a problem. Please try again."})
            return


@router.websocket("/ws/voice-agent")
async def voice_agent_socket(websocket: WebSocket):
    await websocket.accept()

    async def refuse(text, code):
        with contextlib.suppress(Exception):
            await websocket.send_text(json.dumps({"type": "error", "text": text}))
            await websocket.close(code=code)

    try:
        patient = await _authenticate(websocket)
    except HTTPException as e:
        detail = e.detail["message"] if isinstance(e.detail, dict) else e.detail
        return await refuse(detail, 4403 if e.status_code == 403 else 4401)
    except Exception:
        return await refuse("Please sign in to start the interview.", 4401)
    if not configured():
        return await refuse("The Azure voice agent is not set up on this server.", 4500)

    session_id = str(uuid.uuid4())
    
    try:
        await r.set_owner(session_id, patient["user_id"], patient['org_id'])
    
    except Exception:
        return await refuse("The interview could not be started. Please try again.", 4500)
    
    interview = Interview(session_id, patient)
    db.audit_later(patient["user_id"], "start_voice_agent_interview", case_id=session_id, org_id=patient["org_id"])

    try:
        async with open_connection() as connection:
            tasks = {asyncio.create_task(_from_browser(websocket, connection, interview)),
                     asyncio.create_task(_to_browser(websocket, connection, interview))}
            done, pending = await asyncio.wait(tasks, timeout=MAX_SECONDS, return_when=asyncio.FIRST_COMPLETED)
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
        if not interview.stored:
            try:
                await interview.store(aborted=True, reason="Stopped before the agent finished.")
            except Exception as e:
                print(f"\n⚠️ Could not store case {session_id}: {e!r}")
            with contextlib.suppress(Exception):
                await websocket.send_text(json.dumps(
                    {"type": "done", "case_id": interview.case_id, "aborted": True, "reason": interview.reason}))
        with contextlib.suppress(Exception):
            await websocket.close()
