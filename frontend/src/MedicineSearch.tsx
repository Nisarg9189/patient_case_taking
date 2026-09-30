import { useEffect, useId, useMemo, useRef, useState } from 'react'
import type { Form, Medicine, Route } from './rx'

// One medicine of the list (public/data/medicines.json, made by tools/medicines/build.py from
// the Jan Aushadhi list of 2110 generic medicines): the official name as listed, and the parts
// the prescription form needs.
export interface ListedMedicine {
  code: string
  name: string // "Paracetamol Paediatric Oral Suspension IP 125 mg per 5 ml"
  pack: string // "60 ml", "10's"
  generic: string // "PARACETAMOL", "ACECLOFENAC + PARACETAMOL"
  strength: string // "125 mg/5 ml", "100 mg + 325 mg"
  form: Form
  route: Route | null
  release: string // "sustained release", "gastro-resistant", ""
  aliases: string[] // what doctors also type: "ors"
}

interface Indexed {
  item: ListedMedicine
  words: string[]
  single: boolean
}

let listPromise: Promise<Indexed[]> | null = null

// the Indian Pharmacopoeia spells some names the older way (Amoxycillin, Cephalexin, Sulphate):
// compare "amoxicillin", "cefalexin", "sulfate" and the listed names alike
function spelling(word: string) {
  return word.replace(/ph/g, 'f').replace(/y/g, 'i').replace(/ae/g, 'e').replace(/oe/g, 'e')
}

function words(text: string) {
  return text.toLowerCase().split(/[^a-z0-9.%]+/).filter(Boolean).map(spelling)
}

// fetched once per page load (the browser caches the file), searched here: no request per key
function loadList(): Promise<Indexed[]> {
  listPromise ??= fetch('/data/medicines.json')
    .then((response) => {
      if (!response.ok) throw new Error(`medicine list: HTTP ${response.status}`)
      return response.json() as Promise<{ items: ListedMedicine[] }>
    })
    .then(({ items }) =>
      items.map((item) => ({
        item,
        words: words(`${item.name} ${item.generic} ${item.strength} ${item.aliases.join(' ')}`),
        single: !item.generic.includes('+'),
      })),
    )
    .catch((error) => {
      listPromise = null // try again next time
      throw error
    })
  return listPromise
}

// every typed word must start a word of the medicine ("para 500" finds Paracetamol 500 mg);
// medicines whose generic name starts with the first word come first, single ones before combinations
function search(list: Indexed[], query: string, limit = 12) {
  const terms = words(query)
  if (terms.length === 0) return []
  const found: { entry: Indexed; score: number }[] = []
  for (const entry of list) {
    if (!terms.every((term) => entry.words.some((word) => word.startsWith(term)))) continue
    const generic = spelling(entry.item.generic.toLowerCase())
    const score = (generic.startsWith(terms[0]) ? 0 : 100) + (entry.single ? 0 : 50) + entry.item.name.length / 100
    found.push({ entry, score })
  }
  return found.sort((a, b) => a.score - b.score).slice(0, limit).map((f) => f.entry.item)
}

const USUAL_DOSE: Partial<Record<Form, string>> = {
  tablet: '1 tablet', capsule: '1 capsule', syrup: '5 ml', suspension: '5 ml', drops: '1 drop', inhaler: '2 puffs',
  sachet: '1 sachet', ointment: 'Apply a thin layer', cream: 'Apply a thin layer', gel: 'Apply a thin layer',
  lotion: 'Apply a thin layer', spray: '1 spray',
}

// the prescription fields a picked medicine fills in (the dose only when none is written yet)
export function fromListed(picked: ListedMedicine, current: Medicine): Partial<Medicine> {
  return {
    code: picked.code,
    generic_name: picked.release ? `${picked.generic} (${picked.release.toUpperCase()})` : picked.generic,
    strength: picked.strength || current.strength,
    form: picked.form,
    ...(picked.route ? { route: picked.route } : {}),
    ...(!current.dose && USUAL_DOSE[picked.form] ? { dose: USUAL_DOSE[picked.form] } : {}),
  }
}

// The generic-name field with search over the medicine list; typing a name that is not in the
// list is fine too (it is just not linked to the list).
export function MedicineSearch({
  value,
  code,
  onType,
  onPick,
}: {
  value: string
  code?: string
  onType: (text: string) => void
  onPick: (medicine: ListedMedicine) => void
}) {
  const [list, setList] = useState<Indexed[] | null>(null)
  const [failed, setFailed] = useState(false)
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const listId = useId()
  const box = useRef<HTMLDivElement>(null)

  const ensureList = () => {
    if (!list) loadList().then(setList).catch(() => setFailed(true))
  }

  const results = useMemo(() => (list && open ? search(list, value) : []), [list, open, value])
  const picked = code && list ? list.find((entry) => entry.item.code === code)?.item : undefined

  useEffect(() => {
    const close = (event: MouseEvent) => {
      if (box.current && !box.current.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [])

  const pick = (medicine: ListedMedicine) => {
    onPick(medicine)
    setOpen(false)
  }

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (!open || results.length === 0) return
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setActive((i) => Math.min(i + 1, results.length - 1))
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActive((i) => Math.max(i - 1, 0))
    } else if (event.key === 'Enter') {
      event.preventDefault()
      pick(results[active])
    } else if (event.key === 'Escape') {
      setOpen(false)
    }
  }

  return (
    <div className="med-search" ref={box}>
      <input
        value={value}
        placeholder="Search: e.g. paracetamol 500"
        role="combobox"
        aria-expanded={open && results.length > 0}
        aria-controls={listId}
        aria-autocomplete="list"
        autoComplete="off"
        onFocus={() => { ensureList(); setOpen(true) }}
        onChange={(e) => { ensureList(); onType(e.target.value.toUpperCase()); setOpen(true); setActive(0) }}
        onKeyDown={onKeyDown}
      />
      {picked && <span className="med-picked">Jan Aushadhi {picked.code} · {picked.name} · {picked.pack}</span>}
      {open && value.trim() && (
        <ul className="med-results" id={listId} role="listbox">
          {failed ? (
            <li className="med-empty">The medicine list could not be loaded. Type the generic name.</li>
          ) : !list ? (
            <li className="med-empty">Loading the medicine list…</li>
          ) : results.length === 0 ? (
            <li className="med-empty">Not in the Jan Aushadhi list: the name you typed is used as it is.</li>
          ) : (
            results.map((m, i) => (
              <li key={m.code} role="option" aria-selected={i === active}
                  className={i === active ? 'med-result med-result-active' : 'med-result'}
                  onMouseEnter={() => setActive(i)}
                  onMouseDown={(e) => { e.preventDefault(); pick(m) }}>
                <span className="med-result-name">{m.name}</span>
                <span className="med-result-meta">
                  {m.generic}{m.strength && ` · ${m.strength}`} · {m.form} · pack {m.pack}
                </span>
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  )
}
