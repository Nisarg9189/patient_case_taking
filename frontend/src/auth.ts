// Sign-in with Neon Auth (email + password), and signed-in calls to our backend.
// VITE_NEON_AUTH_URL (frontend/.env.local) is the project's Neon Auth URL.
import { createAuthClient } from '@neondatabase/neon-js/auth'

export const authClient = createAuthClient(import.meta.env.VITE_NEON_AUTH_URL)

export type Role = 'patient' | 'doctor' | 'nurse' | 'front_desk' | 'clinic_admin'

export interface Me {
  id: string
  email: string
  name: string
  platform_admin: boolean
  memberships: { org_id: string; org_name: string; role: Role }[]
}

// The backend accepts Neon Auth's short-lived JWT (15 min). The SDK puts it in the session:
// getSession() reads it from the set-auth-jwt response header into session.token and caches
// the session until the JWT expires, so this is cheap to call before every request.
export async function getToken(): Promise<string> {
  const { data } = await authClient.getSession()
  const token = data?.session?.token
  if (!token || token.split('.').length !== 3) throw new Error('Please sign in again.')
  return token
}

// An error from our /api: `code` is set for errors the app handles (e.g. email_not_verified).
export class ApiError extends Error {
  status: number
  code?: string

  constructor(status: number, message: string, code?: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

// fetch() to our /api with the signed-in user's token; throws ApiError with the server's message
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init.headers, Authorization: `Bearer ${await getToken()}` },
  })
  if (!response.ok) {
    const detail = (await response.json().catch(() => null))?.detail
    const message = typeof detail === 'string' ? detail : detail?.message
    throw new ApiError(response.status, message ?? `Request failed (${response.status})`, detail?.code)
  }
  return response.json() as Promise<T>
}

// Fetch the session again instead of using the SDK's cached copy (its JWT still says
// "not verified" right after the email is verified). X-Force-Fetch makes the SDK skip its cache.
export async function refreshSession() {
  await authClient.getSession({ fetchOptions: { headers: { 'X-Force-Fetch': 'true' } } })
}

// Invitation links look like <app>/?invite=<token>; the token is kept until it is accepted.
const INVITE_KEY = 'pendingInvite'

export function takeInviteFromUrl() {
  const url = new URL(window.location.href)
  const token = url.searchParams.get('invite')
  if (token) {
    sessionStorage.setItem(INVITE_KEY, token)
    url.searchParams.delete('invite')
    history.replaceState(history.state, '', url.href) // keep the token out of the address bar and history
  }
  return sessionStorage.getItem(INVITE_KEY)
}

export function forgetInvite() {
  sessionStorage.removeItem(INVITE_KEY)
}

export interface Invitation {
  org_name: string
  role: Role
  email: string
}

export async function describeInvitation(token: string): Promise<Invitation> {
  const response = await fetch(`/api/invitations/${encodeURIComponent(token)}`)
  const body = await response.json().catch(() => null)
  if (!response.ok) throw new Error(body?.detail ?? 'This invitation is not valid.')
  return body as Invitation
}
