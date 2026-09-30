import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, type Me } from './auth'
import { dayKey, formatDay, formatTime, type Appointment, type Hours } from './booking'
import { CasePage, type CaseFull } from './CasesView'
import { CalendarIcon, ChevronLeft, ChevronRight, ClockIcon, MoreIcon, SearchIcon } from './icons'
import { PageHeader } from './PageHeader'
import { ReferDialog } from './ReferDialog'

const STAFF_ROLES = ['doctor', 'nurse', 'front_desk', 'clinic_admin']

interface PlanDoctor {
  doctor_id: string
  name: string
  email: string
  day_off: boolean
  hours: Hours[]
}

// one day of the clinic (GET /api/orgs/{org_id}/appointments?day=)
interface Day {
  timezone: string
  day: string // YYYY-MM-DD
  slot_minutes: number
  opening_hours: Hours | null
  doctors: PlanDoctor[]
  appointments: Appointment[]
}

type Tab = 'planner' | 'list'
type Range = 1 | 3 | 7
type Spacing = 15 | 30 | 60
type Group = 'date' | 'doctor'
type DoctorFilter = 'all' | 'booked' | 'mine'
type Action = { label: string; run: () => void; danger?: boolean }

const PX_PER_MINUTE: Record<Spacing, number> = { 15: 4, 30: 2.4, 60: 1.4 }

const STATUS_LABEL: Record<Appointment['status'], string> = {
  booked: 'Booked',
  cancelled: 'Cancelled',
  referred: 'Referred out',
  completed: 'Completed',
}

function minutes(hhmm: string) {
  const [h, m] = hhmm.split(':').map(Number)
  return h * 60 + m
}

function clock(total: number) {
  return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`
}

// minutes after midnight of this moment on the clinic's clock
function minutesOfDay(iso: string, timeZone: string) {
  const parts = new Intl.DateTimeFormat('en-GB', { timeZone, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
    .formatToParts(new Date(iso))
  const get = (type: string) => Number(parts.find((p) => p.type === type)?.value ?? 0)
  return get('hour') * 60 + get('minute')
}

// a day from an older backend may lack the planner fields: no doctor columns rather than a crash
function complete(day: Partial<Day> & Pick<Day, 'timezone' | 'day'>): Day {
  return {
    ...day,
    slot_minutes: day.slot_minutes ?? 30,
    opening_hours: day.opening_hours ?? null,
    doctors: day.doctors ?? [],
    appointments: day.appointments ?? [],
  }
}

function addDays(day: string, count: number) {
  const next = new Date(`${day}T12:00:00Z`)
  next.setUTCDate(next.getUTCDate() + count)
  return next.toISOString().slice(0, 10)
}

function longDate(day: string) {
  return new Date(`${day}T12:00:00Z`).toLocaleDateString([], { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' })
}

function shortDate(day: string) {
  return new Date(`${day}T12:00:00Z`).toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC' })
}

function initials(name: string) {
  const words = name.replace(/^dr\.?\s+/i, '').replace(/@.*$/, '').replace(/[^\p{L}\s]/gu, ' ').split(/\s+/).filter(Boolean)
  return ((words[0]?.[0] ?? '') + (words[1]?.[0] ?? '')).toUpperCase()
}

// Clinic staff: the clinic's appointments as a day planner (a column per doctor) or a list.
// Front desk and admins can cancel; doctors and nurses open the interview; doctors refer.
export function AppointmentsView({ me }: { me: Me }) {
  const [orgs, setOrgs] = useState<{ org_id: string; name: string }[]>([])
  const [orgId, setOrgId] = useState<string | null>(null)
  const [startDay, setStartDay] = useState<string | null>(null) // null: today in the clinic's time zone
  const [range, setRange] = useState<Range>(1)
  const [spacing, setSpacing] = useState<Spacing>(15)
  const [group, setGroup] = useState<Group>('date')
  const [tab, setTab] = useState<Tab>('planner')
  const [doctorFilter, setDoctorFilter] = useState<DoctorFilter | null>(null) // null: the role's default
  const [search, setSearch] = useState('')
  const [days, setDays] = useState<Day[] | null>(null)
  const [openCase, setOpenCase] = useState<CaseFull | null>(null)
  const [referring, setReferring] = useState<Appointment | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const datePicker = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const fromRoles = () => {
      const byId = new Map<string, string>()
      for (const m of me.memberships) if (STAFF_ROLES.includes(m.role)) byId.set(m.org_id, m.org_name)
      return [...byId].map(([org_id, name]) => ({ org_id, name }))
    }
    const load = me.platform_admin ? api<{ org_id: string; name: string }[]>('/api/orgs') : Promise.resolve(fromRoles())
    load
      .then((list) => {
        setOrgs(list)
        setOrgId(list[0]?.org_id ?? null)
      })
      .catch((e) => setError(e.message))
  }, [me])

  const roles = new Set(me.memberships.filter((m) => m.org_id === orgId).map((m) => m.role))
  const isDoctor = roles.has('doctor')
  const canCancel = me.platform_admin || roles.has('front_desk') || roles.has('clinic_admin')
  const clinical = roles.has('doctor') || roles.has('nurse')
  const filter: DoctorFilter = doctorFilter ?? (isDoctor ? 'mine' : 'all')

  const load = useCallback(async () => {
    if (!orgId) return
    // all the days in one request (each request costs database round trips)
    const query = new URLSearchParams({ days: String(range), ...(startDay ? { day: startDay } : {}) })
    const loaded = await api<{ days: Day[] } | Day>(`/api/orgs/${orgId}/appointments?${query}`)
    setDays(('days' in loaded ? loaded.days : [loaded]).map(complete))
  }, [orgId, startDay, range])

  useEffect(() => {
    load().catch((e) => setError(e.message))
  }, [load])

  const shift = (count: number) => days && setStartDay(addDays(days[0].day, count))

  const cancel = async (a: Appointment) => {
    if (!window.confirm(`Cancel the visit of ${a.patient_name || a.patient_email} at ${formatTime(a.starts_at, a.timezone)}?`)) return
    setError(null)
    try {
      await api(`/api/appointments/${a.appointment_id}`, { method: 'DELETE' })
      setNotice(`Visit of ${a.patient_name || a.patient_email} cancelled.`)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not cancel.')
    }
  }

  const markCompleted = async (a: Appointment) => {
    setError(null)
    try {
      await api(`/api/appointments/${a.appointment_id}/complete`, { method: 'POST' })
      setNotice(`Visit of ${a.patient_name || a.patient_email} marked as completed.`)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not mark it completed.')
    }
  }

  const open = async (caseId: string) => {
    setError(null)
    try {
      setOpenCase(await api<CaseFull>(`/api/cases/${caseId}`))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not open the case.')
    }
  }

  const matches = useCallback(
    (a: Appointment) => {
      const q = search.trim().toLowerCase()
      return !q || `${a.patient_name ?? ''} ${a.patient_email ?? ''} ${a.doctor_name ?? ''}`.toLowerCase().includes(q)
    },
    [search],
  )

  const actionsFor = (a: Appointment): Action[] => {
    const upcoming = new Date(a.ends_at).getTime() > Date.now()
    const actions: Action[] = []
    if (clinical && a.case_id) actions.push({ label: 'Open interview', run: () => void open(a.case_id!) })
    if (isDoctor && (a.status === 'booked' || a.status === 'completed')) {
      actions.push({ label: 'Refer to another doctor', run: () => { setNotice(null); setReferring(a) } })
    }
    if ((clinical || canCancel || roles.has('front_desk')) && a.status === 'booked') {
      actions.push({ label: 'Mark as completed', run: () => void markCompleted(a) })
    }
    if (canCancel && a.status === 'booked' && upcoming) actions.push({ label: 'Cancel visit', run: () => void cancel(a), danger: true })
    return actions
  }

  if (openCase) return <CasePage selected={openCase} onBack={() => setOpenCase(null)} backLabel="← Appointments" meId={me.id} />

  const first = days?.[0]
  const today = first ? dayKey(new Date().toISOString(), first.timezone) : null
  const shown = (days ?? [])
    .flatMap((d) => d.appointments.filter(matches).map((a) => ({ day: d, a })))
    .filter(({ a }) => filter !== 'mine' || a.doctor_id === me.id)
  const booked = shown.filter(({ a }) => a.status === 'booked').length

  return (
    <div className={tab === 'planner' ? 'page page-wide page-planner' : 'page page-wide'}>
      <PageHeader
        tabs={[['planner', 'Schedule'], ['list', 'List']]}
        active={tab}
        onTab={setTab}
        right={
          <label className="search">
            <SearchIcon size={18} />
            <input placeholder="Search patient or doctor…" value={search} onChange={(e) => setSearch(e.target.value)} />
          </label>
        }
      />

      <div className="filters">
        <label className="field">
          <span>Clinic</span>
          <select value={orgId ?? ''} onChange={(e) => { setOrgId(e.target.value); setStartDay(null) }} disabled={orgs.length < 2}>
            {orgs.map((o) => (
              <option key={o.org_id} value={o.org_id}>
                {o.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Doctors</span>
          <select value={filter} onChange={(e) => setDoctorFilter(e.target.value as DoctorFilter)}>
            <option value="all">All doctors</option>
            <option value="booked">With appointments only</option>
            {isDoctor && <option value="mine">Only me</option>}
          </select>
        </label>
        <span className="filters-spacer" />
        {days && <span className="count-pill">{booked} booked</span>}
      </div>

      <div className="filters filters-dates">
        <div className="date-title">
          <span className="muted small">{today ? `Today, ${longDate(today)}` : ' '}</span>
          <strong>
            {days
              ? days.length === 1
                ? longDate(days[0].day)
                : `${shortDate(days[0].day)} – ${longDate(days[days.length - 1].day)}`
              : '…'}
          </strong>
        </div>
        <div className="icon-buttons">
          <button className="icon-button" onClick={() => shift(-range)} disabled={!days} aria-label="Earlier">
            <ChevronLeft />
          </button>
          <button className="icon-button" onClick={() => shift(range)} disabled={!days} aria-label="Later">
            <ChevronRight />
          </button>
        </div>
        <button className="icon-button" aria-label="Pick a date" onClick={() => datePicker.current?.showPicker?.()}>
          <CalendarIcon />
          <input ref={datePicker} type="date" className="hidden-date" value={first?.day ?? ''} tabIndex={-1}
                 onChange={(e) => e.target.value && setStartDay(e.target.value)} />
        </button>
        <button className="link-button" onClick={() => setStartDay(null)}>
          Today
        </button>
        <div className="segmented" role="group" aria-label="Days shown">
          {([1, 3, 7] as Range[]).map((r) => (
            <button key={r} className={r === range ? 'segment segment-active' : 'segment'} onClick={() => setRange(r)}>
              {r} day{r > 1 ? 's' : ''}
            </button>
          ))}
        </div>
        {tab === 'planner' && (
          <>
            <label className="field">
              <span>Grid spacing</span>
              <select value={spacing} onChange={(e) => setSpacing(Number(e.target.value) as Spacing)}>
                <option value={15}>15 minutes</option>
                <option value={30}>30 minutes</option>
                <option value={60}>1 hour</option>
              </select>
            </label>
            {range > 1 && (
              <label className="field">
                <span>Group</span>
                <select value={group} onChange={(e) => setGroup(e.target.value as Group)}>
                  <option value="date">By date</option>
                  <option value="doctor">By doctor</option>
                </select>
              </label>
            )}
          </>
        )}
      </div>

      {error && <div className="notice">{error}</div>}
      {notice && <div className="notice notice-ok">{notice}</div>}
      {referring && (
        <ReferDialog
          appointment={referring}
          onClose={() => setReferring(null)}
          onReferred={(message) => {
            setReferring(null)
            setNotice(message)
            void load()
          }}
        />
      )}

      {days === null ? (
        <p className="muted">Loading…</p>
      ) : tab === 'planner' ? (
        <Planner days={days} spacing={spacing} group={group} filter={filter} me={me} matches={matches}
                 actionsFor={actionsFor} clinical={clinical} />
      ) : shown.length === 0 ? (
        <p className="muted empty">No appointments.</p>
      ) : (
        <div className="table-wrap">
          <table className="cases-table">
            <thead>
              <tr>
                <th>Date</th>
                <th>Time</th>
                <th>Patient</th>
                <th>Doctor</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {shown.map(({ day, a }) => (
                <tr key={a.appointment_id} className={a.status === 'cancelled' ? 'row-cancelled' : undefined}>
                  <td>{shortDate(day.day)}</td>
                  <td>
                    {formatTime(a.starts_at, a.timezone)} – {formatTime(a.ends_at, a.timezone)}
                  </td>
                  <td>
                    {a.patient_name || '—'}
                    <div className="muted small">{a.patient_email}</div>
                    <ReferralLines a={a} />
                  </td>
                  <td>{a.doctor_name ?? '—'}</td>
                  <td>
                    <span className={`chip-tag chip-${a.status}`}>{STATUS_LABEL[a.status]}</span>
                  </td>
                  <td className="actions">
                    {actionsFor(a).map((action) => (
                      <button key={action.label} className="link-button" onClick={action.run}>
                        {action.label}
                      </button>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function ReferralLines({ a }: { a: Appointment }) {
  return (
    <>
      {a.referred_from && (
        <div className="referral-line">
          Referred by {a.referred_from.doctor_name} ({a.referred_from.clinic_name})
          {a.referred_from.note && <div className="referral-note">“{a.referred_from.note}”</div>}
        </div>
      )}
      {a.referred_to && (
        <div className="referral-line">
          Referred to {a.referred_to.doctor_name} ({a.referred_to.clinic_name}),{' '}
          {formatDay(a.referred_to.starts_at, a.referred_to.timezone)} {formatTime(a.referred_to.starts_at, a.referred_to.timezone)}
        </div>
      )}
    </>
  )
}

interface Column {
  key: string
  day: Day
  doctor: PlanDoctor
  appointments: Appointment[]
}

// The day planner: time down the side, a column per doctor (per day), visits as cards.
// Hatched: the doctor does not see patients then.
function Planner({
  days,
  spacing,
  group,
  filter,
  me,
  matches,
  actionsFor,
  clinical,
}: {
  days: Day[]
  spacing: Spacing
  group: Group
  filter: DoctorFilter
  me: Me
  matches: (a: Appointment) => boolean
  actionsFor: (a: Appointment) => Action[]
  clinical: boolean
}) {
  const [menu, setMenu] = useState<string | null>(null)
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 60_000)
    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => {
    if (!menu) return
    const close = () => setMenu(null)
    window.addEventListener('click', close)
    return () => window.removeEventListener('click', close)
  }, [menu])

  const columns = useMemo(() => {
    const flat: Column[] = days.flatMap((day) => {
      const doctors = [...day.doctors]
      for (const a of day.appointments) {
        if (!doctors.some((d) => d.doctor_id === a.doctor_id)) {
          // no longer a doctor here, but has visits this day
          doctors.push({ doctor_id: a.doctor_id, name: a.doctor_name ?? 'Doctor', email: '', day_off: false, hours: [] })
        }
      }
      return doctors
        .map((doctor) => ({
          key: `${day.day}:${doctor.doctor_id}`,
          day,
          doctor,
          appointments: day.appointments.filter((a) => a.doctor_id === doctor.doctor_id && matches(a)),
        }))
        .filter((c) => (filter === 'mine' ? c.doctor.doctor_id === me.id : filter === 'booked' ? c.appointments.length > 0 : true))
    })
    if (group === 'doctor') {
      const order = [...new Set(flat.map((c) => c.doctor.doctor_id))]
      flat.sort((a, b) => order.indexOf(a.doctor.doctor_id) - order.indexOf(b.doctor.doctor_id) || a.day.day.localeCompare(b.day.day))
    }
    return flat
  }, [days, filter, group, matches, me.id])

  // the hours shown: from the earliest opening / doctor hours / visit to the latest, in whole hours
  const [start, end] = useMemo(() => {
    const starts: number[] = []
    const ends: number[] = []
    for (const day of days) {
      if (day.opening_hours) {
        starts.push(minutes(day.opening_hours[0]))
        ends.push(minutes(day.opening_hours[1]))
      }
      for (const d of day.doctors) {
        for (const [a, b] of d.hours) {
          starts.push(minutes(a))
          ends.push(minutes(b))
        }
      }
      for (const a of day.appointments) {
        const from = minutesOfDay(a.starts_at, day.timezone)
        starts.push(from)
        ends.push(from + Math.max(15, (Date.parse(a.ends_at) - Date.parse(a.starts_at)) / 60000))
      }
    }
    if (starts.length === 0) return [8 * 60, 18 * 60]
    return [Math.floor(Math.min(...starts) / 60) * 60, Math.min(24 * 60, Math.ceil(Math.max(...ends) / 60) * 60)]
  }, [days])

  const scale = PX_PER_MINUTE[spacing]
  const height = (end - start) * scale
  const ticks = Array.from({ length: Math.floor((end - start) / spacing) + 1 }, (_, i) => start + i * spacing)
  const timeZone = days[0].timezone
  const todayKey = dayKey(new Date(now).toISOString(), timeZone)
  const nowMinutes = minutesOfDay(new Date(now).toISOString(), timeZone)
  const showNow = days.some((d) => d.day === todayKey) && nowMinutes >= start && nowMinutes <= end

  if (columns.length === 0) {
    return <p className="muted empty">{filter === 'all' ? 'This clinic has no doctors yet.' : 'No appointments on these days.'}</p>
  }

  return (
    // One scroll area. The header row and the time column are each pinned as one piece, and
    // the grid lines are drawn once across all columns: few layers to repaint while scrolling.
    <div className="planner">
      <div className="planner-inner" style={{ ['--planner-columns' as string]: `64px repeat(${columns.length}, minmax(230px, 1fr))` }}>
      <div className="planner-header">
        <div className="planner-corner">{timeZone.split('/').pop()?.replace('_', ' ')}</div>
        {columns.map((c) => (
          <div key={c.key} className="planner-head">
            <span className="doctor-avatar">{initials(c.doctor.name)}</span>
            <div className="planner-head-text">
              <strong>{c.doctor.name}</strong>
              <span className="muted small">
                {days.length > 1 && `${shortDate(c.day.day)} · `}
                {c.doctor.day_off ? 'Day off' : c.doctor.hours.length ? c.doctor.hours.map(([a, b]) => `${a}–${b}`).join(', ') : 'Not working'}
              </span>
            </div>
          </div>
        ))}
      </div>

      <div className="planner-body" style={{ height }}>
        <div className="planner-lines" aria-hidden="true">
          {ticks.map((t) => (
            <div key={t} className={t % 60 === 0 ? 'grid-line grid-line-hour' : 'grid-line'} style={{ top: (t - start) * scale }} />
          ))}
        </div>

        <div className="planner-times" style={{ height }}>
          {ticks.map((t) => (
            <span key={t} className={t % 60 === 0 ? 'tick tick-hour' : 'tick'} style={{ top: (t - start) * scale }}>
              {clock(t)}
            </span>
          ))}
          {showNow && <span className="now-dot" style={{ top: (nowMinutes - start) * scale }} />}
        </div>

        {columns.map((c) => (
          <div key={c.key} className="planner-column" style={{ height }}>
            {offBlocks(c, start, end).map(([a, b]) => (
              <div key={a} className="off-hours" style={{ top: (a - start) * scale, height: (b - a) * scale }} />
            ))}
            {layout(c.appointments, c.day.timezone).map(({ a, from, to, lane, lanes }) => {
              const cardHeight = Math.max(30, (to - from) * scale - 4)
              const actions = actionsFor(a)
              const kind = a.status !== 'booked' ? a.status : a.referred_from ? 'referral' : 'booked'
              return (
                <article key={a.appointment_id}
                         className={`appt appt-${kind}${cardHeight < 84 ? ' appt-compact' : cardHeight < 140 ? ' appt-medium' : ''}${menu === a.appointment_id ? ' appt-open' : ''}`}
                         style={{ top: (from - start) * scale + 2, height: cardHeight,
                                  left: `calc(${(lane / lanes) * 100}% + 5px)`, width: `calc(${100 / lanes}% - 10px)` }}>
                  <div className="appt-head">
                    <strong>{a.patient_name || a.patient_email}</strong>
                    {actions.length > 0 && (
                      <button className="appt-more" aria-label="Actions" aria-expanded={menu === a.appointment_id}
                              onClick={(e) => { e.stopPropagation(); setMenu(menu === a.appointment_id ? null : a.appointment_id) }}>
                        <MoreIcon size={18} />
                      </button>
                    )}
                  </div>
                  <div className="appt-chips">
                    <span className={`chip-tag chip-${a.status}`}>{STATUS_LABEL[a.status]}</span>
                    {a.referred_from && <span className="chip-tag">Referral</span>}
                    {a.case_id && clinical && <span className="chip-tag">Interview</span>}
                  </div>
                  {clinical && a.complaint && <p className="appt-text">{a.complaint}</p>}
                  {a.referred_from && <p className="appt-meta">From {a.referred_from.doctor_name} · {a.referred_from.clinic_name}</p>}
                  {a.referred_to && (
                    <p className="appt-meta">
                      To {a.referred_to.doctor_name} · {a.referred_to.clinic_name}, {formatDay(a.referred_to.starts_at, a.referred_to.timezone)}
                    </p>
                  )}
                  <p className="appt-time">
                    <ClockIcon size={15} /> {formatTime(a.starts_at, a.timezone)} – {formatTime(a.ends_at, a.timezone)}
                  </p>
                  {menu === a.appointment_id && (
                    <div className="appt-menu" role="menu" onClick={(e) => e.stopPropagation()}>
                      {actions.map((action) => (
                        <button key={action.label} role="menuitem" className={action.danger ? 'danger' : undefined}
                                onClick={() => { setMenu(null); action.run() }}>
                          {action.label}
                        </button>
                      ))}
                    </div>
                  )}
                </article>
              )
            })}
            {showNow && c.day.day === todayKey && <div className="now-line" style={{ top: (nowMinutes - start) * scale }} />}
          </div>
        ))}
      </div>
      </div>
    </div>
  )
}

// the parts of [start, end] in which this doctor does not see patients (hatched)
function offBlocks(c: Column, start: number, end: number): [number, number][] {
  const open = c.day.opening_hours
  const working = (c.doctor.day_off || !open ? [] : c.doctor.hours)
    .map(([a, b]) => [Math.max(minutes(a), minutes(open![0])), Math.min(minutes(b), minutes(open![1]))] as [number, number])
    .filter(([a, b]) => a < b)
    .sort((x, y) => x[0] - y[0])
  const blocks: [number, number][] = []
  let cursor = start
  for (const [a, b] of working) {
    if (a > cursor) blocks.push([cursor, Math.min(a, end)])
    cursor = Math.max(cursor, b)
  }
  if (cursor < end) blocks.push([cursor, end])
  return blocks
}

// overlapping visits side by side: each gets a lane, and the number of lanes of its group
function layout(appointments: Appointment[], timeZone: string) {
  const items = appointments
    .map((a) => {
      const from = minutesOfDay(a.starts_at, timeZone)
      return { a, from, to: from + Math.max(10, (Date.parse(a.ends_at) - Date.parse(a.starts_at)) / 60000), lane: 0, lanes: 1 }
    })
    .sort((x, y) => x.from - y.from)
  let group: typeof items = []
  let groupEnd = -1
  const laneEnds: number[] = []
  const closeGroup = () => group.forEach((item) => (item.lanes = laneEnds.length))
  for (const item of items) {
    if (group.length && item.from >= groupEnd) {
      closeGroup()
      group = []
      laneEnds.length = 0
    }
    let lane = laneEnds.findIndex((endAt) => endAt <= item.from)
    if (lane === -1) lane = laneEnds.push(item.to) - 1
    else laneEnds[lane] = item.to
    item.lane = lane
    group.push(item)
    groupEnd = Math.max(groupEnd, item.to)
  }
  closeGroup()
  return items
}
