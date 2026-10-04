"""The MCP tools that let the phone agent take a caller through the whole journey on one call:
choose a hospital and doctor, the intake interview (the tools in patient-nlp/mcp_server.py), then a
time slot and the booking. They are registered on the same MCP server (register(mcp), called by app.py)
and only work for a phone call: the call's owner record in Redis (written by phone_agent.py) says so.

The order the agent follows (agents/phone_agent_instructions.md): the caller tells the problem and answers the
health questions (record_answer each time), hears the summary and confirms it, and only then chooses a hospital
(by name, or at most three that fit the problem) and a doctor (or lets the agent pick the best match).
  record_answer ... -> [find_hospitals -> list_doctors] -> choose_doctor -> save_patient_name
  -> finish_interview -> get_open_slots -> book_appointment -> end_call

The interview is stored (finish_interview) with the hospital and doctor chosen by then, so it goes to them.
"""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import db
import hospitals
import scheduling
import voice_agent
from india import STATES

MAX_HOSPITALS = 3
SLOTS_PER_DAY = 3        # when no day is asked for, the first few free times of each day ...
DAYS_SHOWN = 4           # ... for the first few days that have any
SLOTS_ON_A_DAY = 8       # when a day is asked for


def _owner_key(case_id):
    return f"case:{case_id}:owner"


async def _owner(case_id):
    """The call's owner record, or None when this is not a live phone call."""
    owner = await voice_agent.redis().get_owner(case_id)
    return owner if owner and owner.get("channel") == "phone" else None


async def _save_owner(case_id, owner):
    await voice_agent.redis().r.set(_owner_key(case_id), json.dumps(owner), ex=3600)


def _open_to_calls(owner):
    """Which hospitals a call may use. The platform line only offers hospitals listed on the website (a
    state, a district and a doctor). A hospital's own line (or the pilot hospital, PHONE_AGENT_ORG)
    works whether or not it is listed: its callers chose it by dialling it. It still needs a doctor."""
    if owner.get("line_org"):
        return "EXISTS (SELECT 1 FROM memberships m WHERE m.org_id = o.org_id AND m.role = 'doctor')"
    return hospitals._LISTED


def _no_call():
    return {"ok": False, "error": "unknown or expired case_id"}


def _state(text):
    """The canonical state for what was said ('gujarat', 'Gujarat.'), or None."""
    text = " ".join(str(text).replace(".", " ").split()).lower()
    if not text:
        return None
    exact = [s for s in STATES if s.lower() == text]
    return (exact or [s for s in STATES if text in s.lower() or s.lower() in text] or [None])[0]


def _spoken(when, tz):
    local = when.astimezone(ZoneInfo(tz))
    return f"{local:%A} {local.day} {local:%B}, {local.hour % 12 or 12}:{local:%M} {'AM' if local.hour < 12 else 'PM'}"


def _keywords(text, limit=5):
    """Comma- or space-separated words as typed, tidied: at most `limit` of at least 3 letters."""
    words = []
    for part in str(text or "").replace(",", " ").replace("%", " ").replace("_", " ").split():
        if len(part) >= 3 and part.lower() not in words:
            words.append(part.lower())
    return words[:limit]


async def find_hospitals(case_id: str, name: str | None = None, specialty: str | None = None,
                         state: str | None = None, district: str | None = None) -> dict:
    """Find hospitals the caller can choose from, best rated first, at most three, with the fee.

    Use it on the platform line (the call's opening message says no hospital is chosen), after the
    caller has confirmed their summary. If the caller named a hospital, pass name (the words of
    it). If they do not know which hospital, pass specialty: two to four English words for the kind
    of doctor their problem needs, with synonyms, for example "dermatology skin" or "orthopaedic
    bone joint", chosen from their chief complaint (this only routes them, never a diagnosis); you
    get up to three hospitals that have such a doctor. With neither you get the three best rated.
    Pass district (and state) in English only to narrow down, for example when the caller says where
    they live or when more_available is true and the three are not near them. Read the names (with
    the fee) to the caller and let them choose; then call list_doctors for the one they pick."""
    owner = await _owner(case_id)
    if not owner:
        return _no_call()
    if owner.get("line_org"):
        return {"ok": False, "error": "This line belongs to one hospital; it is already chosen. Use list_doctors."}
    where, args = [hospitals._LISTED], []
    for word in _keywords(name, 6):    # every word of the name must be in the hospital's name
        args.append(f"%{word}%")
        where.append(f"o.name ILIKE ${len(args)}")
    if state and state.strip():
        canonical = _state(state)
        if not canonical:
            return {"ok": False, "error": "That state was not understood. Ask again, or leave it out."}
        args.append(canonical)
        where.append(f"o.state = ${len(args)}")
    if district and district.strip():
        args.append(" ".join(district.split()))
        where.append(f"lower(o.district) = lower(${len(args)})")
    wanted = _keywords(specialty)

    async def search(extra):
        database = await db.pool()
        rows = await database.fetch(
            f"""
            SELECT o.org_id, o.name, o.address, o.district, o.consultation_fee, r.avg, r.n,
                   (SELECT count(*) FROM memberships m WHERE m.org_id = o.org_id AND m.role = 'doctor') AS doctors
            FROM organizations o {hospitals._RATINGS}
            WHERE {" AND ".join(where + extra[0])}
            ORDER BY r.avg DESC NULLS LAST, r.n DESC NULLS LAST, o.name LIMIT {MAX_HOSPITALS + 1}
            """,
            *args, *extra[1],
        )
        return rows

    matched = None
    if wanted:   # a hospital with a doctor of this kind (or that says so in its description)
        marks = [f"${len(args) + i + 1}" for i in range(len(wanted))]
        clause = " OR ".join(f"p.specialty ILIKE {m} OR p.qualification ILIKE {m} OR o.description ILIKE {m}" for m in marks)
        rows = await search(([f"EXISTS (SELECT 1 FROM memberships m JOIN doctor_profiles p ON p.user_id = m.user_id "
                              f"WHERE m.org_id = o.org_id AND m.role = 'doctor' AND ({clause}))"],
                             [f"%{w}%" for w in wanted]))
        matched = bool(rows)
        if not rows:
            rows = await search(([], []))
    else:
        rows = await search(([], []))
    if not rows:
        return {"ok": True, "hospitals": [],
                "note": "No hospital matches. Ask if they want the best rated ones instead (call again with no arguments)."}
    result = {"ok": True, "more_available": len(rows) > MAX_HOSPITALS, "hospitals": [
        {"org_id": str(r["org_id"]), "name": r["name"], "district": r["district"] or None,
         "address": r["address"] or None, "consultation_fee_rupees": r["consultation_fee"], "doctors": r["doctors"],
         "rating": round(r["avg"], 1) if r["avg"] is not None else None, "ratings": r["n"] or 0}
        for r in rows[:MAX_HOSPITALS]]}
    if matched is False:
        result["note"] = ("No hospital lists that kind of doctor, so these are the best rated. Say so, and let the caller "
                          "choose, or name a hospital they know.")
    return result


async def list_doctors(case_id: str, org_id: str | None = None, specialty: str | None = None) -> dict:
    """The doctors of a hospital with their speciality. org_id may be left out when the call is
    to one hospital's own line. Pass specialty (two to four English words for the kind of doctor
    the caller's problem needs, with synonyms) to get the matching doctors first and
    suggested_doctor_id, the best match. Offer the doctors by name and speciality and let the
    caller choose; if they say any doctor is fine, choose suggested_doctor_id (or, with no match,
    the general medicine doctor) and tell them who. Then call choose_doctor."""
    owner = await _owner(case_id)
    if not owner:
        return _no_call()
    org = owner.get("line_org") or org_id
    try:
        org = hospitals._key(org)
    except Exception:
        return {"ok": False, "error": "Choose a hospital first (find_hospitals)."}
    if owner.get("line_org") and str(org) != owner["line_org"]:
        return {"ok": False, "error": "This line belongs to one hospital; use its doctors."}
    database = await db.pool()
    hospital = await database.fetchrow(
        f"SELECT o.name, o.consultation_fee FROM organizations o WHERE o.org_id = $1 AND {_open_to_calls(owner)}", org)
    if hospital is None:
        return {"ok": False, "error": "No such hospital."}
    doctors = await database.fetch(
        """
        SELECT m.user_id AS doctor_id, coalesce(nullif(u.name, ''), split_part(u.email, '@', 1)) AS name,
               coalesce(p.specialty, '') AS specialty, coalesce(p.qualification, '') AS qualification
        FROM memberships m JOIN neon_auth."user" u ON u.id = m.user_id
        LEFT JOIN doctor_profiles p ON p.user_id = m.user_id
        WHERE m.org_id = $1 AND m.role = 'doctor' ORDER BY name
        """,
        org,
    )
    wanted = _keywords(specialty)

    def fits(d):
        text = f"{d['specialty']} {d['qualification']}".lower()
        return bool(wanted) and any(w in text for w in wanted)
    ranked = sorted(doctors, key=lambda d: not fits(d))    # matching doctors first (the order is otherwise kept)
    suggested = next((d for d in ranked if fits(d)), None)
    return {"ok": True, "hospital": hospital["name"], "org_id": str(org),
            "consultation_fee_rupees": hospital["consultation_fee"],
            "suggested_doctor_id": str(suggested["doctor_id"]) if suggested else None,
            "doctors": [{"doctor_id": str(d["doctor_id"]), "name": d["name"], "specialty": d["specialty"] or None,
                         "qualification": d["qualification"] or None, "matches_problem": fits(d)} for d in ranked]}


async def choose_doctor(case_id: str, org_id: str | None = None, doctor_id: str | None = None) -> dict:
    """Record the hospital and doctor the caller chose; the interview goes to them. Call it once,
    before the interview questions. doctor_id may be left out if the caller has no preference
    (the doctor with the most free time is used when the visit is booked)."""
    owner = await _owner(case_id)
    if not owner:
        return _no_call()
    org = owner.get("line_org") or org_id
    try:
        org, doctor = hospitals._key(org), (hospitals._key(doctor_id) if doctor_id else None)
    except Exception:
        return {"ok": False, "error": "That hospital or doctor was not understood. Check the ids from list_doctors."}
    if owner.get("line_org") and str(org) != owner["line_org"]:
        return {"ok": False, "error": "This line belongs to one hospital."}
    database = await db.pool()
    row = await database.fetchrow(
        f"""
        SELECT o.name,
               (SELECT coalesce(nullif(u.name, ''), split_part(u.email, '@', 1)) FROM memberships m
                JOIN neon_auth."user" u ON u.id = m.user_id
                WHERE m.org_id = o.org_id AND m.user_id = $2 AND m.role = 'doctor') AS doctor_name
        FROM organizations o WHERE o.org_id = $1 AND {_open_to_calls(owner)}
        """,
        org, doctor,
    )
    if row is None:
        return {"ok": False, "error": "No such hospital."}
    if doctor and not row["doctor_name"]:
        return {"ok": False, "error": "No such doctor at that hospital."}
    await database.execute(
        "INSERT INTO memberships (user_id, org_id, role) VALUES ($1, $2, 'patient') ON CONFLICT DO NOTHING",
        hospitals._key(owner["user_id"]), org)
    owner.update(org_id=str(org), doctor_id=str(doctor) if doctor else None)
    await _save_owner(case_id, owner)
    return {"ok": True, "hospital": row["name"], "doctor": row["doctor_name"] or "the first available doctor",
            "now": "Now ask the caller's name (save_patient_name), then call finish_interview, then offer times (get_open_slots)."}


async def save_patient_name(case_id: str, name: str) -> dict:
    """Save the caller's name as they said it (in English letters if they can be spelt, else as
    spoken), so the doctor knows who they are. Ask for it at the start of the call."""
    owner = await _owner(case_id)
    if not owner:
        return _no_call()
    name = " ".join(str(name).split())[:80]
    if not name:
        return {"ok": False, "error": "Ask for the name again."}
    database = await db.pool()
    # only the call's own generated account is ever renamed here
    await database.execute(
        """UPDATE neon_auth."user" SET name = $2, "updatedAt" = now()
           WHERE id = $1 AND email LIKE '%@phone.invalid'""",
        hospitals._key(owner["user_id"]), name)
    return {"ok": True}


async def get_open_slots(case_id: str, date: str | None = None) -> dict:
    """Free appointment times with the chosen doctor (or any doctor of the hospital if none was
    chosen). Without date: the first few times of the next few days. With date (YYYY-MM-DD): that
    day's times. Read two or three to the caller; to book, pass a slot's starts_at unchanged to
    book_appointment. Only after finish_interview. When the caller says yes to a time, book it at
    once with book_appointment; do not ask again."""
    owner = await _owner(case_id)
    if not owner:
        return _no_call()
    if not owner.get("org_id"):
        return {"ok": False, "error": "Choose the hospital and doctor first (choose_doctor)."}
    result = await scheduling.open_slots(owner["org_id"])
    tz, doctor = result["timezone"], owner.get("doctor_id")
    slots = [s for s in result["slots"] if doctor is None or any(d["doctor_id"] == doctor for d in s["doctors"])]
    if date:
        try:
            wanted = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            return {"ok": False, "error": "date must be YYYY-MM-DD"}
        slots = [s for s in slots if s["starts_at"].astimezone(ZoneInfo(tz)).date() == wanted][:SLOTS_ON_A_DAY]
    else:
        days, kept = {}, []
        for s in slots:
            day = s["starts_at"].astimezone(ZoneInfo(tz)).date()
            if len(days) >= DAYS_SHOWN and day not in days:
                break
            days.setdefault(day, 0)
            if days[day] < SLOTS_PER_DAY:
                days[day] += 1
                kept.append(s)
        slots = kept
    if not slots:
        return {"ok": True, "slots": [], "note": "No free time then. Ask for another day, or say no time is free for now."}
    return {"ok": True, "timezone": tz, "slots": [
        {"starts_at": s["starts_at"].isoformat(), "spoken": _spoken(s["starts_at"], tz)} for s in slots]}


async def book_appointment(case_id: str, starts_at: str) -> dict:
    """Book the slot the caller chose (starts_at exactly as get_open_slots gave it). Call it the moment
    the caller says yes to a time, and only after finish_interview. When it succeeds the call is
    closed by itself as soon as you have finished speaking: tell the caller the hospital, doctor,
    day and time in one or two short sentences, add a short goodbye, and ask nothing more (no
    "anything else?"). Do not call end_call."""
    owner = await _owner(case_id)
    if not owner:
        return _no_call()
    if not owner.get("org_id"):
        return {"ok": False, "error": "Choose the hospital and doctor first."}
    try:
        when = datetime.fromisoformat(starts_at)
    except ValueError:
        return {"ok": False, "error": "Pass starts_at exactly as get_open_slots gave it."}
    if when.tzinfo is None:
        return {"ok": False, "error": "Pass starts_at exactly as get_open_slots gave it."}
    try:
        appointment = await scheduling.book(owner["org_id"], owner.get("doctor_id"), when, owner["user_id"], case_id)
    except scheduling.BookingError as e:
        message = str(e)
        if e.status == 404 and "interview" in message:
            message = "The interview is not saved yet: finish it (finish_interview) before booking."
        return {"ok": False, "error": message}
    database = await db.pool()
    info = await database.fetchrow(
        """SELECT o.name, o.address, coalesce(nullif(u.name, ''), split_part(u.email, '@', 1)) AS doctor,
                  coalesce(s.timezone, 'Asia/Kolkata') AS tz
           FROM organizations o LEFT JOIN clinic_schedules s USING (org_id)
           LEFT JOIN neon_auth."user" u ON u.id = $2 WHERE o.org_id = $1""",
        appointment["org_id"], appointment["doctor_id"])
    db.audit_later(owner["user_id"], "book_appointment", case_id=case_id, org_id=owner["org_id"],
                   detail={"by": "phone", "starts_at": when.isoformat()})
    # the journey is over: the call closes once the agent has said the confirmation and goodbye
    await voice_agent.redis().r.set(f"case:{case_id}:ended", "1", ex=3600)
    return {"ok": True, "hospital": info["name"], "address": info["address"] or None, "doctor": info["doctor"],
            "when": _spoken(appointment["starts_at"], info["tz"]),
            "now": "Say the hospital, doctor, day and time in one or two short sentences, then a short goodbye. "
                   "Do not ask anything else. The call closes by itself after you finish."}


async def end_call(case_id: str, no_booking: bool = False) -> dict:
    """Call it to close a call that ends without a booking, then say a short goodbye: the call is closed
    after you finish speaking. (After a booking you do not need it: book_appointment closes the call.)
    If the interview was saved but no appointment is booked, this refuses (so a booking
    the caller agreed to is not forgotten): book it first. Pass no_booking=true only when the caller
    does not want an appointment, no time was free, or it was an emergency stop."""
    if not await _owner(case_id):
        return _no_call()
    if not no_booking:
        database = await db.pool()
        row = await database.fetchrow(
            """SELECT EXISTS (SELECT 1 FROM appointments WHERE case_id = c.case_id AND status = 'booked') AS booked
               FROM cases c WHERE c.case_id = $1""", hospitals._key(case_id))
        if row is not None and not row["booked"]:
            return {"ok": False, "error": "No appointment is booked yet. If the caller chose a time and said yes, call "
                    "book_appointment now. If they want one, offer times (get_open_slots). Only if they do not want "
                    "one (or no time is free, or it was an emergency) call end_call again with no_booking=true."}
    await voice_agent.redis().r.set(f"case:{case_id}:ended", "1", ex=3600)
    return {"ok": True, "now": "Say a short goodbye now."}


def register(mcp):
    for tool in (find_hospitals, list_doctors, choose_doctor, save_patient_name, get_open_slots,
                 book_appointment, end_call):
        mcp.tool(tool)
