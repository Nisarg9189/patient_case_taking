"""The app's Postgres database (Neon): one connection pool, and the tables it needs.

DATABASE_URL (in .env) is the Neon connection string (pooled or direct: the direct address
is used, see _dsn). The tables are created on first use.
Users themselves live in Neon Auth's own schema (neon_auth."user"); the app's tables refer
to them by id.

  organizations  clinics (with the address, phone, email and registration number printed on
                 their prescriptions); one is the default, where self-registered patients belong
  memberships    who has which role in which clinic (patient, doctor, nurse, front_desk,
                 clinic_admin); platform admins come from PLATFORM_ADMIN_EMAILS instead
  invitations    one-time links that give a role in a clinic to a given email address
                 (only a SHA-256 hash of the link's token is stored)
  cases          one row per finished interview (see case_store.py)
  clinic_schedules     a clinic's opening hours and slot settings   } see
  doctor_availability  each doctor's weekly hours and days off      } backend/scheduling.py
  appointments         patients' bookings                           }
  referrals            a doctor sent a patient's visit (and interview) to another doctor
  patient_profiles     a patient's date of birth, sex, weight, phone, address, ABHA   } see
  doctor_profiles      a doctor's registration number, council and qualification  } see
  prescriptions        what a doctor prescribed for a case (draft, signed, void)    } backend/prescriptions.py
  patient_documents    photos/PDFs of a patient's past lab reports and prescriptions, with the
                       OCR markdown and the summary for the doctor (backend/documents.py)
  audit_log      who viewed or changed what, and when
"""
import asyncio
import json
import os
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import asyncpg

ROLES = ("patient", "doctor", "nurse", "front_desk", "clinic_admin")

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS organizations (
        org_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        name       text NOT NULL,
        is_default boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS organizations_one_default ON organizations (is_default) WHERE is_default",
    """
    INSERT INTO organizations (name, is_default)
    SELECT 'Main clinic', true WHERE NOT EXISTS (SELECT 1 FROM organizations WHERE is_default)
    """,
    f"""
    CREATE TABLE IF NOT EXISTS memberships (
        user_id    uuid NOT NULL REFERENCES neon_auth."user" (id) ON DELETE CASCADE,
        org_id     uuid NOT NULL REFERENCES organizations ON DELETE CASCADE,
        role       text NOT NULL CHECK (role IN ({", ".join(f"'{r}'" for r in ROLES)})),
        created_at timestamptz NOT NULL DEFAULT now(),
        created_by uuid,
        PRIMARY KEY (user_id, org_id, role)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cases (
        case_id         uuid PRIMARY KEY,
        finished_at     timestamptz,
        aborted         boolean,
        reason          text,
        original_case   jsonb,
        checklist       jsonb,
        conversation    jsonb,
        review_sections jsonb,
        reviewed_at     timestamptz,
        summary         text,
        summarized_at   timestamptz
    )
    """,
    "ALTER TABLE cases ADD COLUMN IF NOT EXISTS patient_user_id uuid",
    "ALTER TABLE cases ADD COLUMN IF NOT EXISTS org_id uuid",
    # the clinician summary as sections (summary_agent.py's summary_output); `summary` keeps
    # the same as "Label: text" lines
    "ALTER TABLE cases ADD COLUMN IF NOT EXISTS summary_sections jsonb",
    "CREATE INDEX IF NOT EXISTS memberships_by_org ON memberships (org_id, role)",
    "CREATE INDEX IF NOT EXISTS cases_by_org ON cases (org_id, finished_at DESC)",
    "CREATE INDEX IF NOT EXISTS cases_by_patient ON cases (patient_user_id, finished_at DESC)",
    f"""
    CREATE TABLE IF NOT EXISTS invitations (
        invite_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        token_hash  text NOT NULL UNIQUE,
        org_id      uuid NOT NULL REFERENCES organizations ON DELETE CASCADE,
        email       text NOT NULL,
        role        text NOT NULL CHECK (role IN ({", ".join(f"'{r}'" for r in ROLES)})),
        created_by  uuid,
        created_at  timestamptz NOT NULL DEFAULT now(),
        expires_at  timestamptz NOT NULL,
        accepted_at timestamptz,
        accepted_by uuid
    )
    """,
    "CREATE INDEX IF NOT EXISTS invitations_pending_by_email ON invitations (lower(email)) WHERE accepted_at IS NULL",
    """
    CREATE TABLE IF NOT EXISTS clinic_schedules (
        org_id            uuid PRIMARY KEY REFERENCES organizations ON DELETE CASCADE,
        timezone          text NOT NULL DEFAULT 'Asia/Kolkata',
        slot_minutes      integer NOT NULL DEFAULT 15,
        patients_per_slot integer NOT NULL DEFAULT 1,
        days_ahead        integer NOT NULL DEFAULT 14,
        opening_hours     jsonb NOT NULL DEFAULT '[null, null, null, null, null, null, null]',
        updated_at        timestamptz NOT NULL DEFAULT now(),
        updated_by        uuid
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS doctor_availability (
        org_id     uuid NOT NULL REFERENCES organizations ON DELETE CASCADE,
        doctor_id  uuid NOT NULL REFERENCES neon_auth."user" (id) ON DELETE CASCADE,
        weekly     jsonb NOT NULL DEFAULT '[[], [], [], [], [], [], []]',
        days_off   date[] NOT NULL DEFAULT '{}',
        updated_at timestamptz NOT NULL DEFAULT now(),
        updated_by uuid,
        PRIMARY KEY (org_id, doctor_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS appointments (
        appointment_id  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id          uuid NOT NULL REFERENCES organizations ON DELETE CASCADE,
        doctor_id       uuid NOT NULL REFERENCES neon_auth."user" (id) ON DELETE CASCADE,
        patient_user_id uuid NOT NULL REFERENCES neon_auth."user" (id) ON DELETE CASCADE,
        case_id         uuid REFERENCES cases ON DELETE SET NULL,
        starts_at       timestamptz NOT NULL,
        ends_at         timestamptz NOT NULL,
        status          text NOT NULL DEFAULT 'booked',
        created_at      timestamptz NOT NULL DEFAULT now(),
        cancelled_at    timestamptz,
        cancelled_by    uuid
    )
    """,
    "CREATE INDEX IF NOT EXISTS appointments_by_doctor ON appointments (org_id, doctor_id, starts_at) WHERE status = 'booked'",
    "CREATE INDEX IF NOT EXISTS appointments_by_org_day ON appointments (org_id, starts_at)",
    "CREATE INDEX IF NOT EXISTS appointments_by_patient ON appointments (patient_user_id, starts_at DESC)",
    "CREATE INDEX IF NOT EXISTS appointments_by_case ON appointments (case_id) WHERE status = 'booked'",
    # 'referred': a doctor moved this (upcoming) visit to another doctor, see referrals;
    # 'completed': the visit took place (marked by the clinic, or when a doctor signs a
    # prescription for the case that day); a patient cannot cancel it
    "ALTER TABLE appointments DROP CONSTRAINT IF EXISTS appointments_status_check",
    "ALTER TABLE appointments ADD CONSTRAINT appointments_status_check CHECK (status IN ('booked', 'cancelled', 'referred', 'completed'))",
    "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS completed_at timestamptz",
    "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS completed_by uuid",
    """
    CREATE TABLE IF NOT EXISTS referrals (
        referral_id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        case_id             uuid REFERENCES cases ON DELETE SET NULL,
        patient_user_id     uuid NOT NULL REFERENCES neon_auth."user" (id) ON DELETE CASCADE,
        from_appointment_id uuid NOT NULL REFERENCES appointments ON DELETE CASCADE,
        to_appointment_id   uuid NOT NULL REFERENCES appointments ON DELETE CASCADE,
        from_org_id         uuid NOT NULL REFERENCES organizations ON DELETE CASCADE,
        from_doctor_id      uuid NOT NULL,
        to_org_id           uuid NOT NULL REFERENCES organizations ON DELETE CASCADE,
        to_doctor_id        uuid NOT NULL,
        note                text NOT NULL,
        created_at          timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS referrals_from ON referrals (from_appointment_id)",
    "CREATE INDEX IF NOT EXISTS referrals_to ON referrals (to_appointment_id)",
    # a clinic's contact details, printed on its prescriptions
    "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS address text NOT NULL DEFAULT ''",
    "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS phone text NOT NULL DEFAULT ''",
    "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS email text NOT NULL DEFAULT ''",
    "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS registration_number text NOT NULL DEFAULT ''",
    # a patient's details for prescriptions: filled in by the patient, or saved from the
    # prescriptions doctors sign (so the next one starts with them)
    """
    CREATE TABLE IF NOT EXISTS patient_profiles (
        user_id       uuid PRIMARY KEY REFERENCES neon_auth."user" (id) ON DELETE CASCADE,
        date_of_birth date,
        sex           text NOT NULL DEFAULT '' CHECK (sex IN ('', 'female', 'male', 'other')),
        weight_kg     text NOT NULL DEFAULT '',
        phone         text NOT NULL DEFAULT '',
        address       text NOT NULL DEFAULT '',
        abha_number   text NOT NULL DEFAULT '',
        updated_at    timestamptz NOT NULL DEFAULT now(),
        updated_by    uuid
    )
    """,
    # a doctor's details for prescriptions (registration number, council, qualification)
    """
    CREATE TABLE IF NOT EXISTS doctor_profiles (
        user_id              uuid PRIMARY KEY REFERENCES neon_auth."user" (id) ON DELETE CASCADE,
        qualification        text NOT NULL DEFAULT '',
        specialty            text NOT NULL DEFAULT '',
        registration_number  text NOT NULL DEFAULT '',
        registration_council text NOT NULL DEFAULT '',
        updated_at           timestamptz NOT NULL DEFAULT now()
    )
    """,
    # prescriptions are medical records: no cascading deletes from cases or clinics; the
    # prescriber's details are copied into `prescriber` when it is signed
    """
    CREATE TABLE IF NOT EXISTS prescriptions (
        prescription_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        case_id         uuid NOT NULL REFERENCES cases,
        patient_user_id uuid,
        org_id          uuid NOT NULL REFERENCES organizations,
        doctor_id       uuid NOT NULL,
        status          text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'signed', 'void')),
        content         jsonb NOT NULL,
        prescriber      jsonb,
        created_at      timestamptz NOT NULL DEFAULT now(),
        updated_at      timestamptz NOT NULL DEFAULT now(),
        signed_at       timestamptz,
        voided_at       timestamptz,
        void_reason     text
    )
    """,
    "CREATE INDEX IF NOT EXISTS prescriptions_by_case ON prescriptions (case_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS prescriptions_by_patient ON prescriptions (patient_user_id, signed_at DESC) WHERE status <> 'draft'",
    # a patient's past lab reports and prescriptions (photo or PDF); `markdown` is what OCR read,
    # `summary` the short version for the doctor. Deleted with the patient.
    """
    CREATE TABLE IF NOT EXISTS patient_documents (
        document_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        patient_user_id uuid NOT NULL REFERENCES neon_auth."user" (id) ON DELETE CASCADE,
        kind            text NOT NULL DEFAULT 'other' CHECK (kind IN ('lab_report', 'prescription', 'other')),
        title           text NOT NULL DEFAULT '',
        content_type    text NOT NULL,
        size_bytes      integer NOT NULL,
        file            bytea NOT NULL,
        status          text NOT NULL DEFAULT 'processing' CHECK (status IN ('processing', 'ready', 'failed')),
        error           text,
        markdown        text,
        summary         jsonb,
        created_at      timestamptz NOT NULL DEFAULT now(),
        started_at      timestamptz NOT NULL DEFAULT now(),
        processed_at    timestamptz
    )
    """,
    "CREATE INDEX IF NOT EXISTS patient_documents_by_patient ON patient_documents (patient_user_id, created_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS audit_log (
        id      bigserial PRIMARY KEY,
        at      timestamptz NOT NULL DEFAULT now(),
        user_id uuid,
        action  text NOT NULL,
        case_id uuid,
        org_id  uuid,
        detail  jsonb
    )
    """,
]

_pool = None
_pool_lock = asyncio.Lock()


def _dsn(url):
    """The connection string asyncpg gets.

    Neon's string ends with sslmode=require&channel_binding=require: asyncpg understands
    sslmode but would pass channel_binding to the server as a setting and fail, so it goes.

    A pooled address (host "ep-...-pooler.<region>...") becomes the direct one: this process
    keeps its own small pool, and only on a direct connection can asyncpg cache its prepared
    queries. Through the pooler every query with parameters takes two round trips instead of
    one, which doubles the wait when the database is far away (measured: 291 ms vs 129 ms)."""
    parts = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parts.query) if key != "channel_binding"]
    netloc = parts.netloc.replace("-pooler.", ".", 1)
    return urlunsplit(parts._replace(netloc=netloc, query=urlencode(query)))


async def _json_columns(connection):
    await connection.set_type_codec("jsonb", schema="pg_catalog", format="text",
                                    encoder=lambda value: json.dumps(value, ensure_ascii=False),
                                    decoder=json.loads)


async def _no_session_reset(connection):
    """Replaces asyncpg's default reset when a connection goes back to the pool: that runs
    RESET ALL / CLOSE ALL / UNLISTEN / pg_advisory_unlock_all and makes the caller wait one
    round trip for it, after every query. This app leaves nothing in a session to reset (no
    SET, LISTEN, cursors or session-level locks; its locks end with their transaction), and
    asyncpg still rolls back a transaction left open."""


async def pool():
    """The shared connection pool (created, and the tables made, on first use)."""
    global _pool
    async with _pool_lock:
        if _pool is None:
            url = os.getenv("DATABASE_URL")  # read here, so .env may be loaded after import
            if not url:
                raise RuntimeError("DATABASE_URL is not set (the Neon connection string, in .env)")
            new_pool = await asyncpg.create_pool(
                _dsn(url),
                # 3 ready connections: a page's queries go out together, one per connection,
                # and opening a connection to a far database costs several round trips
                min_size=3,
                max_size=8,
                # Neon suspends an idle database after 5 minutes; drop idle connections before that
                max_inactive_connection_lifetime=280,
                init=_json_columns,
                reset=_no_session_reset,
            )
            async with new_pool.acquire() as connection:
                # one script, one round trip (the database may be far away: each trip counts)
                await connection.execute(";\n".join(SCHEMA))
            _pool = new_pool
    return _pool


async def audit(user_id, action, case_id=None, org_id=None, detail=None):
    """Record who did what (views and changes of patient data, role changes)."""
    def as_uuid(value):
        return uuid.UUID(str(value)) if value is not None else None
    database = await pool()
    await database.execute(
        "INSERT INTO audit_log (user_id, action, case_id, org_id, detail) VALUES ($1, $2, $3, $4, $5)",
        as_uuid(user_id), action, as_uuid(case_id), as_uuid(org_id), detail,
    )


_background = set()


def audit_later(user_id, action, case_id=None, org_id=None, detail=None):
    """audit() without making the caller wait for the database: the answer goes out while
    the entry is written (one round trip less for every request). Failures are printed;
    close() waits for entries still being written."""
    async def write():
        try:
            await audit(user_id, action, case_id, org_id, detail)
        except Exception as e:
            print(f"\n⚠️ Could not write audit entry {action}: {e!r}")
    task = asyncio.get_running_loop().create_task(write())
    _background.add(task)
    task.add_done_callback(_background.discard)


async def close():
    global _pool
    if _background:
        await asyncio.gather(*_background, return_exceptions=True)  # audit entries still being written
    if _pool is not None:
        await _pool.close()
        _pool = None
