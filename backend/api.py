"""The signed-in part of the HTTP API (everything needs a Neon Auth token, see auth.py).

  GET  /api/me                              the user, their clinics and roles
  GET  /api/cases                           own cases (patient) and clinic cases (doctor, nurse)
  GET  /api/cases/{case_id}                 one case: its patient, or a doctor or nurse of its clinic
                                            (or of a clinic where it is booked, see booking_api.py)
  PUT  /api/cases/{case_id}/review          the patient saves their edited summary
  GET  /api/orgs                            clinics the user can manage
  POST /api/orgs                            create a clinic (platform admin)
  GET  /api/orgs/{org_id}/details           address, phone, email, registration number (admins;
  PUT  /api/orgs/{org_id}/details           printed on the clinic's prescriptions)
  GET  /api/orgs/{org_id}/members           who has which role (clinic admin, platform admin)
  POST /api/orgs/{org_id}/members           grant a role to a registered user, by email
  DELETE /api/orgs/{org_id}/members/{user_id}/{role}
  POST /api/orgs/{org_id}/invitations       invite an email address to a role (one-time link, 7 days)
  GET  /api/orgs/{org_id}/invitations       pending invitations
  DELETE /api/orgs/{org_id}/invitations/{invite_id}
  GET  /api/invitations/{token}             what an invitation is for (no sign-in needed)
  POST /api/invitations/accept              the invited person, signed in with that email, accepts

Front desk and admins do not see clinical content. Views and changes of patient data and
role changes are written to the audit log.
"""
import asyncio
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import case_store
import db
from auth import User, current_user, forget
from india import STATES

router = APIRouter(prefix="/api")

CLINICAL_ROLES = ("doctor", "nurse")  # may see the clinical content of their clinic's cases


def _profile(user: User):
    return {"id": user.id, "email": user.email, "name": user.name,
            "platform_admin": user.platform_admin, "memberships": user.memberships}


@router.get("/me")
async def me(user: User = Depends(current_user)):
    return _profile(user)


@router.get("/cases")
async def list_cases(user: User = Depends(current_user)):
    clinics = user.orgs_with(*CLINICAL_ROLES)

    async def nothing():
        return []
    own, clinic_cases = await asyncio.gather(  # both lists in one round trip of waiting
        case_store.list_for_patient(user.id) if user.has_role("patient") else nothing(),
        case_store.list_for_orgs(clinics) if clinics else nothing(),
    )
    if clinic_cases:
        db.audit_later(user.id, "list_cases", detail={"orgs": sorted(clinics), "count": len(clinic_cases)})
    return {"own": own, "clinic": clinic_cases}


@router.get("/cases/{case_id}")
async def get_case(case_id: str, user: User = Depends(current_user)):
    case = await case_store.load(case_id)
    clinics = {str(org) for org in ([case["org_id"]] + case["booked_org_ids"]) if org} if case else set()
    allowed = case is not None and (
        str(case["patient_user_id"]) == user.id or bool(clinics & user.orgs_with(*CLINICAL_ROLES))
    )
    if not allowed:
        raise HTTPException(404, "No such case")  # the same answer whether it exists or not
    db.audit_later(user.id, "view_case", case_id=case["case_id"], org_id=case["org_id"])
    return case


class CaseReview(BaseModel):
    sections: dict[str, str]  # summary section label -> the text as the patient edited it


@router.put("/cases/{case_id}/review")
async def save_case_review(case_id: str, review: CaseReview, user: User = Depends(current_user)):
    try:
        saved_at = await case_store.save_review(case_id, review.sections, user.id)
    except case_store.InvalidReview as e:
        raise HTTPException(400, str(e))
    if saved_at is None:
        raise HTTPException(404, "No such case")
    db.audit_later(user.id, "review_case", case_id=case_store._case_uuid(case_id))
    return {"saved_at": saved_at}


# ---- clinics and roles

def _require_manager(user: User, org_id: str):
    try:
        org_id = str(uuid.UUID(org_id))
    except ValueError:
        raise HTTPException(404, "No such clinic")
    if not (user.platform_admin or org_id in user.orgs_with("clinic_admin")):
        raise HTTPException(403, "Only this clinic's admins can manage its members")


@router.get("/orgs")
async def list_orgs(user: User = Depends(current_user)):
    database = await db.pool()
    rows = await database.fetch("SELECT org_id, name, is_default FROM organizations ORDER BY name")
    manageable = user.orgs_with("clinic_admin")
    return [dict(r) for r in rows if user.platform_admin or str(r["org_id"]) in manageable]


class NewOrg(BaseModel):
    name: str


@router.post("/orgs")
async def create_org(new: NewOrg, user: User = Depends(current_user)):
    if not user.platform_admin:
        raise HTTPException(403, "Only platform admins can create clinics")
    name = new.name.strip()
    if not 2 <= len(name) <= 100:
        raise HTTPException(400, "A clinic name is 2 to 100 characters")
    database = await db.pool()
    org = await database.fetchrow("INSERT INTO organizations (name) VALUES ($1) RETURNING org_id, name, is_default", name)
    db.audit_later(user.id, "create_org", org_id=org["org_id"], detail={"name": name})
    return dict(org)


class ClinicDetails(BaseModel):
    """Printed on the clinic's prescriptions (WHO: the prescriber's address and telephone, so
    the pharmacist can reach them)."""
    address: str = Field("", max_length=300)
    phone: str = Field("", max_length=40)
    email: str = Field("", max_length=120)
    registration_number: str = Field("", max_length=60)  # e.g. under the Clinical Establishments Act
    # how patients find the hospital (a state and district are needed to be listed), and what it shows
    state: str = Field("", max_length=60)
    district: str = Field("", max_length=80)
    consultation_fee: Optional[int] = Field(None, ge=0, le=100000)  # rupees; none = not shown
    description: str = Field("", max_length=600)


def _district(text: str):
    """A district as typed, tidied: spaces collapsed, and capitalised when typed all in one case."""
    text = " ".join(text.split())
    return text.title() if text.islower() or text.isupper() else text


@router.get("/orgs/{org_id}/details")
async def get_clinic_details(org_id: str, user: User = Depends(current_user)):
    _require_manager(user, org_id)
    database = await db.pool()
    row = await database.fetchrow(
        "SELECT name, address, phone, email, registration_number, state, district, consultation_fee, description "
        "FROM organizations WHERE org_id = $1::uuid", org_id)
    if row is None:
        raise HTTPException(404, "No such clinic")
    return dict(row)


@router.put("/orgs/{org_id}/details")
async def save_clinic_details(org_id: str, details: ClinicDetails, user: User = Depends(current_user)):
    _require_manager(user, org_id)
    values = {k: v.strip() if isinstance(v, str) else v for k, v in details.model_dump().items()}
    if values["state"] and values["state"] not in STATES:
        raise HTTPException(400, "Choose the state from the list")
    values["district"] = _district(values["district"])
    database = await db.pool()
    saved = await database.fetchval(
        """
        UPDATE organizations SET address = $2, phone = $3, email = $4, registration_number = $5,
                                 state = $6, district = $7, consultation_fee = $8, description = $9
        WHERE org_id = $1::uuid RETURNING true
        """,
        org_id, values["address"], values["phone"], values["email"], values["registration_number"],
        values["state"], values["district"], values["consultation_fee"], values["description"],
    )
    if not saved:
        raise HTTPException(404, "No such clinic")
    db.audit_later(user.id, "save_clinic_details", org_id=org_id, detail=values)
    return values


@router.get("/orgs/{org_id}/members")
async def list_members(org_id: str, user: User = Depends(current_user)):
    _require_manager(user, org_id)
    database = await db.pool()
    rows = await database.fetch(
        """
        SELECT m.user_id, u.name, u.email, m.role, m.created_at
        FROM memberships m JOIN neon_auth."user" u ON u.id = m.user_id
        WHERE m.org_id = $1::uuid ORDER BY u.email, m.role
        """,
        org_id,
    )
    return [dict(r) for r in rows]


class Grant(BaseModel):
    email: str
    role: Literal[db.ROLES]


@router.post("/orgs/{org_id}/members")
async def grant_role(org_id: str, grant: Grant, user: User = Depends(current_user)):
    _require_manager(user, org_id)
    database = await db.pool()
    # find the account and give the role in one statement
    target = await database.fetchval(
        """
        WITH target AS (SELECT id FROM neon_auth."user" WHERE lower(email) = lower($1) LIMIT 1),
             granted AS (INSERT INTO memberships (user_id, org_id, role, created_by)
                         SELECT id, $2::uuid, $3, $4 FROM target ON CONFLICT DO NOTHING)
        SELECT id FROM target
        """,
        grant.email.strip(), org_id, grant.role, user.id,
    )
    if target is None:
        raise HTTPException(404, "No account with this email yet: ask them to sign up first")
    forget(target)
    db.audit_later(user.id, "grant_role", org_id=org_id, detail={"user_id": str(target), "role": grant.role})
    return {"user_id": target, "role": grant.role}


@router.delete("/orgs/{org_id}/members/{member_id}/{role}")
async def revoke_role(org_id: str, member_id: str, role: str, user: User = Depends(current_user)):
    _require_manager(user, org_id)
    try:
        uuid.UUID(member_id)
    except ValueError:
        raise HTTPException(404, "No such role")
    database = await db.pool()
    result = await database.execute(
        "DELETE FROM memberships WHERE org_id = $1::uuid AND user_id = $2::uuid AND role = $3", org_id, member_id, role,
    )
    if result == "DELETE 0":
        raise HTTPException(404, "No such role")
    forget(member_id)
    db.audit_later(user.id, "revoke_role", org_id=org_id, detail={"user_id": member_id, "role": role})
    return {"removed": True}


# ---- invitations: a one-time link that gives a role in a clinic to one email address

INVITATION_DAYS = 7


def _token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()  # only the hash is stored


class Invite(BaseModel):
    email: str
    role: Literal[db.ROLES]


@router.post("/orgs/{org_id}/invitations")
async def create_invitation(org_id: str, invite: Invite, user: User = Depends(current_user)):
    _require_manager(user, org_id)
    email = invite.email.strip().lower()
    if "@" not in email or len(email) > 254:
        raise HTTPException(400, "Enter a valid email address")
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=INVITATION_DAYS)
    database = await db.pool()
    invite_id = await database.fetchval(
        """
        INSERT INTO invitations (token_hash, org_id, email, role, created_by, expires_at)
        VALUES ($1, $2::uuid, $3, $4, $5, $6) RETURNING invite_id
        """,
        _token_hash(token), org_id, email, invite.role, user.id, expires_at,
    )
    db.audit_later(user.id, "create_invitation", org_id=org_id,
                   detail={"invite_id": str(invite_id), "email": email, "role": invite.role})
    # the browser builds the link from its own address: <origin>/?invite=<token>
    return {"invite_id": invite_id, "token": token, "email": email, "role": invite.role, "expires_at": expires_at}


@router.get("/orgs/{org_id}/invitations")
async def list_invitations(org_id: str, user: User = Depends(current_user)):
    _require_manager(user, org_id)
    database = await db.pool()
    rows = await database.fetch(
        """
        SELECT invite_id, email, role, created_at, expires_at FROM invitations
        WHERE org_id = $1::uuid AND accepted_at IS NULL AND expires_at > now()
        ORDER BY created_at DESC
        """,
        org_id,
    )
    return [dict(r) for r in rows]


@router.delete("/orgs/{org_id}/invitations/{invite_id}")
async def revoke_invitation(org_id: str, invite_id: str, user: User = Depends(current_user)):
    _require_manager(user, org_id)
    try:
        invite_key = uuid.UUID(invite_id)
    except ValueError:
        raise HTTPException(404, "No such invitation")
    database = await db.pool()
    result = await database.execute(
        "DELETE FROM invitations WHERE invite_id = $1 AND org_id = $2::uuid AND accepted_at IS NULL", invite_key, org_id,
    )
    if result == "DELETE 0":
        raise HTTPException(404, "No such invitation")
    db.audit_later(user.id, "revoke_invitation", org_id=org_id, detail={"invite_id": invite_id})
    return {"revoked": True}


async def _pending_invitation(token):
    database = await db.pool()
    row = await database.fetchrow(
        """
        SELECT i.invite_id, i.org_id, o.name AS org_name, i.email, i.role, i.created_by
        FROM invitations i JOIN organizations o USING (org_id)
        WHERE i.token_hash = $1 AND i.accepted_at IS NULL AND i.expires_at > now()
        """,
        _token_hash(token),
    )
    if row is None:
        raise HTTPException(404, "This invitation has expired, was already used, or was withdrawn")
    return row


@router.get("/invitations/{token}")
async def describe_invitation(token: str):
    invitation = await _pending_invitation(token)
    return {"org_name": invitation["org_name"], "role": invitation["role"], "email": invitation["email"]}


class Acceptance(BaseModel):
    token: str


@router.post("/invitations/accept")
async def accept_invitation(acceptance: Acceptance, user: User = Depends(current_user)):
    database = await db.pool()
    # claim the invitation (once, and only with its email address) and add the role, in one
    # statement: atomic, and one round trip
    claimed = await database.fetchrow(
        """
        WITH claimed AS (
            UPDATE invitations SET accepted_at = now(), accepted_by = $2
            WHERE token_hash = $1 AND accepted_at IS NULL AND expires_at > now() AND lower(email) = lower($3)
            RETURNING invite_id, org_id, role, created_by
        ),
        joined AS (
            INSERT INTO memberships (user_id, org_id, role, created_by)
            SELECT $2, org_id, role, created_by FROM claimed ON CONFLICT DO NOTHING
        )
        SELECT c.*, o.name AS org_name FROM claimed c JOIN organizations o USING (org_id)
        """,
        _token_hash(acceptance.token), user.id, user.email,
    )
    if claimed is None:  # say why (only on this error path: one more query)
        invitation = await _pending_invitation(acceptance.token)
        raise HTTPException(403, f"This invitation is for {invitation['email']}: sign in with that address")
    forget(user.id)
    db.audit_later(user.id, "accept_invitation", org_id=claimed["org_id"],
                   detail={"invite_id": str(claimed["invite_id"]), "role": claimed["role"]})
    role = {"org_id": str(claimed["org_id"]), "org_name": claimed["org_name"], "role": claimed["role"]}
    if role not in user.memberships:
        user.memberships = sorted(user.memberships + [role], key=lambda m: (m["org_name"], m["role"]))
    return _profile(user)
