"""Opening hours, doctors' availability, and the appointment slots they give.

A clinic's schedule (clinic_schedules, set by its admins):
  timezone           the clinic's time zone; all hours below are its local time
  opening_hours      7 entries, Monday first: ["09:00", "17:00"], or None when closed that day
  slot_minutes       the length of one appointment slot
  patients_per_slot  how many patients one doctor takes in one slot
  days_ahead         how far ahead patients may book (today plus this many days)

A doctor's availability (doctor_availability, also set by the clinic's admins): `weekly`,
7 entries (Monday first), each a list of ["HH:MM", "HH:MM"] ranges, and `days_off` (dates).

A slot is bookable with a doctor when it lies inside the clinic's opening hours and inside
one of the doctor's ranges that day, the doctor is not off that day, the doctor still has
the clinic's doctor role, and fewer than patients_per_slot booked appointments overlap it.

A doctor can refer a booked visit to another doctor, at their own or another hospital
(refer(), the referrals table): the patient's interview goes with it.
"""
import asyncio
import contextlib
import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import db

DEFAULT_SCHEDULE = {
    "timezone": "Asia/Kolkata",
    "slot_minutes": 15,
    "patients_per_slot": 1,
    "days_ahead": 14,
    "opening_hours": [None] * 7,
}
MAX_UPCOMING_PER_PATIENT = 3  # booked, not yet over: stops one account holding many slots


class BookingError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _uuid(value):
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def _at(day, hhmm, tz):
    return datetime.combine(day, time.fromisoformat(hhmm), tzinfo=tz)


# ---- reading and saving the settings

async def get_schedule(org_id, connection=None):
    """The clinic's schedule (the defaults, all days closed, if it was never set)."""
    connection = connection or await db.pool()
    row = await connection.fetchrow(
        "SELECT timezone, slot_minutes, patients_per_slot, days_ahead, opening_hours FROM clinic_schedules WHERE org_id = $1",
        _uuid(org_id),
    )
    return dict(row) if row else dict(DEFAULT_SCHEDULE)


async def save_schedule(org_id, schedule, user_id):
    database = await db.pool()
    await database.execute(
        """
        INSERT INTO clinic_schedules (org_id, timezone, slot_minutes, patients_per_slot, days_ahead,
                                      opening_hours, updated_by)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (org_id) DO UPDATE SET
            timezone = $2, slot_minutes = $3, patients_per_slot = $4, days_ahead = $5,
            opening_hours = $6, updated_at = now(), updated_by = $7
        """,
        _uuid(org_id), schedule["timezone"], schedule["slot_minutes"], schedule["patients_per_slot"],
        schedule["days_ahead"], schedule["opening_hours"], _uuid(user_id),
    )


async def doctors(org_id, connection=None):
    """The clinic's doctors with their availability (none set: never available)."""
    connection = connection or await db.pool()
    rows = await connection.fetch(
        """
        SELECT m.user_id AS doctor_id, u.name, u.email,
               coalesce(a.weekly, '[[], [], [], [], [], [], []]'::jsonb) AS weekly,
               coalesce(a.days_off, '{}') AS days_off
        FROM memberships m
        JOIN neon_auth."user" u ON u.id = m.user_id
        LEFT JOIN doctor_availability a ON a.org_id = m.org_id AND a.doctor_id = m.user_id
        WHERE m.org_id = $1 AND m.role = 'doctor'
        ORDER BY u.name, u.email
        """,
        _uuid(org_id),
    )
    return [dict(r, doctor_id=str(r["doctor_id"]), days_off=sorted(r["days_off"])) for r in rows]


async def save_availability(org_id, doctor_id, weekly, days_off, user_id):
    """Returns False when this person is not a doctor of the clinic."""
    database = await db.pool()
    saved = await database.fetchval(
        """
        INSERT INTO doctor_availability (org_id, doctor_id, weekly, days_off, updated_by)
        SELECT $1, $2, $3, $4, $5
        WHERE EXISTS (SELECT 1 FROM memberships WHERE org_id = $1 AND user_id = $2 AND role = 'doctor')
        ON CONFLICT (org_id, doctor_id) DO UPDATE SET
            weekly = $3, days_off = $4, updated_at = now(), updated_by = $5
        RETURNING true
        """,
        _uuid(org_id), _uuid(doctor_id), weekly, sorted(set(days_off)), _uuid(user_id),
    )
    return bool(saved)


# ---- slots

def compute_slots(schedule, doctor_list, booked, now):
    """Free slots from now to the end of the booking window, earliest first:
    [{"starts_at", "ends_at", "doctors": [{"doctor_id", "name", "places_left"}]}].
    `booked` maps doctor_id to the (starts_at, ends_at) of their booked appointments."""
    tz = ZoneInfo(schedule["timezone"])
    step = timedelta(minutes=schedule["slot_minutes"])
    per_slot = schedule["patients_per_slot"]
    today = now.astimezone(tz).date()
    slots = []
    for offset in range(schedule["days_ahead"] + 1):
        day = today + timedelta(days=offset)
        hours = schedule["opening_hours"][day.weekday()]
        if not hours:
            continue
        working = []
        for doctor in doctor_list:
            ranges = doctor["weekly"][day.weekday()] or []
            if ranges and day not in doctor["days_off"]:
                working.append((doctor, [(_at(day, a, tz), _at(day, b, tz)) for a, b in ranges]))
        if not working:
            continue
        start, closes = _at(day, hours[0], tz), _at(day, hours[1], tz)
        while start + step <= closes:
            end = start + step
            if start > now:
                free = []
                for doctor, ranges in working:
                    if not any(a <= start and end <= b for a, b in ranges):
                        continue
                    taken = sum(1 for s, e in booked.get(doctor["doctor_id"], ()) if s < end and e > start)
                    if taken < per_slot:
                        free.append({"doctor_id": doctor["doctor_id"], "name": doctor["name"] or doctor["email"],
                                     "places_left": per_slot - taken})
                if free:
                    slots.append({"starts_at": start, "ends_at": end, "doctors": free})
            start = end
    return slots


# The database may be far away (a round trip each time), so these functions read what they
# need in as few statements as possible and send independent reads together.

def _booked_by_doctor(items):
    """[{doctor_id, starts_at, ends_at}] (jsonb from _BOOKED) -> {doctor_id: [(starts, ends)]}"""
    booked = {}
    for item in items:
        booked.setdefault(item["doctor_id"], []).append(
            (datetime.fromisoformat(item["starts_at"]), datetime.fromisoformat(item["ends_at"])))
    return booked


# the booked visits of clinic $1 that can overlap its booking window ($2: its days_ahead),
# leaving out the ids in $3 (cancelled by the same statement), as one jsonb value
_BOOKED = """
    SELECT coalesce(jsonb_agg(jsonb_build_object('doctor_id', b.doctor_id, 'starts_at', b.starts_at,
                                                 'ends_at', b.ends_at)), '[]')
    FROM appointments b
    WHERE b.org_id = $1 AND b.status = 'booked' AND b.ends_at > now()
      AND b.starts_at < now() + ($2::int + 2) * interval '1 day'
"""


def takes_bookings(schedule):
    return any(schedule["opening_hours"])


async def bookable_clinics():
    """Clinics with opening hours set and at least one doctor with hours."""
    database = await db.pool()
    rows = await database.fetch(
        """
        SELECT o.org_id, o.name, s.timezone, s.days_ahead, s.opening_hours
        FROM organizations o JOIN clinic_schedules s USING (org_id)
        WHERE EXISTS (SELECT 1 FROM memberships m JOIN doctor_availability a
                        ON a.org_id = m.org_id AND a.doctor_id = m.user_id
                      WHERE m.org_id = o.org_id AND m.role = 'doctor'
                        AND EXISTS (SELECT 1 FROM jsonb_array_elements(a.weekly) AS day WHERE jsonb_array_length(day) > 0))
        ORDER BY o.name
        """
    )
    return [{"org_id": r["org_id"], "name": r["name"], "timezone": r["timezone"], "days_ahead": r["days_ahead"]}
            for r in rows if takes_bookings(r)]


async def _clinic_schedule(connection, org_id):
    """The clinic's schedule (defaults if never set), or None when there is no such clinic."""
    row = await connection.fetchrow(
        """
        SELECT s.timezone, s.slot_minutes, s.patients_per_slot, s.days_ahead, s.opening_hours
        FROM organizations o LEFT JOIN clinic_schedules s USING (org_id) WHERE o.org_id = $1
        """,
        _uuid(org_id),
    )
    if row is None:
        return None
    return dict(row) if row["timezone"] is not None else dict(DEFAULT_SCHEDULE)


async def open_slots(org_id):
    database = await db.pool()
    key = _uuid(org_id)
    schedule, booked, doctor_list = await asyncio.gather(
        _clinic_schedule(database, key),
        database.fetchval(_BOOKED.replace("$2::int", "coalesce((SELECT days_ahead FROM clinic_schedules WHERE org_id = $1), 14)"), key),
        doctors(org_id, database),
    )
    schedule = schedule or dict(DEFAULT_SCHEDULE)
    return {"timezone": schedule["timezone"], "slot_minutes": schedule["slot_minutes"],
            "slots": compute_slots(schedule, doctor_list, _booked_by_doctor(booked), datetime.now(timezone.utc))}


# ---- appointments

@contextlib.asynccontextmanager
async def _locked(database, keys):
    """A transaction holding these advisory locks, begun with all of them in one round trip.
    Every booking takes the patient's lock, then its doctors' in id order, so concurrent
    bookings wait for each other instead of deadlocking or overfilling a slot."""
    for key in keys:  # they go into the SQL text: only our own "kind:uuid[:uuid]" keys
        if not re.fullmatch(r"[a-z]+(:[0-9a-f-]{36})+", key):
            raise ValueError(f"bad lock key {key!r}")
    statements = ["BEGIN"] + [f"SELECT pg_advisory_xact_lock(hashtextextended('{key}', 0))" for key in keys]
    async with database.acquire() as connection:
        await connection.execute("; ".join(statements))
        try:
            yield connection
        except BaseException:
            with contextlib.suppress(Exception):
                await connection.execute("ROLLBACK")
            raise
        await connection.execute("COMMIT")


def _free_slot(schedule, candidates, booked, starts_at):
    """The slot starting at starts_at if one of these doctors is free then, with the doctor
    who has the most room; raises BookingError otherwise."""
    now = datetime.now(timezone.utc)
    slot = next((s for s in compute_slots(schedule, candidates, booked, now) if s["starts_at"] == starts_at), None)
    if slot is None:
        raise BookingError(409, "This time is no longer available. Please choose another.")
    return slot, max(slot["doctors"], key=lambda d: d["places_left"])


def _insert_appointment(more=""):
    """Insert an appointment ($1 org, $2 doctor, $3 patient, $4 case, $5 starts, $6 ends) and
    return it as get() does, in one statement; `more`: further CTEs using new_row."""
    return f"""
        WITH new_row AS (
            INSERT INTO appointments (org_id, doctor_id, patient_user_id, case_id, starts_at, ends_at)
            VALUES ($1, $2, $3, $4, $5, $6) RETURNING *
        ) {more}
        SELECT {_APPOINTMENT_COLUMNS} {_appointment_from("new_row")}
    """


async def book(org_id, doctor_id, starts_at, patient_user_id, case_id=None):
    """Book a slot with this doctor (or, doctor_id None, the doctor with the most room).
    A new booking for the same interview replaces the earlier one. Raises BookingError.
    Round trips: the reads that cannot change under us (together), then the transaction:
    begin + locks, one statement to check, one to insert, commit."""
    database = await db.pool()
    org, patient = _uuid(org_id), _uuid(patient_user_id)
    case = _uuid(case_id) if case_id else None

    async def own_case():
        return case is None or bool(await database.fetchval(
            "SELECT true FROM cases WHERE case_id = $1 AND patient_user_id = $2", case, patient))

    schedule, case_ok, doctor_list = await asyncio.gather(
        _clinic_schedule(database, org), own_case(), doctors(org_id, database))
    if schedule is None:
        raise BookingError(404, "No such clinic")
    if not case_ok:
        raise BookingError(404, "No such interview")
    candidates = [d for d in doctor_list if doctor_id in (None, d["doctor_id"])]
    if not candidates:
        raise BookingError(404, "No such doctor at this clinic")

    keys = [f"patient:{patient}"] + [f"doctor:{org}:{d['doctor_id']}" for d in sorted(candidates, key=lambda d: d["doctor_id"])]
    async with _locked(database, keys) as connection:
        # cancel this interview's earlier upcoming booking (a new one replaces it), then count
        # the patient's upcoming visits and read the clinic's bookings, leaving out the one
        # just cancelled (a statement does not see its own changes)
        state = await connection.fetchrow(
            f"""
            WITH cancelled AS (
                UPDATE appointments SET status = 'cancelled', cancelled_at = now(), cancelled_by = $3
                WHERE $4::uuid IS NOT NULL AND case_id = $4 AND status = 'booked' AND ends_at > now()
                RETURNING appointment_id
            )
            SELECT
                (SELECT count(*) FROM appointments
                 WHERE patient_user_id = $3 AND status = 'booked' AND ends_at > now()
                   AND appointment_id NOT IN (SELECT appointment_id FROM cancelled)) AS upcoming,
                ({_BOOKED} AND b.appointment_id NOT IN (SELECT appointment_id FROM cancelled)) AS booked
            """,
            org, schedule["days_ahead"], patient, case,
        )
        if state["upcoming"] >= MAX_UPCOMING_PER_PATIENT:
            raise BookingError(409, f"You already have {state['upcoming']} upcoming appointments. Cancel one to book another.")
        slot, doctor = _free_slot(schedule, candidates, _booked_by_doctor(state["booked"]), starts_at)
        row = await connection.fetchrow(
            _insert_appointment(), org, _uuid(doctor["doctor_id"]), patient, case, slot["starts_at"], slot["ends_at"])
    return dict(row)


async def refer(appointment_id, to_org_id, to_doctor_id, starts_at, note, referring_doctor_id, referring_orgs):
    """A doctor of the visit's clinic (referring_orgs: the clinics where they are a doctor)
    sends a booked visit to another doctor (usually at another hospital) at a free time
    there, with a note for that doctor. The new visit carries the same interview, so the
    receiving hospital's doctors and nurses can open it. An upcoming visit is moved (it becomes
    'referred'); a visit that has already taken place stays as it was.
    Returns (the new appointment, whether the old one was moved). Raises BookingError."""
    database = await db.pool()
    source_key, to_org = _uuid(appointment_id), _uuid(to_org_id)
    if source_key is None or to_org is None:
        raise BookingError(404, "No such appointment")
    source, schedule, doctor_list = await asyncio.gather(
        database.fetchrow("SELECT org_id, doctor_id, patient_user_id, case_id FROM appointments WHERE appointment_id = $1",
                          source_key),
        _clinic_schedule(database, to_org),
        doctors(to_org_id, database),
    )
    if source is None or str(source["org_id"]) not in referring_orgs:
        raise BookingError(404, "No such appointment")
    candidates = [d for d in doctor_list if d["doctor_id"] == to_doctor_id]
    if schedule is None or not candidates:
        raise BookingError(404, "No such doctor at that hospital")
    if source["org_id"] == to_org and str(source["doctor_id"]) == to_doctor_id:
        raise BookingError(400, "Choose another doctor to refer to")

    keys = [f"patient:{source['patient_user_id']}", f"doctor:{to_org}:{to_doctor_id}"]
    async with _locked(database, keys) as connection:
        state = await connection.fetchrow(
            f"""
            SELECT a.status, a.status = 'booked' AND a.ends_at > now() AS upcoming, ({_BOOKED}) AS booked
            FROM appointments a WHERE a.appointment_id = $3 FOR UPDATE OF a
            """,
            to_org, schedule["days_ahead"], source_key,
        )
        if state["status"] not in ("booked", "completed"):
            raise BookingError(409, "Only a booked or completed visit can be referred")
        slot, doctor = _free_slot(schedule, candidates, _booked_by_doctor(state["booked"]), starts_at)
        # the new visit, the old one moved (if upcoming) and the referral: one statement
        row = await connection.fetchrow(
            _insert_appointment("""
                , moved AS (
                    UPDATE appointments SET status = 'referred', cancelled_at = now(), cancelled_by = $8
                    WHERE appointment_id = $7 AND $9
                ), referral AS (
                    INSERT INTO referrals (case_id, patient_user_id, from_appointment_id, to_appointment_id,
                                           from_org_id, from_doctor_id, to_org_id, to_doctor_id, note)
                    SELECT $4, $3, $7, new_row.appointment_id, $10, $8, $1, $2, $11 FROM new_row
                )
            """),
            to_org, _uuid(doctor["doctor_id"]), source["patient_user_id"], source["case_id"], slot["starts_at"],
            slot["ends_at"], source_key, _uuid(referring_doctor_id), state["upcoming"], source["org_id"], note,
        )
    return dict(row), state["upcoming"]


_APPOINTMENT_COLUMNS = """
    a.appointment_id, a.org_id, o.name AS clinic_name, coalesce(s.timezone, 'Asia/Kolkata') AS timezone,
    a.doctor_id, coalesce(nullif(d.name, ''), d.email) AS doctor_name,
    a.patient_user_id, p.name AS patient_name, p.email AS patient_email,
    a.case_id, a.starts_at, a.ends_at, a.status, a.created_at, a.cancelled_at, a.completed_at,
    (SELECT c.original_case -> 'chief_complaint' ->> 'text' FROM cases c WHERE c.case_id = a.case_id) AS complaint,
    (SELECT jsonb_build_object('clinic_name', ro.name, 'doctor_name', coalesce(nullif(ru.name, ''), ru.email),
                               'note', r.note, 'at', r.created_at)
     FROM referrals r JOIN organizations ro ON ro.org_id = r.from_org_id
     LEFT JOIN neon_auth."user" ru ON ru.id = r.from_doctor_id
     WHERE r.to_appointment_id = a.appointment_id) AS referred_from,
    (SELECT jsonb_build_object('clinic_name', ro.name, 'doctor_name', coalesce(nullif(ru.name, ''), ru.email),
                               'starts_at', ra.starts_at, 'timezone', coalesce(rs.timezone, 'Asia/Kolkata'),
                               'at', r.created_at)
     FROM referrals r JOIN organizations ro ON ro.org_id = r.to_org_id
     JOIN appointments ra ON ra.appointment_id = r.to_appointment_id
     LEFT JOIN clinic_schedules rs ON rs.org_id = r.to_org_id
     LEFT JOIN neon_auth."user" ru ON ru.id = r.to_doctor_id
     WHERE r.from_appointment_id = a.appointment_id ORDER BY r.created_at DESC LIMIT 1) AS referred_to
"""
def _appointment_from(source="appointments"):
    return f"""
    FROM {source} a
    JOIN organizations o ON o.org_id = a.org_id
    LEFT JOIN clinic_schedules s ON s.org_id = a.org_id
    LEFT JOIN neon_auth."user" d ON d.id = a.doctor_id
    LEFT JOIN neon_auth."user" p ON p.id = a.patient_user_id
"""


_APPOINTMENT_FROM = _appointment_from()


async def get(appointment_id):
    key = _uuid(appointment_id)
    if key is None:
        return None
    database = await db.pool()
    row = await database.fetchrow(f"SELECT {_APPOINTMENT_COLUMNS} {_APPOINTMENT_FROM} WHERE a.appointment_id = $1", key)
    return dict(row) if row else None


async def for_patient(patient_user_id, limit=20):
    """The patient's appointments: upcoming first (soonest first), then past and cancelled."""
    database = await db.pool()
    rows = await database.fetch(
        f"""
        SELECT {_APPOINTMENT_COLUMNS} {_APPOINTMENT_FROM}
        WHERE a.patient_user_id = $1
        ORDER BY (a.status = 'booked' AND a.ends_at > now()) DESC,
                 CASE WHEN a.status = 'booked' AND a.ends_at > now() THEN a.starts_at END ASC,
                 a.starts_at DESC
        LIMIT $2
        """,
        _uuid(patient_user_id), limit,
    )
    return [dict(r) for r in rows]


async def for_clinic_days(org_id, first_day: date | None, count=1):
    """The clinic's appointments on `count` days of its own calendar from first_day (None:
    today there), each day with its opening hours and each doctor's hours (the day planner
    shows them as columns). The three queries go out together: one round trip of waiting."""
    database = await db.pool()
    key = _uuid(org_id)
    zone = "coalesce((SELECT timezone FROM clinic_schedules WHERE org_id = $1), 'Asia/Kolkata')"
    first = f"coalesce($2::date, (now() AT TIME ZONE {zone})::date)"
    settings, rows, doctor_list = await asyncio.gather(
        database.fetchrow(
            f"""
            SELECT {first} AS first_day, s.timezone, s.slot_minutes, s.patients_per_slot, s.days_ahead, s.opening_hours
            FROM (SELECT 1) AS one LEFT JOIN clinic_schedules s ON s.org_id = $1
            """,
            key, first_day,
        ),
        database.fetch(
            f"""
            SELECT {_APPOINTMENT_COLUMNS} {_APPOINTMENT_FROM}
            WHERE a.org_id = $1
              AND a.starts_at >= ({first})::timestamp AT TIME ZONE {zone}
              AND a.starts_at < ({first} + $3::int)::timestamp AT TIME ZONE {zone}
            ORDER BY a.starts_at, doctor_name
            """,
            key, first_day, count,
        ),
        doctors(org_id, database),
    )
    schedule = dict(DEFAULT_SCHEDULE)
    if settings["timezone"] is not None:
        schedule.update({k: settings[k] for k in DEFAULT_SCHEDULE})
    tz = ZoneInfo(schedule["timezone"])
    by_day = {}
    for r in rows:
        by_day.setdefault(r["starts_at"].astimezone(tz).date(), []).append(dict(r))

    result = []
    for offset in range(count):
        day = settings["first_day"] + timedelta(days=offset)
        weekday = day.weekday()
        plan = [{"doctor_id": d["doctor_id"], "name": d["name"] or d["email"], "email": d["email"],
                 "day_off": day in d["days_off"],
                 "hours": [] if day in d["days_off"] else (d["weekly"][weekday] or [])}
                for d in doctor_list]
        result.append({"timezone": schedule["timezone"], "day": day, "slot_minutes": schedule["slot_minutes"],
                       "opening_hours": schedule["opening_hours"][weekday], "doctors": plan,
                       "appointments": by_day.get(day, [])})
    return result


async def for_clinic_day(org_id, day: date | None):
    return (await for_clinic_days(org_id, day, 1))[0]


async def cancel(appointment_id, user_id, desk_orgs=(), any_clinic=False):
    """Cancel a booked visit, if this user may: their own before it starts, or (front desk,
    clinic admin of desk_orgs; any_clinic: platform admin) one that has not ended. A completed
    visit cannot be cancelled. One statement. Returns the cancelled visit's {org_id, case_id},
    or None (not theirs, not booked, or too late)."""
    database = await db.pool()
    row = await database.fetchrow(
        """
        UPDATE appointments SET status = 'cancelled', cancelled_at = now(), cancelled_by = $2
        WHERE appointment_id = $1 AND status = 'booked' AND ends_at > now()
          AND ((patient_user_id = $2 AND starts_at > now()) OR $3 OR org_id = ANY($4::uuid[]))
        RETURNING org_id, case_id
        """,
        _uuid(appointment_id), _uuid(user_id), any_clinic, [_uuid(o) for o in desk_orgs],
    )
    return dict(row) if row else None


async def complete(appointment_id, user_id, staff_orgs=(), any_clinic=False):
    """The clinic (staff of staff_orgs; any_clinic: platform admin) marks a booked visit as
    completed: it took place, and the patient can no longer cancel or change it.
    Returns the visit's {org_id, case_id}, or None."""
    database = await db.pool()
    row = await database.fetchrow(
        """
        UPDATE appointments SET status = 'completed', completed_at = now(), completed_by = $2
        WHERE appointment_id = $1 AND status = 'booked' AND ($3 OR org_id = ANY($4::uuid[]))
        RETURNING org_id, case_id
        """,
        _uuid(appointment_id), _uuid(user_id), any_clinic, [_uuid(o) for o in staff_orgs],
    )
    return dict(row) if row else None
