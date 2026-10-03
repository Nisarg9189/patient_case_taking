import { useCallback, useEffect, useState } from 'react'
import { ApiError, api, getToken } from './auth'

// A patient's past lab reports and prescriptions (a photo or PDF each). The server reads them
// with OCR and writes a short summary; the patient adds them here and the doctor reads the
// summaries with the consultation summary (backend/documents.py).

type Kind = 'lab_report' | 'prescription' | 'other'

const KIND_LABEL: Record<Kind, string> = { lab_report: 'Lab report', prescription: 'Prescription', other: 'Other document' }

interface DocumentSummary {
  document_type: string
  date: string | null
  issued_by: string | null
  headline: string
  key_points: string[]
}

export interface PatientDocument {
  document_id: string
  kind: Kind
  title: string
  content_type: string
  size_bytes: number
  created_at: string
  status: 'processing' | 'ready' | 'failed'
  error: string | null
  summary: DocumentSummary | null
  markdown?: string | null // only in the doctor's view
}

const MAX_SIDE = 2200 // photos are shrunk to this many pixels on the long side before they are sent

function day(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
}

// A phone photo is several MB; a JPEG of at most MAX_SIDE pixels is plenty for OCR. PDFs go as they are.
async function prepare(file: File): Promise<Blob> {
  if (file.type === 'application/pdf') return file
  try {
    const bitmap = await createImageBitmap(file)
    const scale = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height))
    const canvas = document.createElement('canvas')
    canvas.width = Math.round(bitmap.width * scale)
    canvas.height = Math.round(bitmap.height * scale)
    canvas.getContext('2d')!.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.85))
    if (blob) return blob
  } catch {
    // the browser could not decode it: send JPEG and PNG files as they are
  }
  if (file.type === 'image/jpeg' || file.type === 'image/png') return file
  throw new Error('Please use a JPEG or PNG photo, or a PDF.')
}

async function send(path: string, init: RequestInit) {
  const response = await fetch(path, { ...init, headers: { ...init.headers, Authorization: `Bearer ${await getToken()}` } })
  if (!response.ok) {
    const detail = (await response.json().catch(() => null))?.detail
    throw new ApiError(response.status, typeof detail === 'string' ? detail : `Request failed (${response.status})`)
  }
  return response
}

// The original photo or PDF, shown in the page (the request needs the sign-in token, so the
// browser cannot simply open the address).
function FilePreview({ path, contentType, onClose }: { path: string; contentType: string; onClose: () => void }) {
  const [url, setUrl] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let objectUrl: string | null = null
    let cancelled = false
    send(path, {})
      .then((response) => response.blob())
      .then((blob) => {
        if (cancelled) return
        objectUrl = URL.createObjectURL(blob)
        setUrl(objectUrl)
      })
      .catch(() => !cancelled && setFailed(true))
    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [path])

  return (
    <div className="doc-preview">
      {failed ? (
        <p className="muted">Could not load the file.</p>
      ) : !url ? (
        <p className="muted">Loading…</p>
      ) : contentType === 'application/pdf' ? (
        <iframe title="Document" src={url} className="doc-preview-pdf" />
      ) : (
        <img src={url} alt="The document as the patient photographed it" />
      )}
      <button className="edit-button" onClick={onClose}>Close</button>
    </div>
  )
}

// ---- the patient's page

export function MyDocuments() {
  const [docs, setDocs] = useState<PatientDocument[] | null>(null)
  const [adding, setAdding] = useState(false)
  const [kind, setKind] = useState<Kind>('lab_report')
  const [title, setTitle] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null)
  const [viewing, setViewing] = useState<string | null>(null)

  const load = useCallback(
    () => api<PatientDocument[]>('/api/me/documents').then(setDocs).catch(() => setDocs((old) => old ?? [])),
    [],
  )
  useEffect(() => {
    void load()
  }, [load])

  const reading = docs?.some((d) => d.status === 'processing') ?? false
  useEffect(() => {
    if (!reading) return
    const timer = setInterval(() => void load(), 3000)
    return () => clearInterval(timer)
  }, [reading, load])

  const upload = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!file) return
    setBusy(true)
    setMessage(null)
    try {
      const body = await prepare(file)
      await send(`/api/me/documents?kind=${kind}&title=${encodeURIComponent(title.trim())}`, {
        method: 'POST',
        headers: { 'Content-Type': body.type },
        body,
      })
      setAdding(false)
      setFile(null)
      setTitle('')
      setMessage({ text: 'Added. It is being read now; this takes a few seconds.', error: false })
      await load()
    } catch (e) {
      setMessage({ text: e instanceof Error ? e.message : 'Could not add the document.', error: true })
    } finally {
      setBusy(false)
    }
  }

  const retry = async (id: string) => {
    setMessage(null)
    try {
      await api(`/api/me/documents/${id}/retry`, { method: 'POST' })
      await load()
    } catch (e) {
      setMessage({ text: e instanceof Error ? e.message : 'Could not try again.', error: true })
    }
  }

  const remove = async (doc: PatientDocument) => {
    if (!window.confirm(`Delete "${doc.title || KIND_LABEL[doc.kind]}"? Your doctor will no longer see it.`)) return
    setMessage(null)
    try {
      await api(`/api/me/documents/${doc.document_id}`, { method: 'DELETE' })
      await load()
    } catch (e) {
      setMessage({ text: e instanceof Error ? e.message : 'Could not delete it.', error: true })
    }
  }

  return (
    <div className="card">
      <div className="rx-card-head">
        <h3>Your reports and past prescriptions</h3>
        {!adding && <button className="edit-button" onClick={() => setAdding(true)}>Add</button>}
      </div>
      <p className="muted small">
        Take a photo of a lab report or an old prescription. Your doctor, and the hospitals you book with, can read a
        short summary of it before your visit.
      </p>

      {adding && (
        <form onSubmit={upload} className="doc-form">
          <div className="rx-grid rx-grid-3">
            <label>What is it?
              <select value={kind} onChange={(e) => setKind(e.target.value as Kind)}>
                {(Object.keys(KIND_LABEL) as Kind[]).map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
              </select>
            </label>
            <label className="span-2">Name <span className="muted small">(optional)</span>
              <input value={title} maxLength={120} placeholder="e.g. Blood test, March" onChange={(e) => setTitle(e.target.value)} />
            </label>
          </div>
          <label className="doc-file">Photo or PDF
            <input type="file" accept="image/*,application/pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </label>
          <div className="buttons">
            <button type="button" className="edit-button" onClick={() => { setAdding(false); setFile(null) }} disabled={busy}>Cancel</button>
            <button type="submit" className="save-button" disabled={!file || busy}>{busy ? 'Uploading…' : 'Upload'}</button>
          </div>
        </form>
      )}

      {docs === null ? (
        <p className="muted">Loading…</p>
      ) : docs.length === 0 ? (
        !adding && <p className="muted">Nothing added yet.</p>
      ) : (
        <ul className="doc-list">
          {docs.map((doc) => (
            <li key={doc.document_id} className="doc-item">
              <div className="doc-head">
                <span className="chip-tag">{KIND_LABEL[doc.kind]}</span>
                <strong className="doc-title">{doc.title || doc.summary?.headline || KIND_LABEL[doc.kind]}</strong>
                <span className="muted small">{day(doc.created_at)}</span>
              </div>
              {doc.status === 'processing' && (
                <p className="doc-state"><span className="spinner" aria-hidden="true" /> Reading your document…</p>
              )}
              {doc.status === 'ready' && doc.summary && <p className="muted small">{doc.summary.headline}</p>}
              {doc.error && <p className={doc.status === 'failed' ? 'review-note review-error' : 'muted small'}>{doc.error}</p>}
              <div className="doc-actions">
                <button className="edit-button" onClick={() => setViewing(viewing === doc.document_id ? null : doc.document_id)}>
                  {viewing === doc.document_id ? 'Hide' : 'View'}
                </button>
                {doc.status === 'failed' && <button className="edit-button" onClick={() => void retry(doc.document_id)}>Retry</button>}
                <button className="edit-button" onClick={() => void remove(doc)}>Delete</button>
              </div>
              {viewing === doc.document_id && (
                <FilePreview path={`/api/me/documents/${doc.document_id}/file`} contentType={doc.content_type}
                             onClose={() => setViewing(null)} />
              )}
            </li>
          ))}
        </ul>
      )}
      {message && <p className={message.error ? 'review-note review-error' : 'review-note'} role="status">{message.text}</p>}
    </div>
  )
}

// ---- the doctor's page: with the consultation summary

// a result the report itself marked high/low, or that the summary found outside the printed range
const FLAGGED = /\((?:[^)]*\b)?(?:H|L|high|low|abnormal|outside printed range)\b/i

export function CaseDocuments({ caseId }: { caseId: string }) {
  const [docs, setDocs] = useState<PatientDocument[] | null>(null)
  const [viewing, setViewing] = useState<string | null>(null)

  useEffect(() => {
    api<PatientDocument[]>(`/api/cases/${caseId}/documents`).then(setDocs).catch(() => setDocs([]))
  }, [caseId])

  if (docs === null) return null

  return (
    <div className="card">
      <h3>
        Past reports and prescriptions {docs.length > 0 && <span className="muted small">{docs.length}</span>}
      </h3>
      {docs.length === 0 ? (
        <p className="muted">The patient has not added any reports or prescriptions.</p>
      ) : (
        <>
          <p className="muted small">
            Read from the patient's own photos by software. Check the original before relying on a value.
          </p>
          <ul className="doc-list">
            {docs.map((doc) => (
              <li key={doc.document_id} className="doc-item">
                <div className="doc-head">
                  <span className="chip-tag">{KIND_LABEL[doc.kind]}</span>
                  <strong className="doc-title">{doc.title || doc.summary?.headline || KIND_LABEL[doc.kind]}</strong>
                  <span className="muted small">
                    {[doc.summary?.date, doc.summary?.issued_by].filter(Boolean).join(' · ') || `added ${day(doc.created_at)}`}
                  </span>
                </div>
                {doc.status === 'processing' && <p className="doc-state"><span className="spinner" aria-hidden="true" /> Still being read…</p>}
                {doc.status === 'failed' && <p className="muted small">{doc.error ?? 'This document could not be read.'}</p>}
                {doc.summary && (
                  <>
                    <p className="doc-headline">{doc.summary.headline}</p>
                    {doc.summary.key_points.length > 0 && (
                      <ul className="doc-points">
                        {doc.summary.key_points.map((point, i) => (
                          <li key={i} className={FLAGGED.test(point) ? 'doc-point-flag' : undefined}>{point}</li>
                        ))}
                      </ul>
                    )}
                  </>
                )}
                {doc.status === 'ready' && !doc.summary && doc.error && <p className="muted small">{doc.error}</p>}
                <div className="doc-actions">
                  <button className="edit-button" onClick={() => setViewing(viewing === doc.document_id ? null : doc.document_id)}>
                    {viewing === doc.document_id ? 'Hide original' : 'View original'}
                  </button>
                </div>
                {viewing === doc.document_id && (
                  <FilePreview path={`/api/cases/${caseId}/documents/${doc.document_id}/file`} contentType={doc.content_type}
                               onClose={() => setViewing(null)} />
                )}
                {doc.markdown && (
                  <details className="doc-text-details">
                    <summary className="muted small">Text read from the document</summary>
                    <pre className="doc-text">{doc.markdown}</pre>
                  </details>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}
