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
  source: 'upload' | 'prescription' // a photo the patient added, or a prescription a doctor signed on the platform
  kind: Kind
  title: string
  content_type: string | null
  size_bytes: number | null
  created_at: string
  date: string // YYYY-MM-DD: the day on the document (the day it was added, when unknown)
  document_date: string | null // the day the patient typed or the reading found
  status: 'processing' | 'ready' | 'failed'
  error: string | null
  voided: boolean // a prescription the doctor cancelled
  summary: DocumentSummary | null
  markdown?: string | null // only in the doctor's view
}

const MAX_SIDE = 2200 // photos are shrunk to this many pixels on the long side before they are sent

// "Fri, 3 Oct 2026" for a YYYY-MM-DD date
function dayLabel(date: string) {
  return new Date(`${date}T00:00:00`).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })
}

// the newest-first list as one group per date
function byDate(docs: PatientDocument[]) {
  const groups: { date: string; items: PatientDocument[] }[] = []
  for (const doc of [...docs].sort((a, b) => b.date.localeCompare(a.date))) { // stable: the server's order within a day stays
    const last = groups[groups.length - 1]
    if (last?.date === doc.date) last.items.push(doc)
    else groups.push({ date: doc.date, items: [doc] })
  }
  return groups
}

const today = () => new Date().toISOString().slice(0, 10)

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

// ---- one entry of the history

// a prescription a doctor signed on the platform: no photo, the doctor's own words
function IssuedBadge({ doc }: { doc: PatientDocument }) {
  return (
    <>
      <span className="chip-tag chip-good">Issued on this platform</span>
      {doc.voided && <span className="chip-tag chip-warn">Voided</span>}
    </>
  )
}

// a result the report itself marked high/low, or that the summary found outside the printed range
const FLAGGED = /\((?:[^)]*\b)?(?:H|L|high|low|abnormal|outside printed range)\b/i

function KeyPoints({ doc }: { doc: PatientDocument }) {
  if (!doc.summary || doc.summary.key_points.length === 0) return null
  return (
    <ul className="doc-points">
      {doc.summary.key_points.map((point, i) => (
        <li key={i} className={doc.kind === 'lab_report' && FLAGGED.test(point) ? 'doc-point-flag' : undefined}>{point}</li>
      ))}
    </ul>
  )
}

// ---- the patient's page

// Tick boxes on the history: which documents go to the doctor the patient is about to see.
export interface ShareControl {
  who: string // "Dr. Asha Mehta"
  isShared: (id: string) => boolean
  toggle: (id: string) => void
  onDocs: (docs: PatientDocument[]) => void // the list as loaded, so the page knows every id
}

export function MyDocuments({ heading, intro, share }: { heading?: string; intro?: string; share?: ShareControl } = {}) {
  const [docs, setDocs] = useState<PatientDocument[] | null>(null)
  const [adding, setAdding] = useState(false)
  const [kind, setKind] = useState<Kind>('lab_report')
  const [title, setTitle] = useState('')
  const [dated, setDated] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null)
  const [viewing, setViewing] = useState<string | null>(null)
  const [editing, setEditing] = useState<string | null>(null) // the document whose date is being changed
  const [newDate, setNewDate] = useState('')

  const load = useCallback(
    () => api<PatientDocument[]>('/api/me/documents').then((list) => { setDocs(list); share?.onDocs(list) }).catch(() => setDocs((old) => old ?? [])),
    [], // eslint-disable-line react-hooks/exhaustive-deps
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
      await send(`/api/me/documents?kind=${kind}&title=${encodeURIComponent(title.trim())}&date=${dated}`, {
        method: 'POST',
        headers: { 'Content-Type': body.type },
        body,
      })
      setAdding(false)
      setFile(null)
      setTitle('')
      setDated('')
      setMessage({ text: 'Added. It is being read now; the date is filled in from the document if you did not give one.', error: false })
      await load()
    } catch (e) {
      setMessage({ text: e instanceof Error ? e.message : 'Could not add the document.', error: true })
    } finally {
      setBusy(false)
    }
  }

  const act = async (run: () => Promise<unknown>, failure: string) => {
    setMessage(null)
    try {
      await run()
      await load()
    } catch (e) {
      setMessage({ text: e instanceof Error ? e.message : failure, error: true })
    }
  }

  const saveDate = (doc: PatientDocument) =>
    act(async () => {
      await api(`/api/me/documents/${doc.document_id}`, { method: 'PATCH', body: JSON.stringify({ document_date: newDate || null }) })
      setEditing(null)
    }, 'Could not change the date.')

  const remove = (doc: PatientDocument) => {
    if (!window.confirm(`Delete "${doc.title || KIND_LABEL[doc.kind]}"? Your doctor will no longer see it.`)) return
    void act(() => api(`/api/me/documents/${doc.document_id}`, { method: 'DELETE' }), 'Could not delete it.')
  }

  return (
    <div className="card">
      <div className="rx-card-head">
        <h3>{heading ?? 'Your reports and prescriptions'}</h3>
        {!adding && <button className="edit-button" onClick={() => setAdding(true)}>Add</button>}
      </div>
      <p className="muted small">
        {intro ?? 'Your lab reports and old prescriptions, with every prescription your doctors write, by date. Your doctor, and the hospitals you book with, read a short summary before your visit.'}
      </p>

      {share && docs && docs.length > 0 && (
        <p className="doc-share-summary">
          Sharing <strong>{docs.filter((d) => share.isShared(d.document_id)).length} of {docs.length}</strong> with {share.who}. Untick anything you
          would rather keep private.
        </p>
      )}

      {adding && (
        <form onSubmit={upload} className="doc-form">
          <div className="rx-grid rx-grid-3">
            <label>What is it?
              <select value={kind} onChange={(e) => setKind(e.target.value as Kind)}>
                {(Object.keys(KIND_LABEL) as Kind[]).map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
              </select>
            </label>
            <label>Date on it <span className="muted small">(optional)</span>
              <input type="date" value={dated} max={today()} onChange={(e) => setDated(e.target.value)} />
            </label>
            <label>Name <span className="muted small">(optional)</span>
              <input value={title} maxLength={120} placeholder="e.g. Blood test" onChange={(e) => setTitle(e.target.value)} />
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
        !adding && <p className="muted">Nothing here yet. Add a report or an old prescription.</p>
      ) : (
        <div className="doc-timeline">
          {byDate(docs).map((group) => (
            <section key={group.date} className="doc-day">
              <h4 className="doc-day-heading">{dayLabel(group.date)}</h4>
              <ul className="doc-list">
                {group.items.map((doc) => (
                  <li key={doc.document_id} className="doc-item">
                    <div className="doc-head">
                      {share && (
                        <input type="checkbox" className="doc-share-box" checked={share.isShared(doc.document_id)}
                               onChange={() => share.toggle(doc.document_id)} aria-label={`Share with ${share.who}`} />
                      )}
                      <span className="chip-tag">{KIND_LABEL[doc.kind]}</span>
                      {doc.source === 'prescription' && <IssuedBadge doc={doc} />}
                      <strong className="doc-title">{doc.title || doc.summary?.headline || KIND_LABEL[doc.kind]}</strong>
                    </div>
                    {doc.source === 'prescription' ? (
                      <details className="doc-text-details">
                        <summary className="muted small">Diagnosis and medicines</summary>
                        <KeyPoints doc={doc} />
                      </details>
                    ) : (
                      <>
                        {doc.status === 'processing' && <p className="doc-state"><span className="spinner" aria-hidden="true" /> Reading your document…</p>}
                        {doc.status === 'ready' && doc.summary && <p className="muted small">{doc.summary.headline}</p>}
                        {doc.error && <p className={doc.status === 'failed' ? 'review-note review-error' : 'muted small'}>{doc.error}</p>}
                        {editing === doc.document_id && (
                          <div className="doc-date-edit">
                            <input type="date" value={newDate} max={today()} onChange={(e) => setNewDate(e.target.value)} aria-label="Date on the document" />
                            <button className="save-button" onClick={() => void saveDate(doc)}>Save date</button>
                            <button className="edit-button" onClick={() => setEditing(null)}>Cancel</button>
                          </div>
                        )}
                        <div className="doc-actions">
                          <button className="edit-button" onClick={() => setViewing(viewing === doc.document_id ? null : doc.document_id)}>
                            {viewing === doc.document_id ? 'Hide' : 'View'}
                          </button>
                          <button className="edit-button" onClick={() => { setEditing(doc.document_id); setNewDate(doc.date) }}>Change date</button>
                          {doc.status === 'failed' && (
                            <button className="edit-button" onClick={() => void act(() => api(`/api/me/documents/${doc.document_id}/retry`, { method: 'POST' }), 'Could not try again.')}>Retry</button>
                          )}
                          <button className="edit-button" onClick={() => remove(doc)}>Delete</button>
                        </div>
                        {viewing === doc.document_id && doc.content_type && (
                          <FilePreview path={`/api/me/documents/${doc.document_id}/file`} contentType={doc.content_type}
                                       onClose={() => setViewing(null)} />
                        )}
                      </>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
      {message && <p className={message.error ? 'review-note review-error' : 'review-note'} role="status">{message.text}</p>}
    </div>
  )
}

// ---- the doctor's page: the patient's history by date, with the consultation summary

type Filter = 'all' | 'lab_report' | 'prescription'

export function CaseDocuments({ caseId }: { caseId: string }) {
  const [docs, setDocs] = useState<PatientDocument[] | null>(null)
  const [filter, setFilter] = useState<Filter>('all')
  const [viewing, setViewing] = useState<string | null>(null)

  useEffect(() => {
    api<PatientDocument[]>(`/api/cases/${caseId}/documents`).then(setDocs).catch(() => setDocs([]))
  }, [caseId])

  if (docs === null) return null
  const shown = docs.filter((d) => filter === 'all' || d.kind === filter)
  const count = (kind: Filter) => docs.filter((d) => kind === 'all' || d.kind === kind).length

  return (
    <div className="card">
      <h3>
        Past reports and prescriptions {docs.length > 0 && <span className="muted small">{docs.length}</span>}
      </h3>
      {docs.length === 0 ? (
        <p className="muted">The patient has not added any reports or prescriptions, and has no signed prescriptions yet.</p>
      ) : (
        <>
          <p className="muted small">
            The patient's history by date: what they added, and the prescriptions doctors signed for them. Uploaded ones are read from
            photos by software: check the original before relying on a value.
          </p>
          <div className="chips">
            {([['all', 'All'], ['lab_report', 'Lab reports'], ['prescription', 'Prescriptions']] as [Filter, string][]).map(([key, label]) => (
              <button key={key} className={filter === key ? 'chip chip-selected' : 'chip'} onClick={() => setFilter(key)}>
                {label} <span className="chip-note">{count(key)}</span>
              </button>
            ))}
          </div>
          {shown.length === 0 && <p className="muted">Nothing of this kind.</p>}
          <div className="doc-timeline">
            {byDate(shown).map((group) => (
              <section key={group.date} className="doc-day">
                <h4 className="doc-day-heading">{dayLabel(group.date)}</h4>
                <ul className="doc-list">
                  {group.items.map((doc) => (
                    <li key={doc.document_id} className="doc-item">
                      <div className="doc-head">
                        <span className="chip-tag">{KIND_LABEL[doc.kind]}</span>
                        {doc.source === 'prescription' && <IssuedBadge doc={doc} />}
                        <strong className="doc-title">{doc.title || doc.summary?.headline || KIND_LABEL[doc.kind]}</strong>
                        {doc.source === 'upload' && doc.summary?.issued_by && <span className="muted small">{doc.summary.issued_by}</span>}
                      </div>
                      {doc.status === 'processing' && <p className="doc-state"><span className="spinner" aria-hidden="true" /> Still being read…</p>}
                      {doc.status === 'failed' && <p className="muted small">{doc.error ?? 'This document could not be read.'}</p>}
                      {doc.summary && (
                        <>
                          {(doc.source === 'upload' || doc.title) && <p className="doc-headline">{doc.summary.headline}</p>}
                          <KeyPoints doc={doc} />
                        </>
                      )}
                      {doc.status === 'ready' && !doc.summary && doc.error && <p className="muted small">{doc.error}</p>}
                      {doc.source === 'upload' && doc.content_type && (
                        <div className="doc-actions">
                          <button className="edit-button" onClick={() => setViewing(viewing === doc.document_id ? null : doc.document_id)}>
                            {viewing === doc.document_id ? 'Hide original' : 'View original'}
                          </button>
                        </div>
                      )}
                      {viewing === doc.document_id && doc.content_type && (
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
              </section>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

// ---- what a booked visit shares: the patient can change it until the visit

export function SharedReports({ caseId }: { caseId: string }) {
  const [open, setOpen] = useState(false)
  const [docs, setDocs] = useState<PatientDocument[] | null>(null)
  const [chosen, setChosen] = useState<Set<string>>(new Set())
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null)

  useEffect(() => {
    if (!open) return
    let live = true
    Promise.all([
      api<PatientDocument[]>('/api/me/documents'),
      api<{ document_ids: string[] | null }>(`/api/cases/${caseId}/shared-documents`),
    ])
      .then(([list, shared]) => {
        if (!live) return
        setDocs(list)
        setChosen(new Set(shared.document_ids ?? list.map((d) => d.document_id))) // no choice recorded: everything was shared
      })
      .catch((e) => live && setMessage({ text: e instanceof Error ? e.message : 'Could not load your reports.', error: true }))
    return () => { live = false }
  }, [open, caseId])

  const toggle = (id: string) => {
    const next = new Set(chosen)
    if (!next.delete(id)) next.add(id)
    setChosen(next)
  }

  const save = async () => {
    setMessage(null)
    try {
      await api(`/api/cases/${caseId}/shared-documents`, { method: 'PUT', body: JSON.stringify({ document_ids: [...chosen] }) })
      setMessage({ text: 'Saved. Your doctor sees these.', error: false })
    } catch (e) {
      setMessage({ text: e instanceof Error ? e.message : 'Could not save.', error: true })
    }
  }

  return (
    <div className="shared-reports">
      <button className="edit-button" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        {open ? 'Hide reports' : 'Reports for the doctor'}
      </button>
      {open && (
        <div className="shared-panel">
          {docs === null && !message && <p className="muted small">Loading…</p>}
          {docs?.length === 0 && <p className="muted small">You have no reports or prescriptions yet. Add them on your home page.</p>}
          {docs && docs.length > 0 && (
            <>
              <p className="muted small">Tick what your doctor may read before this visit.</p>
              <ul className="shared-list">
                {docs.map((d) => (
                  <li key={d.document_id}>
                    <label>
                      <input type="checkbox" checked={chosen.has(d.document_id)} onChange={() => toggle(d.document_id)} />
                      <span className="chip-tag">{KIND_LABEL[d.kind]}</span>
                      <span>{d.title || d.summary?.headline || KIND_LABEL[d.kind]}</span>
                      <span className="muted small">{dayLabel(d.date)}</span>
                    </label>
                  </li>
                ))}
              </ul>
              <button className="save-button" onClick={() => void save()}>Save</button>
            </>
          )}
          {message && <p className={message.error ? 'review-note review-error' : 'review-note'} role="status">{message.text}</p>}
        </div>
      )}
    </div>
  )
}
