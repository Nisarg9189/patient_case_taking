"""A patient's past lab reports and prescriptions: a photo or PDF each, read by OCR and
summarised for the doctor (patient-nlp/document_ocr.py).

  POST   /api/me/documents?kind=&title=      add one (the file itself is the request body);
                                             kind: lab_report | prescription | other
  GET    /api/me/documents                   the patient's documents (no files)
  GET    /api/me/documents/{id}/file         the original photo or PDF
  POST   /api/me/documents/{id}/retry        read it again (after a failure)
  DELETE /api/me/documents/{id}
  GET    /api/cases/{case_id}/documents      the patient's documents, with the text and summary, for
                                             whoever may see that case (the patient, or a doctor or
                                             nurse of its clinic or of a clinic where it is booked)
  GET    /api/cases/{case_id}/documents/{id}/file

The file is read in the background after the upload: status is "processing", then "ready" (the
summary and the text read are filled in) or "failed". Staff views are written to the audit log.
"""
import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response

import db
import document_ocr
from auth import User, current_user

router = APIRouter(prefix="/api")

MAX_BYTES = 10 * 1024 * 1024
MAX_DOCUMENTS = 20
KINDS = ("lab_report", "prescription", "other")
CLINICAL_ROLES = ("doctor", "nurse")

# a document still "processing" this long after it started is shown as failed (the server may
# have restarted while reading it); the patient can press Retry
_STALE = "10 minutes"
_STATUS = f"""
    CASE WHEN d.status = 'processing' AND d.started_at < now() - interval '{_STALE}' THEN 'failed' ELSE d.status END AS status,
    CASE WHEN d.status = 'processing' AND d.started_at < now() - interval '{_STALE}'
         THEN 'Reading took too long. Press Retry.' ELSE d.error END AS error"""
_LIGHT = f"d.document_id, d.kind, d.title, d.content_type, d.size_bytes, d.created_at, d.processed_at, d.summary, {_STATUS}"

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


def _patient_only(user: User):
    if not user.has_role("patient"):
        raise HTTPException(403, "Only patients can add documents.")


async def _process(document_id, data, content_type, kind):
    """Read the file, summarise it, store both. Never raises: the outcome goes into the row."""
    async with _reading:
        markdown = summary = None
        status, error = "ready", None
        try:
            markdown = await document_ocr.read_markdown(data, content_type)
            if len(markdown) < 20:
                raise document_ocr.OcrError("No text could be read. Try a clear, well-lit photo of the whole page.")
            try:
                summary = await document_ocr.summarize(markdown, kind)
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
                "UPDATE patient_documents SET status = $2, error = $3, markdown = $4, summary = $5, processed_at = now() "
                "WHERE document_id = $1",
                document_id, status, error, markdown, summary,
            )
        except Exception as e:
            print(f"\n⚠️ Could not store the reading of document {document_id}: {e!r}")


def _start(document_id, data, content_type, kind):
    task = asyncio.get_running_loop().create_task(_process(document_id, data, content_type, kind))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


# ---- the patient's own documents

@router.post("/me/documents")
async def add_document(request: Request, kind: str = "other", title: str = "", user: User = Depends(current_user)):
    _patient_only(user)
    if kind not in KINDS:
        raise HTTPException(400, f"kind must be one of {', '.join(KINDS)}")
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
            INSERT INTO patient_documents (patient_user_id, kind, title, content_type, size_bytes, file)
            SELECT $1, $2, $3, $4, $5, $6
            WHERE (SELECT count(*) FROM patient_documents WHERE patient_user_id = $1) < $7
            RETURNING *
        )
        SELECT {_LIGHT} FROM d
        """,
        uuid.UUID(user.id), kind, title.strip()[:120], content_type, len(data), data, MAX_DOCUMENTS,
    )
    if row is None:
        raise HTTPException(400, f"You can keep up to {MAX_DOCUMENTS} documents. Delete one first.")
    _start(row["document_id"], data, content_type, kind)
    db.audit_later(user.id, "add_document", detail={"kind": kind, "bytes": len(data)})
    return dict(row)


@router.get("/me/documents")
async def my_documents(user: User = Depends(current_user)):
    database = await db.pool()
    rows = await database.fetch(
        f"SELECT {_LIGHT} FROM patient_documents d WHERE d.patient_user_id = $1 ORDER BY d.created_at DESC",
        uuid.UUID(user.id),
    )
    return [dict(row) for row in rows]


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
    rows = await database.fetch(
        f"""
        SELECT {_LIGHT}, d.markdown, (c.patient_user_id = $2) AS own
        FROM cases c JOIN patient_documents d ON d.patient_user_id = c.patient_user_id
        WHERE {_CASE_ACCESS}
        ORDER BY d.created_at DESC
        """,
        key, me, clinics,
    )
    documents = [dict(row) for row in rows]
    if documents and not documents[0]["own"]:     # a doctor or nurse looked, not the patient themselves
        db.audit_later(user.id, "view_documents", case_id=key, detail={"count": len(documents)})
    for document in documents:
        document.pop("own")
    return documents


@router.get("/cases/{case_id}/documents/{document_id}/file")
async def case_document_file(case_id: str, document_id: str, user: User = Depends(current_user)):
    key, me, clinics = _case_args(case_id, user)
    database = await db.pool()
    row = await database.fetchrow(
        f"""
        SELECT d.file, d.content_type
        FROM cases c JOIN patient_documents d ON d.patient_user_id = c.patient_user_id
        WHERE d.document_id = $4 AND {_CASE_ACCESS}
        """,
        key, me, clinics, _uuid(document_id),
    )
    if row is None:
        raise HTTPException(404, "No such document")
    db.audit_later(user.id, "view_document_file", case_id=key, detail={"document_id": document_id})
    return _file_response(row)
