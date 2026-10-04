"""The same Foundry voice interview as voice_agent.py, over a phone call: /ws/phone.

A telephony provider (Exotel in India, or Twilio) answers the hospital's number, then opens a
WebSocket to this server and streams the call's audio both ways. This server hands that audio to
the same agent, the same MCP tools and the same storing code the web interview uses (voice_agent.py);
only the audio and the way the caller is identified differ.

  Exotel   Voicebot applet (Stream) -> wss://<host>/ws/phone?key=<PHONE_AGENT_KEY>&sample-rate=24000
           (24 kHz audio needs no conversion; 8 kHz works too). Put a hang-up after the applet.
  Twilio   TwiML <Connect><Stream url="wss://<host>/ws/phone"> with <Parameter name="key" value="..."/>
           and <Parameter name="to" value="{{called number}}"/>, <Parameter name="from" .../>
           (Twilio streams 8 kHz mu-law).
  Vobiz    the application's Answer URL is https://<host>/vobiz/answer?key=<PHONE_AGENT_KEY> (also the
           answer_url of an outbound call made with Vobiz's API). That route returns XML with a <Stream>
           to /ws/phone, and remembers who is calling (Vobiz's start message has no numbers) for the
           WebSocket. 8 kHz mu-law both ways.

The whole journey happens on the call: the agent (its own Foundry agent, PHONE_AGENT_NAME, with the
tools of phone_tools.py besides the interview's) helps the caller choose a hospital and doctor, takes the
intake interview, finds a free time and books it. The call ends when the agent's end_call tool runs and
its goodbye has been spoken.

Who the caller is: nobody signs in on a phone, so each caller gets a small account of their own, made
here from the number they call from (<digits>@phone.invalid; it cannot be signed in to). Their name is
what they say, their cases and visits are that account's, and a second call from the same number
continues it. The number is never trusted for more than that: nothing is read back to a caller.

Which hospital: a hospital with its own line (organizations.agent_phone, set in Clinic details) is
chosen by the number that was called. A call to any other number is the platform line: the agent first
helps the caller pick a state, district and hospital.

Environment: PHONE_AGENT_KEY (a long random secret the provider sends), PHONE_AGENT_NAME (the phone agent
in Foundry; AGENT_NAME if not set), plus what the web interview needs.
Optional: PHONE_AGENT_ORG (a hospital's id to use for every call to an unknown number: one-hospital pilots),
PHONE_AGENT_LANGUAGE (en, hi, gu or mr: the language the agent speaks; without it the agent follows
the caller and its Foundry instructions).
"""
import asyncio
import base64
import contextlib
import hmac
import json
import os
import secrets
import uuid
from urllib.parse import parse_qs

from fastapi import APIRouter, Request, Response, WebSocket

import db
import voice_agent
from phone_audio import Codec
from voice_agent import LANGUAGES, MAX_SECONDS, Interview

router = APIRouter()

START_SECONDS = 10          # the provider must say who is calling within this
CHUNK_BYTES = 3200          # Exotel wants audio in multiples of 320 bytes, at least 3200
FLUSH_SECONDS = 0.12        # a half-filled chunk is padded with silence and sent after this long
HANGUP_EXTRA_SECONDS = 0.6  # after the goodbye has finished playing
MAX_DRAIN_SECONDS = 20


def configured():
    return bool(os.getenv("PHONE_AGENT_KEY")) and voice_agent.configured()


def _digits(text):
    return "".join(ch for ch in str(text or "") if ch.isdigit())


def _tail(number):
    """The last digits of a number, for the log (the rest is the caller's)."""
    digits = _digits(number)
    return f"…{digits[-4:]}" if digits else "unknown"


class PhoneLine:
    """One call's WebSocket, presented to voice_agent's _to_browser / _watch_stored as if it were the
    browser: the agent's voice and 'interrupted' go out in the provider's format. Everything else
    those functions send (transcripts, status) has no use on a phone and is dropped."""

    def __init__(self, websocket, start):
        self.websocket = websocket
        info = start.get("start") or {}
        self.vobiz = "callId" in info and "streamId" in info     # Vobiz: {"start": {"callId", "streamId", ...}}
        self.call_id = info.get("callId")
        self.stream_sid = (start.get("stream_sid") or start.get("streamSid") or info.get("stream_sid")
                           or info.get("streamSid") or info.get("streamId"))
        self.twilio = ("streamSid" in start or "streamSid" in info or "customParameters" in info) and not self.vobiz
        self.params = {**(info.get("custom_parameters") or {}), **(info.get("customParameters") or {})}
        self.numbers = {"from": info.get("from"), "to": info.get("to")}
        fmt = info.get("media_format") or info.get("mediaFormat") or {}
        self.rate = int(_digits(fmt.get("sample_rate") or fmt.get("sampleRate") or 8000) or 8000)
        kind = "mulaw8k" if (self.twilio or self.vobiz) else {8000: "pcm8k", 24000: "pcm24k"}.get(self.rate)
        self.codec = Codec(kind) if kind else None   # None: a sample rate we do not convert (Exotel 16 kHz)
        self._pending = b""
        self._flush_task = None
        self._play_until = 0.0     # when the audio sent so far will have finished playing (loop time)

    def param(self, name):
        return self.params.get(name) or self.numbers.get(name)

    # ---- to the caller
    async def send_bytes(self, pcm24k):
        data = self.codec.to_phone(pcm24k)
        loop = asyncio.get_running_loop()
        self._play_until = max(self._play_until, loop.time()) + len(data) / self.codec.bytes_per_second
        if self.twilio or self.vobiz:     # any frame size
            return await self._media(data)
        self._pending += data    # Exotel wants whole chunks
        while len(self._pending) >= CHUNK_BYTES:
            chunk, self._pending = self._pending[:CHUNK_BYTES], self._pending[CHUNK_BYTES:]
            await self._media(chunk)
        self._restart_flush()

    async def send_text(self, text):
        if json.loads(text).get("type") == "interrupted":
            await self.clear()

    async def clear(self):
        """The caller started talking: drop what has not been played yet."""
        self._pending = b""
        self._play_until = 0.0
        with contextlib.suppress(Exception):
            await self.websocket.send_text(json.dumps(
                {"event": "clearAudio", "streamId": self.stream_sid} if self.vobiz else {"event": "clear", **self._sid()}))

    async def _media(self, data):
        if self.vobiz:
            return await self.websocket.send_text(json.dumps(
                {"event": "playAudio", "streamId": self.stream_sid,
                 "media": {"contentType": "audio/x-mulaw", "sampleRate": 8000, "payload": base64.b64encode(data).decode()}}))
        await self.websocket.send_text(json.dumps(
            {"event": "media", **self._sid(), "media": {"payload": base64.b64encode(data).decode()}}))

    def _sid(self):
        return {"streamSid" if self.twilio else "stream_sid": self.stream_sid}

    def _restart_flush(self):
        if self._flush_task:
            self._flush_task.cancel()
        if self._pending:
            self._flush_task = asyncio.create_task(self._flush())

    async def _flush(self):
        await asyncio.sleep(FLUSH_SECONDS)
        if self._pending:
            padded = self._pending + bytes(-len(self._pending) % CHUNK_BYTES)   # silence up to a whole chunk
            self._pending = b""
            with contextlib.suppress(Exception):
                await self._media(padded)

    async def drain(self):
        """Wait until what was sent has been played, so the goodbye is not cut off by the hang-up."""
        if self._flush_task and not self._flush_task.done():
            await self._flush_task
        loop = asyncio.get_running_loop()
        wait = min(max(self._play_until - loop.time(), 0) + HANGUP_EXTRA_SECONDS, MAX_DRAIN_SECONDS)
        await asyncio.sleep(wait)

    # ---- from the caller
    async def feed(self, connection):
        """The caller's audio to the agent, until they hang up (stop / disconnect)."""
        while True:
            message = await self.websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            if not message.get("text"):
                continue
            event = json.loads(message["text"])
            kind = event.get("event")
            if kind == "media":
                payload = (event.get("media") or {}).get("payload")
                if payload:
                    pcm = self.codec.from_phone(base64.b64decode(payload))
                    await connection.input_audio_buffer.append(audio=base64.b64encode(pcm).decode())
            elif kind == "stop":
                return


async def _wait_for_start(websocket):
    while True:
        message = await asyncio.wait_for(websocket.receive(), START_SECONDS)
        if message["type"] == "websocket.disconnect":
            return None
        if message.get("text"):
            event = json.loads(message["text"])
            if event.get("event") == "start":
                return event


CALL_NOTE_SECONDS = 120     # how long the answer route's note about a call waits for its WebSocket


def _same_key(given):
    return hmac.compare_digest(str(given or "").encode(), os.getenv("PHONE_AGENT_KEY", "").encode())


@router.api_route("/vobiz/answer", methods=["GET", "POST"])
async def vobiz_answer(request: Request):
    """Vobiz's Answer URL. It answers with XML that streams the call to /ws/phone, and keeps a note of
    who is calling (Vobiz's WebSocket start message has no numbers), found again by the call's id.
    Vobiz does not sign its requests, so the secret key is part of the URL."""
    if not configured() or not _same_key(request.query_params.get("key")):
        print("Phone agent: Vobiz answer refused (wrong key, or the phone agent is not set up)")
        return Response("forbidden", status_code=403)
    fields = {k: v[-1] for k, v in parse_qs((await request.body()).decode("utf-8", "replace")).items()}
    fields = {**request.query_params, **fields}
    called, caller = fields.get("To"), fields.get("From")
    if str(fields.get("Direction", "")).lower() == "outbound":   # we phoned them: the person is who was called
        caller, called = called, caller
    call_id = fields.get("CallUUID") or fields.get("CallId") or ""
    ticket = secrets.token_hex(8)
    note = json.dumps({"from": caller, "to": called})
    redis = voice_agent.redis().r
    await redis.set(f"phone:ticket:{ticket}", note, ex=CALL_NOTE_SECONDS)
    if call_id:
        await redis.set(f"phone:call:{call_id}", note, ex=CALL_NOTE_SECONDS)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    scheme = "ws" if host.startswith(("localhost", "127.0.0.1")) else "wss"
    print(f"Phone agent: Vobiz call answered ({fields.get('Direction') or 'unknown direction'}, from {_tail(caller)})")
    xml = (f'<Response><Stream bidirectional="true" keepCallAlive="true" contentType="audio/x-mulaw;rate=8000" '
           f'extraHeaders="ticket={ticket}">{scheme}://{host}/ws/phone?ticket={ticket}</Stream><Hangup/></Response>')
    return Response(xml, media_type="text/xml")


async def _vobiz_call(websocket, line):
    """Who is calling, from the answer route's note: found by the ticket in the address or header,
    or else by the call's id. None when there is no note (not a call our answer route set up)."""
    redis = voice_agent.redis().r
    for key in (websocket.query_params.get("ticket") and f"phone:ticket:{websocket.query_params['ticket']}",
                websocket.headers.get("ticket") and f"phone:ticket:{websocket.headers['ticket']}",
                line.call_id and f"phone:call:{line.call_id}"):
        note = key and await redis.get(key)
        if note:
            return json.loads(note)
    return None


async def _hospital(called):
    """(id, name) of the hospital whose AI line this is: the number that was called (the last 10
    digits), or the PHONE_AGENT_ORG fallback; None for the platform line."""
    database = await db.pool()
    tail = _digits(called)[-10:]
    if len(tail) == 10:
        row = await database.fetchrow(
            "SELECT org_id, name FROM organizations WHERE agent_phone <> '' AND right(agent_phone, 10) = $1", tail)
        if row:
            return str(row["org_id"]), row["name"]
    fallback = os.getenv("PHONE_AGENT_ORG", "")
    if fallback:
        with contextlib.suppress(ValueError):
            row = await database.fetchrow("SELECT org_id, name FROM organizations WHERE org_id = $1", uuid.UUID(fallback))
            if row:
                return str(row["org_id"]), row["name"]
    return None


async def _caller_account(caller, session_id):
    """The caller's own account: one per number (so a second call continues it), or per call when
    the number is hidden. Its email is <digits>@phone.invalid, which no one can sign in to."""
    database = await db.pool()
    digits = _digits(caller)
    email = f"{digits}@phone.invalid" if digits else f"call-{session_id}@phone.invalid"
    name = f"Caller +{digits}" if digits else "Caller"
    return str(await database.fetchval(
        """
        INSERT INTO neon_auth."user" (name, email, "emailVerified") VALUES ($1, $2, false)
        ON CONFLICT (email) DO UPDATE SET "updatedAt" = now() RETURNING id
        """,
        name, email))


def _intro(hospital, caller):
    """What the agent is told when the call starts, besides the case id."""
    if hospital:
        text = (f" This is a phone call to the line of {hospital[1]} (org_id {hospital[0]}). The hospital is already "
                "chosen: do not ask where the caller lives; offer this hospital's doctors.")
    else:
        text = (" This is a phone call to the platform line: no hospital is chosen yet. Help the caller choose a state, "
                "district and hospital first.")
    return text + (" The caller's number is known, so never ask for it." if caller else "")


@router.websocket("/ws/phone")
async def phone_socket(websocket: WebSocket):
    await websocket.accept()
    secret = os.getenv("PHONE_AGENT_KEY", "")
    if not configured():
        print("Phone agent: PHONE_AGENT_KEY or the voice agent settings are missing")
        return await websocket.close(code=4500)
    try:
        start = await _wait_for_start(websocket)
    except Exception:
        start = None
    if start is None:
        return await websocket.close(code=4400)
    line = PhoneLine(websocket, start)

    key = websocket.query_params.get("key") or line.params.get("key") or ""
    trusted = False
    if line.vobiz:     # the Answer URL (which had the key) set this call up: it left a note
        note = await _vobiz_call(websocket, line)
        if note:
            line.numbers, trusted = note, True
    if not trusted and not hmac.compare_digest(key.encode(), secret.encode()):
        print("Phone agent: call refused (wrong or missing key)")
        return await websocket.close(code=4401)
    if line.codec is None:
        print(f"Phone agent: unsupported sample rate {line.rate}; use sample-rate=24000 (or 8000)")
        return await websocket.close(code=4400)

    caller = _digits(line.param("from"))
    caller = f"+{caller}" if caller else None
    language = LANGUAGES.get(websocket.query_params.get("lang") or line.params.get("lang") or os.getenv("PHONE_AGENT_LANGUAGE", ""))
    session_id = str(uuid.uuid4())
    try:
        hospital = await _hospital(line.param("to"))
        user_id = await _caller_account(caller, session_id)
        org_id = hospital[0] if hospital else None
        if org_id:
            database = await db.pool()
            await database.execute("INSERT INTO memberships (user_id, org_id, role) VALUES ($1, $2, 'patient') "
                                   "ON CONFLICT DO NOTHING", uuid.UUID(user_id), uuid.UUID(org_id))
        patient = {"user_id": user_id, "org_id": org_id, "doctor_id": None, "document_ids": [], "caller_phone": caller}
        await voice_agent.redis().set_owner(session_id, user_id, org_id, None, [], caller,
                                            extra={"channel": "phone", "line_org": org_id})
    except Exception as e:
        print(f"Phone agent: could not start the call: {e!r}")
        return await websocket.close(code=4500)
    print(f"Phone interview {session_id[:8]} started ({'Twilio' if line.twilio else 'Exotel'}, {line.codec.kind}, "
          f"from {_tail(caller)}, {'hospital line' if hospital else 'platform line'}, language {language})")

    interview = Interview(session_id, patient, language)
    interview.intro = _intro(hospital, caller)
    db.audit_later(user_id, "start_phone_interview", case_id=session_id, org_id=org_id)

    async def ended(sid):
        return bool(await voice_agent.redis().r.exists(f"case:{sid}:ended"))

    try:
        async with voice_agent.open_connection(os.getenv("PHONE_AGENT_NAME") or None) as connection:
            print(f"Phone interview {session_id[:8]}: connected to Foundry")
            tasks = {asyncio.create_task(line.feed(connection), name="caller"),
                     asyncio.create_task(voice_agent._to_browser(line, connection, interview), name="foundry"),
                     asyncio.create_task(voice_agent._watch_stored(line, interview, ended, 0.25), name="stored")}
            done, pending = await asyncio.wait(tasks, timeout=MAX_SECONDS, return_when=asyncio.FIRST_COMPLETED)
            print(f"Phone interview {session_id[:8]}: ended by {[t.get_name() for t in done] or 'the time limit'}")
            if any(t.get_name() == "stored" for t in done):
                with contextlib.suppress(Exception):
                    await line.drain()       # let the goodbye finish before the line is closed
            for task in pending:
                task.cancel()
            for task in pending:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            for task in done:
                if task.exception():
                    raise task.exception()
    except Exception as e:
        print(f"\n❌ Phone interview {session_id} failed: {e!r}")
    finally:
        print(f"Phone interview {session_id[:8]} events: {dict(interview.events)}")
        # the caller hung up (or it failed) before the interview was stored: keep what there is. Nothing
        # happens when finish_interview already stored it. The hospital may have been chosen on the call.
        try:
            owner = await voice_agent.redis().get_owner(session_id) or {}
            patient.update(org_id=owner.get("org_id"), doctor_id=owner.get("doctor_id"))
            if patient["org_id"]:
                await interview.store_partial("The call ended before the agent finished.")
        except Exception as e:
            print(f"\n⚠️ Could not store case {session_id}: {e!r}")
        with contextlib.suppress(Exception):
            await websocket.close()
