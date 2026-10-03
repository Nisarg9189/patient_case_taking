"""A patient's past lab reports and prescriptions: a photo or PDF each, read by OCR and
summarised for the doctor (patient-nlp/document_ocr.py).

  POST   /api/me/documents?kind=&title=&date=  add one (the file itself is the request body);
                                             kind: lab_report | prescription | other; date: the day
                                             on the document (YYYY-MM-DD; else read from it)
  PATCH  /api/me/documents/{id}              change its date or name
  GET    /api/me/documents                   the patient's history, newest date first: their uploads
                                             and the prescriptions doctors signed for them (no files)
  GET    /api/me/documents/{id}/file         the original photo or PDF
  POST   /api/me/documents/{id}/retry        read it again (after a failure)
  DELETE /api/me/documents/{id}
  GET    /api/cases/{case_id}/documents      the same history, with the text and summary, for whoever
                                             may see that case (the patient, or a doctor or nurse of
                                             its clinic or of a clinic where it is booked)
  GET    /api/cases/{case_id}/documents/{id}/file

The file is read in the background after the upload: status is "processing", then "ready" (the
summary and the text read are filled in) or "failed". Staff views are written to the audit log.

The patient chooses what each consultation shares (cases.shared_documents): the ones ticked when
they start it, changeable afterwards. A doctor sees only those, plus the prescriptions written for
that case itself; a case with no recorded choice (older ones) shows the whole history.

  GET    /api/cases/{case_id}/shared-documents   the ids shared for this consultation (null: all); its patient
  PUT    /api/cases/{case_id}/shared-documents   {"document_ids": [...]}: change them

Prescriptions doctors sign (diagnosis, medicines, advice) are part of the same history without
being copied: they are read from the prescriptions table (signed, or voided), so they appear on
the day they were signed, for past ones too. They have no file.
"""
import asyncio
import datetime
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

import db
import document_ocr
from auth import User, current_user

router = APIRouter(prefix="/api")

MAX_BYTES = 10 * 1024 * 1024
MAX_DOCUMENTS = 20
MAX_SHARED = 100
KINDS = ("lab_report", "prescription", "other")
CLINICAL_ROLES = ("doctor", "nurse")

# a document still "processing" this long after it started is shown as failed (the server may
# have restarted while reading it); the patient can press Retry
_STALE = "10 minutes"
_STATUS = f"""
    CASE WHEN d.status = 'processing' AND d.started_at < now() - interval '{_STALE}' THEN 'failed' ELSE d.status END AS status,
    CASE WHEN d.status = 'processing' AND d.started_at < now() - interval '{_STALE}'
         THEN 'Reading took too long. Press Retry.' ELSE d.error END AS error"""
_LIGHT = (
    "d.document_id, d.kind, d.title, d.content_type, d.size_bytes, d.created_at, d.processed_at, d.summary, "
    "d.document_date, coalesce(d.document_date, (d.created_at AT TIME ZONE 'Asia/Kolkata')::date) AS date, "
    f"'upload' AS source, {_STATUS}"
)

_tasks = set()
_reading = asyncio.Semaphore(3)   # at most this many files are read at once


def _sniff(data: bytes):
    """The file's type from its first bytes (the browser's word is not trusted); None if unsupported."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    return None


def _uuid(value):
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise HTTPException(404, "No such document")


_FREQUENCY = {"once_daily": "once a day", "twice_daily": "twice a day", "three_times_daily": "three times a day",
              "four_times_daily": "four times a day", "every_6_hours": "every 6 hours", "every_8_hours": "every 8 hours",
              "at_bedtime": "at bedtime", "once_weekly": "once a week", "as_needed": "when needed",
              "stat": "once, immediately", "other": ""}
_TIMING = {"after_food": "after food", "before_food": "before food", "with_food": "with food",
           "empty_stomach": "on an empty stomach", "any": ""}

# the signed (or voided) prescriptions of a patient: what the history shows of them
_RX = ("p.prescription_id, p.status, p.content, p.prescriber, p.signed_at, p.void_reason, "
       "(p.signed_at AT TIME ZONE 'Asia/Kolkata')::date AS date")


def _medicine_line(m):
    name = m.get("generic_name") or m.get("brand_name") or "Medicine"
    if m.get("brand_name") and m.get("generic_name"):
        name += f" ({m['brand_name']})"
    what = " ".join(x for x in (name, m.get("strength"), m.get("form")) if x)
    duration = ("ongoing" if m.get("duration_unit") == "ongoing"
                else f"for {m['duration_value']} {m.get('duration_unit', 'days')}" if m.get("duration_value") else "")
    how = ", ".join(x for x in (m.get("dose"), _FREQUENCY.get(m.get("frequency"), ""), _TIMING.get(m.get("timing"), ""),
                                duration) if x)
    return f"{what}: {how}" if how else what


def _issued(row):
    """A signed prescription as a history entry: the diagnosis, medicines and advice, in the
    same shape as an uploaded document's summary."""
    content, prescriber = row["content"] or {}, row["prescriber"] or {}
    doctor = prescriber.get("name") or "a doctor"
    clinic = prescriber.get("clinic_name") or ""
    voided = row["status"] == "void"
    points = []
    if voided:
        points.append(f"Voided by the doctor: {row['void_reason'] or 'no reason given'}")
    if content.get("diagnosis"):
        points.append(f"Diagnosis ({content.get('diagnosis_type', 'provisional')}): {content['diagnosis']}")
    points += [_medicine_line(m) for m in content.get("medicines", [])]
    if content.get("investigations"):
        points.append("Tests advised: " + "; ".join(content["investigations"]))
    if content.get("advice"):
        points.append(f"Advice: {content['advice'][:400]}")
    if content.get("follow_up_date") or content.get("follow_up_note"):
        points.append("Follow-up: " + " - ".join(x for x in (content.get("follow_up_date"), content.get("follow_up_note")) if x))
    return {
        "document_id": str(row["prescription_id"]), "source": "prescription", "kind": "prescription", "title": "",
        "content_type": None, "size_bytes": None, "created_at": row["signed_at"], "processed_at": row["signed_at"],
        "document_date": None, "date": row["date"], "status": "ready", "error": None, "voided": voided,
        "summary": {"document_type": "prescription", "date": None, "date_iso": row["date"].isoformat(),
                    "issued_by": f"{doctor}, {clinic}" if clinic else doctor,
                    "headline": f"{'Voided prescription' if voided else 'Prescription'} by {doctor}" + (f" at {clinic}" if clinic else ""),
                    "key_points": points},
        "markdown": None,
    }


def _timeline(uploads, prescriptions):
    """Uploads and signed prescriptions together, newest date first."""
    items = [dict(r, voided=False) for r in uploads] + [_issued(r) for r in prescriptions]
    for item in items:
        item.pop("own", None)
    items.sort(key=lambda i: (i["date"], i["created_at"]), reverse=True)
    return items


def _patient_only(user: User):
    if not user.has_role("patient"):
        raise HTTPException(403, "Only patients can add documents.")


def _past_date(text):
    """A YYYY-MM-DD date that is today or earlier, or None."""
    try:
        day = datetime.date.fromisoformat(str(text))
    except ValueError:
        return None
    return day if day <= datetime.date.today() + datetime.timedelta(days=1) else None


def _typed_date(text):
    """The date the patient typed; HTTP 400 when it is not a date or is in the future."""
    if not text:
        return None
    day = _past_date(text)
    if day is None:
        raise HTTPException(400, "Please give the date as it is on the document (not in the future).")
    return day


async def _process(document_id, data, content_type, kind):
    """Read the file, summarise it, store both. Never raises: the outcome goes into the row."""
    async with _reading:
        markdown = summary = found_date = None
        status, error = "ready", None
        try:
            markdown = await document_ocr.read_markdown(data, content_type)
            if len(markdown) < 20:
                raise document_ocr.OcrError("No text could be read. Try a clear, well-lit photo of the whole page.")
            try:
                summary = await document_ocr.summarize(markdown, kind)
                found_date = _past_date(summary.get("date_iso"))
            except Exception as e:   # the text is still useful to the doctor without its summary
                print(f"\n⚠️ Could not summarise document {document_id}: {e!r}")
                error = "The summary could not be written; the text read from the document is shown instead."
        except document_ocr.OcrError as e:
            markdown, status, error = None, "failed", str(e)
        except Exception as e:
            print(f"\n❌ Reading document {document_id} failed: {e!r}")
            markdown, status, error = None, "failed", "The document could not be read. Please try again."
        try:
            database = await db.pool()
            await database.execute(
                "UPDATE patient_documents SET status = $2, error = $3, markdown = $4, summary = $5, processed_at = now(), "
                "document_date = coalesce(document_date, $6) WHERE document_id = $1",
                document_id, status, error, markdown, summary, found_date,
            )
        except Exception as e:
            print(f"\n⚠️ Could not store the reading of document {document_id}: {e!r}")


def _start(document_id, data, content_type, kind):
    task = asyncio.get_running_loop().create_task(_process(document_id, data, content_type, kind))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


# ---- the patient's own documents

@router.post("/me/documents")
async def add_document(request: Request, kind: str = "other", title: str = "", date: str = "",
                       user: User = Depends(current_user)):
    _patient_only(user)
    if kind not in KINDS:
        raise HTTPException(400, f"kind must be one of {', '.join(KINDS)}")
    document_date = _typed_date(date)
    if not document_ocr.configured():
        raise HTTPException(503, "Reading documents is not set up on this server.")
    if int(request.headers.get("content-length") or 0) > MAX_BYTES:
        raise HTTPException(413, "The file is larger than 10 MB.")
    data = await request.body()
    if not data:
        raise HTTPException(400, "No file was sent.")
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "The file is larger than 10 MB.")
    content_type = _sniff(data)
    if content_type is None:
        raise HTTPException(415, "Please use a JPEG or PNG photo, or a PDF.")

    database = await db.pool()
    row = await database.fetchrow(
        f"""
        WITH d AS (
            INSERT INTO patient_documents (patient_user_id, kind, title, content_type, size_bytes, file, document_date)
            SELECT $1, $2, $3, $4, $5, $6, $8
            WHERE (SELECT count(*) FROM patient_documents WHERE patient_user_id = $1) < $7
            RETURNING *
        )
        SELECT {_LIGHT} FROM d
        """,
        uuid.UUID(user.id), kind, title.strip()[:120], content_type, len(data), data, MAX_DOCUMENTS, document_date,
    )
    if row is None:
        raise HTTPException(400, f"You can keep up to {MAX_DOCUMENTS} documents. Delete one first.")
    _start(row["document_id"], data, content_type, kind)
    db.audit_later(user.id, "add_document", detail={"kind": kind, "bytes": len(data)})
    return dict(row)


@router.get("/me/documents")
async def my_documents(user: User = Depends(current_user)):
    database = await db.pool()
    me = uuid.UUID(user.id)
    uploads, prescriptions = await asyncio.gather(
        database.fetch(f"SELECT {_LIGHT} FROM patient_documents d WHERE d.patient_user_id = $1", me),
        database.fetch(f"SELECT {_RX} FROM prescriptions p WHERE p.patient_user_id = $1 AND p.status IN ('signed', 'void')", me),
    )
    return _timeline(uploads, prescriptions)


class DocumentEdit(BaseModel):
    document_date: Optional[datetime.date] = None   # null clears it (the day it was added is used)
    title: Optional[str] = Field(None, max_length=120)


@router.patch("/me/documents/{document_id}")
async def edit_document(document_id: str, body: DocumentEdit, user: User = Depends(current_user)):
    changes = body.model_fields_set & {"document_date", "title"}
    if not changes:
        raise HTTPException(400, "Nothing to change.")
    if body.document_date is not None:
        _typed_date(body.document_date.isoformat())
    database = await db.pool()
    row = await database.fetchrow(
        f"""
        WITH d AS (
            UPDATE patient_documents SET
                document_date = CASE WHEN $3 THEN $4 ELSE document_date END,
                title = CASE WHEN $5 THEN $6 ELSE title END
            WHERE document_id = $1 AND patient_user_id = $2
            RETURNING *
        )
        SELECT {_LIGHT} FROM d
        """,
        _uuid(document_id), uuid.UUID(user.id),
        "document_date" in changes, body.document_date, "title" in changes, (body.title or "").strip()[:120],
    )
    if row is None:
        raise HTTPException(404, "No such document")
    return dict(row, voided=False)


def _file_response(row):
    return Response(content=bytes(row["file"]), media_type=row["content_type"],
                    headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
                             "Content-Disposition": "inline"})


@router.get("/me/documents/{document_id}/file")
async def my_document_file(document_id: str, user: User = Depends(current_user)):
    database = await db.pool()
    row = await database.fetchrow(
        "SELECT file, content_type FROM patient_documents WHERE document_id = $1 AND patient_user_id = $2",
        _uuid(document_id), uuid.UUID(user.id),
    )
    if row is None:
        raise HTTPException(404, "No such document")
    return _file_response(row)


@router.post("/me/documents/{document_id}/retry")
async def retry_document(document_id: str, user: User = Depends(current_user)):
    database = await db.pool()
    row = await database.fetchrow(
        f"""
        WITH d AS (
            UPDATE patient_documents SET status = 'processing', error = NULL, started_at = now()
            WHERE document_id = $1 AND patient_user_id = $2
              AND (status = 'failed' OR (status = 'processing' AND started_at < now() - interval '{_STALE}'))
            RETURNING *
        )
        SELECT {_LIGHT}, d.file AS file_bytes FROM d
        """,
        _uuid(document_id), uuid.UUID(user.id),
    )
    if row is None:
        raise HTTPException(404, "Nothing to retry for this document")
    row = dict(row)
    _start(row["document_id"], bytes(row.pop("file_bytes")), row["content_type"], row["kind"])
    return row


@router.delete("/me/documents/{document_id}")
async def delete_document(document_id: str, user: User = Depends(current_user)):
    database = await db.pool()
    deleted = await database.fetchval(
        "DELETE FROM patient_documents WHERE document_id = $1 AND patient_user_id = $2 RETURNING document_id",
        _uuid(document_id), uuid.UUID(user.id),
    )
    if deleted is None:
        raise HTTPException(404, "No such document")
    db.audit_later(user.id, "delete_document", detail={"document_id": str(deleted)})
    return {"ok": True}


# ---- the documents of a case's patient, for whoever may see that case (same rule as GET /api/cases/{id})

_CASE_ACCESS = """
    c.case_id = $1 AND (
        c.patient_user_id = $2
        OR c.org_id = ANY($3::uuid[])
        OR EXISTS (SELECT 1 FROM appointments a
                   WHERE a.case_id = c.case_id AND a.status IN ('booked', 'referred', 'completed')
                     AND a.org_id = ANY($3::uuid[])))"""


# what of the patient's history a case shares: all of it with the patient themselves, or when no choice
# was recorded; else the chosen ones (and, for prescriptions, those written for this very case)
_SHARED_UPLOAD = "(c.patient_user_id = $2 OR c.shared_documents IS NULL OR d.document_id = ANY(c.shared_documents))"
_SHARED_RX = ("(c.patient_user_id = $2 OR c.shared_documents IS NULL OR p.case_id = c.case_id "
              "OR p.prescription_id = ANY(c.shared_documents))")


async def owned_ids(patient_id, ids):
    """Those of these document or prescription ids that belong to this patient (as UUIDs)."""
    keys = []
    for value in list(ids)[:MAX_SHARED]:
        try:
            keys.append(uuid.UUID(str(value)))
        except ValueError:
            continue
    if not keys:
        return []
    database = await db.pool()
    rows = await database.fetch(
        """
        SELECT document_id AS id FROM patient_documents WHERE patient_user_id = $1 AND document_id = ANY($2::uuid[])
        UNION
        SELECT prescription_id FROM prescriptions
        WHERE patient_user_id = $1 AND prescription_id = ANY($2::uuid[]) AND status IN ('signed', 'void')
        """,
        uuid.UUID(str(patient_id)), keys,
    )
    return [row["id"] for row in rows]


def _case_args(case_id, user: User):
    try:
        key = uuid.UUID(str(case_id))
    except ValueError:
        raise HTTPException(404, "No such case")
    return key, uuid.UUID(user.id), list(user.orgs_with(*CLINICAL_ROLES))


@router.get("/cases/{case_id}/documents")
async def case_documents(case_id: str, user: User = Depends(current_user)):
    key, me, clinics = _case_args(case_id, user)
    database = await db.pool()
    uploads, prescriptions = await asyncio.gather(
        database.fetch(
            f"""
            SELECT {_LIGHT}, d.markdown, (c.patient_user_id = $2) AS own
            FROM cases c JOIN patient_documents d ON d.patient_user_id = c.patient_user_id
            WHERE {_CASE_ACCESS} AND {_SHARED_UPLOAD}
            """,
            key, me, clinics,
        ),
        database.fetch(
            f"""
            SELECT {_RX}, (c.patient_user_id = $2) AS own
            FROM cases c JOIN prescriptions p ON p.patient_user_id = c.patient_user_id
            WHERE {_CASE_ACCESS} AND p.status IN ('signed', 'void') AND {_SHARED_RX}
            """,
            key, me, clinics,
        ),
    )
    items = _timeline(uploads, prescriptions)
    if items and not (uploads or prescriptions)[0]["own"]:   # a doctor or nurse looked, not the patient themselves
        db.audit_later(user.id, "view_documents", case_id=key, detail={"count": len(items)})
    return items


@router.get("/cases/{case_id}/documents/{document_id}/file")
async def case_document_file(case_id: str, document_id: str, user: User = Depends(current_user)):
    key, me, clinics = _case_args(case_id, user)
    database = await db.pool()
    row = await database.fetchrow(
        f"""
        SELECT d.file, d.content_type
        FROM cases c JOIN patient_documents d ON d.patient_user_id = c.patient_user_id
        WHERE d.document_id = $4 AND {_CASE_ACCESS} AND {_SHARED_UPLOAD}
        """,
        key, me, clinics, _uuid(document_id),
    )
    if row is None:
        raise HTTPException(404, "No such document")
    db.audit_later(user.id, "view_document_file", case_id=key, detail={"document_id": document_id})
    return _file_response(row)


class Sharing(BaseModel):
    document_ids: list[str] = Field(max_length=MAX_SHARED)


@router.get("/cases/{case_id}/shared-documents")
async def get_sharing(case_id: str, user: User = Depends(current_user)):
    key, me, _ = _case_args(case_id, user)
    database = await db.pool()
    row = await database.fetchrow("SELECT shared_documents FROM cases WHERE case_id = $1 AND patient_user_id = $2", key, me)
    if row is None:
        raise HTTPException(404, "No such case")
    shared = row["shared_documents"]
    return {"document_ids": None if shared is None else [str(i) for i in shared]}


@router.put("/cases/{case_id}/shared-documents")
async def set_sharing(case_id: str, body: Sharing, user: User = Depends(current_user)):
    key, me, _ = _case_args(case_id, user)
    chosen = await owned_ids(user.id, body.document_ids)
    database = await db.pool()
    saved = await database.fetchval(
        "UPDATE cases SET shared_documents = $3 WHERE case_id = $1 AND patient_user_id = $2 RETURNING true", key, me, chosen)
    if not saved:
        raise HTTPException(404, "No such case")
    db.audit_later(user.id, "share_documents", case_id=key, detail={"count": len(chosen)})
    return {"document_ids": [str(i) for i in chosen]}
