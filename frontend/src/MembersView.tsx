import { useCallback, useEffect, useState } from 'react'
import { api, type Me, type Role } from './auth'
import { PageHeader } from './PageHeader'

interface Org {
  org_id: string
  name: string
  is_default: boolean
}

interface PendingInvite {
  invite_id: string
  email: string
  role: Role
  expires_at: string
}

interface Member {
  user_id: string
  name: string | null
  email: string
  role: Role
}

const ROLE_LABEL: Record<Role, string> = {
  patient: 'Patient',
  doctor: 'Doctor',
  nurse: 'Nurse',
  front_desk: 'Front desk',
  clinic_admin: 'Clinic admin',
}

// Clinic admins (their clinics) and platform admins (all clinics): who has which role.
export function MembersView({ me }: { me: Me }) {
  const [orgs, setOrgs] = useState<Org[] | null>(null)
  const [orgId, setOrgId] = useState<string | null>(null)
  const [members, setMembers] = useState<Member[] | null>(null)
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<Role>('doctor')
  const [newClinic, setNewClinic] = useState('')
  const [invites, setInvites] = useState<PendingInvite[]>([])
  const [inviteEmail, setInviteEmail] = useState('')
  const [inviteRole, setInviteRole] = useState<Role>('doctor')
  const [link, setLink] = useState<{ url: string; email: string; role: Role; org: string } | null>(null)
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null)

  const loadOrgs = useCallback(async () => {
    const list = await api<Org[]>('/api/orgs')
    setOrgs(list)
    setOrgId((current) => current ?? list[0]?.org_id ?? null)
  }, [])

  const loadMembers = useCallback(async (id: string) => {
    const [memberList, inviteList] = await Promise.all([
      api<Member[]>(`/api/orgs/${id}/members`),
      api<PendingInvite[]>(`/api/orgs/${id}/invitations`),
    ])
    setMembers(memberList)
    setInvites(inviteList)
  }, [])

  useEffect(() => {
    loadOrgs().catch((e) => setMessage({ text: e.message, error: true }))
  }, [loadOrgs])

  useEffect(() => {
    if (orgId) loadMembers(orgId).catch((e) => setMessage({ text: e.message, error: true }))
  }, [orgId, loadMembers])

  const run = async (action: () => Promise<unknown>, success: string) => {
    setMessage(null)
    try {
      await action()
      setMessage({ text: success, error: false })
      if (orgId) await loadMembers(orgId)
    } catch (e) {
      setMessage({ text: e instanceof Error ? e.message : 'Something went wrong.', error: true })
    }
  }

  const grant = (event: React.FormEvent) => {
    event.preventDefault()
    void run(
      () => api(`/api/orgs/${orgId}/members`, { method: 'POST', body: JSON.stringify({ email, role }) }),
      `${ROLE_LABEL[role]} role given to ${email}.`,
    ).then(() => setEmail(''))
  }

  const invite = (event: React.FormEvent) => {
    event.preventDefault()
    const org = orgs?.find((o) => o.org_id === orgId)?.name ?? 'the clinic'
    void run(async () => {
      const created = await api<{ token: string; email: string; role: Role }>(`/api/orgs/${orgId}/invitations`, {
        method: 'POST',
        body: JSON.stringify({ email: inviteEmail, role: inviteRole }),
      })
      setLink({ url: `${window.location.origin}/?invite=${created.token}`, email: created.email, role: created.role, org })
      setInviteEmail('')
    }, `Invitation created for ${inviteEmail}. Send them the link below.`)
  }

  const mailto = (l: NonNullable<typeof link>) =>
    `mailto:${l.email}?subject=${encodeURIComponent(`Your invitation to ${l.org}`)}&body=${encodeURIComponent(
      `You have been invited as ${ROLE_LABEL[l.role]} at ${l.org}.\n\nOpen this link to create your account (or sign in) with this email address:\n${l.url}\n\nThe link works once and expires in 7 days.`,
    )}`

  const createClinic = (event: React.FormEvent) => {
    event.preventDefault()
    void run(async () => {
      const org = await api<Org>('/api/orgs', { method: 'POST', body: JSON.stringify({ name: newClinic }) })
      await loadOrgs()
      setOrgId(org.org_id)
      setNewClinic('')
    }, `Clinic "${newClinic}" created.`)
  }

  return (
    <div className="page">
      <PageHeader tabs={[['members', 'Clinic members']]} active="members" />
      <p className="page-intro">Give staff their roles. People must create an account before a role can be given.</p>
      {message && <div className={message.error ? 'notice' : 'notice notice-ok'}>{message.text}</div>}

      <div className="card">
        <label className="edit-row">
          <span className="row-label">Clinic</span>
          <select value={orgId ?? ''} onChange={(e) => setOrgId(e.target.value)}>
            {(orgs ?? []).map((o) => (
              <option key={o.org_id} value={o.org_id}>
                {o.name}
                {o.is_default ? ' (default: new patients join here)' : ''}
              </option>
            ))}
          </select>
        </label>

        {orgId && (
          <>
            <h3>Invite someone</h3>
            <form className="grant-form" onSubmit={invite}>
              <input type="email" placeholder="their email" value={inviteEmail}
                     onChange={(e) => setInviteEmail(e.target.value)} required />
              <select value={inviteRole} onChange={(e) => setInviteRole(e.target.value as Role)}>
                {(Object.keys(ROLE_LABEL) as Role[]).map((r) => (
                  <option key={r} value={r}>
                    {ROLE_LABEL[r]}
                  </option>
                ))}
              </select>
              <button className="primary" type="submit">
                Create invitation
              </button>
            </form>
            {link && (
              <div className="invite-link">
                <code>{link.url}</code>
                <div className="buttons">
                  <button className="edit-button" type="button"
                          onClick={() => void navigator.clipboard.writeText(link.url).then(() =>
                            setMessage({ text: 'Link copied.', error: false }))}>
                    Copy link
                  </button>
                  <a className="save-button" href={mailto(link)}>
                    Email it
                  </a>
                </div>
                <p className="muted small">The link works once, only for {link.email}, and expires in 7 days.</p>
              </div>
            )}
            {invites.length > 0 && (
              <table className="cases-table">
                <thead>
                  <tr>
                    <th>Invited</th>
                    <th>Role</th>
                    <th>Expires</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {invites.map((i) => (
                    <tr key={i.invite_id}>
                      <td>{i.email}</td>
                      <td>{ROLE_LABEL[i.role] ?? i.role}</td>
                      <td>{new Date(i.expires_at).toLocaleDateString()}</td>
                      <td>
                        <button className="link-button" onClick={() =>
                          void run(() => api(`/api/orgs/${orgId}/invitations/${i.invite_id}`, { method: 'DELETE' }),
                                   `Invitation for ${i.email} withdrawn.`)}>
                          Revoke
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <h3>Give a role to an existing account</h3>
          </>
        )}

        {orgId && (
          <form className="grant-form" onSubmit={grant}>
            <input type="email" placeholder="their account email" value={email}
                   onChange={(e) => setEmail(e.target.value)} required />
            <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
              {(Object.keys(ROLE_LABEL) as Role[]).map((r) => (
                <option key={r} value={r}>
                  {ROLE_LABEL[r]}
                </option>
              ))}
            </select>
            <button className="primary" type="submit">
              Give role
            </button>
          </form>
        )}

        {members === null ? (
          <p className="muted">Loading…</p>
        ) : (
          <table className="cases-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Email</th>
                <th>Role</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {members.map((m) => (
                <tr key={`${m.user_id}-${m.role}`}>
                  <td>{m.name || '—'}</td>
                  <td>{m.email}</td>
                  <td>{ROLE_LABEL[m.role] ?? m.role}</td>
                  <td>
                    {m.user_id !== me.id && (
                      <button className="link-button" onClick={() =>
                        void run(() => api(`/api/orgs/${orgId}/members/${m.user_id}/${m.role}`, { method: 'DELETE' }),
                                 `Removed ${ROLE_LABEL[m.role]} role from ${m.email}.`)}>
                        Remove
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {me.platform_admin && (
        <div className="card">
          <h3>New clinic</h3>
          <form className="grant-form" onSubmit={createClinic}>
            <input placeholder="clinic name" value={newClinic} onChange={(e) => setNewClinic(e.target.value)} required />
            <button className="primary" type="submit">
              Create clinic
            </button>
          </form>
        </div>
      )}
    </div>
  )
}
