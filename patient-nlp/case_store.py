"""Finished interviews: one row per case in the "cases" table (see db.py).

Each row keeps the case record the interview produced exactly as it was (original_case,
checklist, conversation), the patient's edited summary sections beside it (review_sections),
the clinician summary (written by summary_agent.py: summary_sections, its structured
output, and summary, the same as text), and whose case it is
(patient_user_id, the Neon Auth user) in which clinic (org_id). A clinic where the patient
has booked a visit for the case (appointments) sees it too, and so does a hospital the case
was referred from or to (a referred visit keeps its hospital's access).

Used by the backend (store the case, save the patient's edits, list and show cases) and by
the summary worker (save the summary); either may come first, so the writes are upserts.
Who may see or change a case is decided by the caller (backend/api.py); the functions here
only apply the ownership rule for patient edits.
"""
import re
import uuid

import db

MAX_SECTIONS = 30
MAX_SECTION_CHARS = 2000

# the columns a case list shows
_LIST_COLUMNS = """
    c.case_id, c.finished_at, c.aborted, c.org_id,
    c.original_case -> 'chief_complaint' ->> 'text' AS complaint,
    c.reviewed_at IS NOT NULL AS reviewed,
    c.summary IS NOT NULL AS has_summary,
    u.name AS patient_name, u.email AS patient_email
"""


def _case_uuid(case_id):
    try:
        return uuid.UUID(str(case_id))
    except ValueError:
        return None


async def save_original(case_id, case, checklist, turns, aborted, reason, patient_user_id=None, org_id=None,
                        review=None):
    """Store the case record of a finished (or stopped) interview, with the patient's saved
    review sections if there are any (one statement: one round trip)."""
    sections = None
    if review:
        try:
            sections = clean_review(review)
        except InvalidReview as e:  # keep the case even if the review cannot be stored
            print(f"\n⚠️ Review of case {case_id} not stored: {e}")
    database = await db.pool()
    await database.execute(
        """
        INSERT INTO cases (case_id, finished_at, aborted, reason, original_case, checklist, conversation,
                           patient_user_id, org_id, review_sections, reviewed_at)
        VALUES ($1, now(), $2, $3, $4, $5, $6, $7, $8, $9, CASE WHEN $9::jsonb IS NULL THEN NULL ELSE now() END)
        ON CONFLICT (case_id) DO UPDATE SET
            finished_at = now(), aborted = $2, reason = $3, original_case = $4, checklist = $5,
            conversation = $6, patient_user_id = $7, org_id = $8,
            review_sections = coalesce($9, cases.review_sections),
            reviewed_at = CASE WHEN $9::jsonb IS NULL THEN cases.reviewed_at ELSE now() END
        """,
        uuid.UUID(case_id), aborted, reason, case, checklist, turns, patient_user_id, org_id, sections,
    )


# summary_output's fields (summary_agent.py), in the order shown, with their headings
SUMMARY_SECTIONS = [
    ("flags", "Flags"),
    ("presenting_complaint", "Presenting complaint"),
    ("history_of_presenting_complaint", "History of presenting complaint"),
    ("allergies", "Allergies"),
    ("current_medicines", "Current medicines"),
    ("past_medical_history", "Past medical history"),
    ("family_history", "Family history"),
    ("social_history", "Social history"),
    ("travel_contacts", "Travel / contacts"),
    ("vaccinations", "Vaccinations"),
    ("vitals_reported_by_patient", "Vitals reported by patient"),
    ("pertinent_negatives", "Pertinent negatives"),
]


def summary_text(sections):
    """The sections as "Heading: text" lines (empty ones left out; no flags: "Flags: none")."""
    lines = []
    for key, heading in SUMMARY_SECTIONS:
        value = sections.get(key)
        if key == "flags":
            flags = [f.strip() for f in (value or []) if f and f.strip()]
            lines.append(f"Flags: {'; '.join(flags) if flags else 'none'}")
        elif isinstance(value, str) and value.strip():
            lines.append(f"{heading}: {value.strip()}")
    return "\n".join(lines)


async def save_summary(case_id, summary):
    """Store the clinician summary of a case: summary_output's fields as a dict (stored as
    they are, and as text), or plain text. The summary worker may finish before the backend
    has stored the case itself."""
    if hasattr(summary, "model_dump"):
        summary = summary.model_dump()
    sections = summary if isinstance(summary, dict) else None
    text = summary_text(sections) if sections is not None else str(summary)
    database = await db.pool()
    await database.execute(
        """
        INSERT INTO cases (case_id, summary, summary_sections, summarized_at) VALUES ($1, $2, $3, now())
        ON CONFLICT (case_id) DO UPDATE SET summary = $2, summary_sections = $3, summarized_at = now()
        """,
        uuid.UUID(case_id), text, sections,
    )


async def load(case_id):
    """The stored case (with the patient's name and email) as a dict, or None."""
    key = _case_uuid(case_id)
    if key is None:
        return None
    database = await db.pool()
    row = await database.fetchrow(
        """
        SELECT c.*, u.name AS patient_name, u.email AS patient_email,
               ARRAY(SELECT a.org_id FROM appointments a WHERE a.case_id = c.case_id AND a.status IN ('booked', 'referred', 'completed'))
                   AS booked_org_ids
        FROM cases c LEFT JOIN neon_auth."user" u ON u.id = c.patient_user_id
        WHERE c.case_id = $1
        """,
        key,
    )
    return dict(row) if row else None


async def list_for_patient(patient_user_id, limit=50):
    database = await db.pool()
    rows = await database.fetch(
        f"""
        SELECT {_LIST_COLUMNS}
        FROM cases c LEFT JOIN neon_auth."user" u ON u.id = c.patient_user_id
        WHERE c.patient_user_id = $1 AND c.original_case IS NOT NULL
        ORDER BY c.finished_at DESC LIMIT $2
        """,
        patient_user_id, limit,
    )
    return [dict(row) for row in rows]


async def list_for_orgs(org_ids, limit=100):
    """Cases of these clinics' patients, and cases booked for a visit at these clinics."""
    database = await db.pool()
    rows = await database.fetch(
        f"""
        SELECT {_LIST_COLUMNS}
        FROM cases c LEFT JOIN neon_auth."user" u ON u.id = c.patient_user_id
        WHERE (c.org_id = ANY($1::uuid[])
               OR EXISTS (SELECT 1 FROM appointments a
                          WHERE a.case_id = c.case_id AND a.status IN ('booked', 'referred', 'completed')
                            AND a.org_id = ANY($1::uuid[])))
          AND c.original_case IS NOT NULL
        ORDER BY c.finished_at DESC LIMIT $2
        """,
        list(org_ids), limit,
    )
    return [dict(row) for row in rows]


class InvalidReview(ValueError):
    pass


def clean_review(sections):
    """The patient's review sections, checked and tidied; raises InvalidReview."""
    if not isinstance(sections, dict) or len(sections) > MAX_SECTIONS:
        raise InvalidReview(f"expected at most {MAX_SECTIONS} sections")
    cleaned = {}
    for label, text in sections.items():
        if not isinstance(label, str) or not isinstance(text, str):
            raise InvalidReview("section labels and texts must be text")
        if len(label) > 60 or len(text) > MAX_SECTION_CHARS:
            raise InvalidReview(f"a section is longer than {MAX_SECTION_CHARS} characters")
        cleaned[label.strip()] = re.sub(r"[ \t]+\n", "\n", text).strip()
    return cleaned


async def save_review(case_id, sections, patient_user_id):
    """Store the patient's edited summary: {section label: text}. Only the patient whose case
    it is can save it. Returns when it was saved (ISO time), or None (no such case of theirs)."""
    cleaned = clean_review(sections)

    key = _case_uuid(case_id)
    if key is None:
        return None
    database = await db.pool()
    saved_at = await database.fetchval(
        """
        UPDATE cases SET review_sections = $2, reviewed_at = now()
        WHERE case_id = $1 AND patient_user_id = $3 AND original_case IS NOT NULL
        RETURNING reviewed_at
        """,
        key, cleaned, patient_user_id,
    )
    return saved_at.isoformat() if saved_at else None


async def close():
    await db.close()

if __name__ == "__main__":
    pass