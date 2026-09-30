"""Prescriptions: what a doctor prescribes for a patient's case, and who wrote it.

What a prescription holds follows the WHO Guide to Good Prescribing (ch. 9) and India's
Telemedicine Practice Guidelines 2020 / NMC rules:
  prescriber   name, qualification, registration number and council (NMC/State Medical
               Council; must be on every prescription), clinic; copied in when signed
  patient      name (from the account), age, sex, weight
  clinical     complaints, examination findings and vitals, diagnosis (provisional or final),
               known allergies (from the interview, shown as a warning)
  medicines    generic name (NMC: prescribe by generic name), optional brand with "do not
               substitute", dosage form, strength, dose, route, frequency, timing with food,
               duration, total quantity to dispense, instructions (for "when needed": the
               maximum dose and interval)
  also         investigations advised, advice, follow-up date
  signed       date and time; a signed prescription cannot change (void it, write a new one)

  GET    /api/me/doctor-profile                     the doctor's own details
  PUT    /api/me/doctor-profile
  GET    /api/cases/{case_id}/prescriptions         the case's prescriptions (+ can_write)
  POST   /api/cases/{case_id}/prescriptions         a doctor starts one (a draft)
  PUT    /api/prescriptions/{id}                    its author edits the draft
  POST   /api/prescriptions/{id}/sign               its author signs it (checked in full)
  POST   /api/prescriptions/{id}/void               its author voids a signed one, with a reason
  DELETE /api/prescriptions/{id}                    its author deletes a draft
  GET    /api/prescriptions                         a patient's own signed prescriptions
  GET    /api/me/patient-profile                    a patient's own details (date of birth, sex,
  PUT    /api/me/patient-profile                    weight, phone, address, ABHA number)

A new prescription starts with the patient's saved details; signing saves the details the
doctor confirmed back to them. The clinic's address, phone, email and registration number
(set by its admins) are copied into a prescription when it is signed.

Who: doctors of a clinic that may see the case (its clinic, or one where it is booked) write;
those doctors and nurses read; the patient reads their own signed ones. Drafts are seen by
their author only. Each request is one database statement (the database is far away).
"""
import re
import uuid
from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

import db
from auth import User, current_user

router = APIRouter(prefix="/api")

FORMS = ("tablet", "capsule", "syrup", "suspension", "injection", "drops", "ointment", "cream", "gel",
         "inhaler", "sachet", "lotion", "spray", "other")
ROUTES = ("oral", "sublingual", "intravenous", "intramuscular", "subcutaneous", "topical", "inhaled",
          "nasal", "eye", "ear", "rectal", "vaginal", "other")
FREQUENCIES = ("once_daily", "twice_daily", "three_times_daily", "four_times_daily", "every_6_hours",
               "every_8_hours", "at_bedtime", "once_weekly", "as_needed", "stat", "other")
TIMINGS = ("after_food", "before_food", "with_food", "empty_stomach", "any")
DURATION_UNITS = ("days", "weeks", "months", "ongoing")

Text = lambda limit: Field("", max_length=limit)  # noqa: E731


class Medicine(BaseModel):
    code: str = Text(20)  # its Jan Aushadhi (PMBJP) drug code, when picked from the medicine list
    generic_name: str = Text(120)
    brand_name: str = Text(120)
    no_substitution: bool = False  # the brand, and no other, is to be dispensed
    form: Literal[FORMS] = "tablet"
    strength: str = Text(40)  # "500 mg", "125 mg/5 ml"
    dose: str = Text(40)  # "1 tablet", "5 ml"
    route: Literal[ROUTES] = "oral"
    frequency: Literal[FREQUENCIES] = "twice_daily"
    timing: Literal[TIMINGS] = "after_food"
    duration_value: Optional[int] = Field(None, ge=1, le=365)
    duration_unit: Literal[DURATION_UNITS] = "days"
    quantity: str = Text(60)  # total to dispense: "10 tablets", "1 bottle (100 ml)"
    instructions: str = Text(300)


def _abha(value):
    """The ABHA number (Ayushman Bharat Health Account, 14 digits, often 12-3456-7890-1234)."""
    value = value.strip()
    if value and len(re.sub(r"\D", "", value)) != 14:
        raise ValueError("an ABHA number has 14 digits")
    return value


class PatientDetails(BaseModel):
    """Who the prescription is for (Telemedicine Practice Guidelines 3.2.2: name, age, address,
    email, phone; ABDM: ABHA number, sex, date of birth, address). The name and email come
    from the patient's account."""
    age: str = Text(20)
    date_of_birth: Optional[date] = None
    sex: Literal["", "female", "male", "other"] = ""
    weight_kg: str = Text(10)
    phone: str = Text(20)
    address: str = Text(300)
    abha_number: str = Text(20)

    _check_abha = field_validator("abha_number")(classmethod(lambda cls, v: _abha(v)))


class Content(BaseModel):
    patient: PatientDetails = PatientDetails()
    complaints: str = Text(2000)
    allergies: str = Text(500)  # known allergies (from the interview, checked by the doctor)
    findings: str = Text(2000)  # examination and vitals
    diagnosis: str = Text(500)
    diagnosis_type: Literal["provisional", "final"] = "provisional"
    medicines: list[Medicine] = Field(default_factory=list, max_length=25)
    investigations: list[str] = Field(default_factory=list, max_length=20)
    advice: str = Text(2000)
    follow_up_date: Optional[date] = None
    follow_up_note: str = Text(300)


def problems_before_signing(content: Content):
    """What is missing for a complete prescription (empty: it can be signed)."""
    problems = []
    if not content.diagnosis.strip():
        problems.append("Write the diagnosis (provisional or final).")
    if not content.medicines and not content.advice.strip() and not content.investigations:
        problems.append("Add at least one medicine, investigation or advice.")
    for number, m in enumerate(content.medicines, 1):
        name = m.generic_name.strip() or f"Medicine {number}"
        missing = [label for label, value in (("generic name", m.generic_name), ("strength", m.strength),
                                              ("dose", m.dose), ("total quantity", m.quantity)) if not value.strip()]
        if m.frequency not in ("stat",) and m.duration_unit != "ongoing" and not m.duration_value:
            missing.append("duration")
        if missing:
            problems.append(f"{name}: add the {', '.join(missing)}.")
        if m.frequency in ("as_needed", "other") and not m.instructions.strip():
            problems.append(f"{name}: for '{'when needed' if m.frequency == 'as_needed' else 'other'}' write the "
                            "instructions (the maximum dose and how often).")
        if m.no_substitution and not m.brand_name.strip():
            problems.append(f"{name}: 'do not substitute' needs the brand name.")
    return problems


def _key(value, what="prescription"):
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise HTTPException(404, f"No such {what}")


def _require_doctor(user: User):
    if not user.has_role("doctor"):
        raise HTTPException(403, "Only doctors write prescriptions")


# a prescription as the API returns it, as jsonb (one row of `p`, the prescriptions alias)
_AS_JSON = """
    jsonb_build_object(
        'prescription_id', p.prescription_id, 'case_id', p.case_id, 'org_id', p.org_id, 'clinic_name', o.name,
        'doctor_id', p.doctor_id, 'doctor_name', coalesce(nullif(u.name, ''), u.email),
        'patient_name', coalesce(nullif(pu.name, ''), pu.email), 'patient_email', pu.email,
        'status', p.status, 'content', p.content, 'prescriber', p.prescriber,
        'created_at', p.created_at, 'updated_at', p.updated_at, 'signed_at', p.signed_at,
        'voided_at', p.voided_at, 'void_reason', p.void_reason)
"""
_JOINS = """
    JOIN organizations o ON o.org_id = p.org_id
    LEFT JOIN neon_auth."user" u ON u.id = p.doctor_id
    LEFT JOIN neon_auth."user" pu ON pu.id = p.patient_user_id
"""

# the first of these clinics ($2) through which a doctor or nurse may see case $1: a clinic
# where it is booked (or was referred from), else the case's own clinic
_ACCESS_ORG = """
    (SELECT org FROM (
        SELECT a.org_id AS org, 1 AS preference FROM appointments a
        WHERE a.case_id = $1 AND a.status IN ('booked', 'referred', 'completed') AND a.org_id = ANY($2::uuid[])
        UNION ALL
        SELECT c.org_id, 2 FROM cases c WHERE c.case_id = $1 AND c.org_id = ANY($2::uuid[])
    ) AS choices ORDER BY preference LIMIT 1)
"""


# a patient_profiles row ({pp}) as prescription patient details, only the fields it has;
# the age is worked out from the date of birth (months under 2 years)
_PROFILE_JSON = """
    jsonb_strip_nulls(jsonb_build_object(
        'date_of_birth', {pp}.date_of_birth,
        'age', CASE WHEN {pp}.date_of_birth IS NULL THEN NULL
                    WHEN age({pp}.date_of_birth) < interval '2 years'
                        THEN (extract(year FROM age({pp}.date_of_birth)) * 12
                              + extract(month FROM age({pp}.date_of_birth)))::int || ' months'
                    ELSE extract(year FROM age({pp}.date_of_birth))::int || ' years' END,
        'sex', nullif({pp}.sex, ''), 'weight_kg', nullif({pp}.weight_kg, ''), 'phone', nullif({pp}.phone, ''),
        'address', nullif({pp}.address, ''), 'abha_number', nullif({pp}.abha_number, '')))
"""


async def _one(sql, *args):
    database = await db.pool()
    return await database.fetchval(sql, *args)


# ---- the doctor's own details

class DoctorProfile(BaseModel):
    qualification: str = Field("", max_length=120)  # MBBS, MD (Medicine)
    specialty: str = Field("", max_length=120)
    registration_number: str = Field("", max_length=40)
    registration_council: str = Field("", max_length=120)  # NMC or the State Medical Council


@router.get("/me/doctor-profile")
async def get_doctor_profile(user: User = Depends(current_user)):
    _require_doctor(user)
    database = await db.pool()
    row = await database.fetchrow(
        "SELECT qualification, specialty, registration_number, registration_council FROM doctor_profiles WHERE user_id = $1",
        _key(user.id, "doctor"))
    return dict(row) if row else DoctorProfile().model_dump()


@router.put("/me/doctor-profile")
async def save_doctor_profile(profile: DoctorProfile, user: User = Depends(current_user)):
    _require_doctor(user)
    values = {k: v.strip() for k, v in profile.model_dump().items()}
    database = await db.pool()
    await database.execute(
        """
        INSERT INTO doctor_profiles (user_id, qualification, specialty, registration_number, registration_council)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (user_id) DO UPDATE SET qualification = $2, specialty = $3, registration_number = $4,
            registration_council = $5, updated_at = now()
        """,
        _key(user.id, "doctor"), values["qualification"], values["specialty"], values["registration_number"],
        values["registration_council"],
    )
    db.audit_later(user.id, "save_doctor_profile", detail=values)
    return values


# ---- a case's prescriptions

@router.get("/cases/{case_id}/prescriptions")
async def list_for_case(case_id: str, user: User = Depends(current_user)):
    """{prescriptions: [...newest first], can_write}. Signed and voided ones for the case's
    doctors, nurses and patient; drafts for their author only."""
    readers = [_key(o, "case") for o in user.orgs_with("doctor", "nurse")]
    writers = [_key(o, "case") for o in user.orgs_with("doctor")]
    result = await _one(
        f"""
        SELECT jsonb_build_object(
            'can_write', {_ACCESS_ORG.replace('$2', '$3')} IS NOT NULL,
            'prescriptions', coalesce((
                SELECT jsonb_agg({_AS_JSON} ORDER BY p.created_at DESC)
                FROM prescriptions p {_JOINS}
                WHERE p.case_id = $1
                  AND ((p.status = 'draft' AND p.doctor_id = $4)
                       OR (p.status <> 'draft' AND ({_ACCESS_ORG} IS NOT NULL
                                                    OR EXISTS (SELECT 1 FROM cases c WHERE c.case_id = $1
                                                               AND c.patient_user_id = $4))))
            ), '[]'::jsonb),
            'visible', {_ACCESS_ORG} IS NOT NULL
                       OR EXISTS (SELECT 1 FROM cases c WHERE c.case_id = $1 AND c.patient_user_id = $4))
        """,
        _key(case_id, "case"), readers, writers, _key(user.id, "user"),
    )
    if not result["visible"]:
        raise HTTPException(404, "No such case")
    if result["prescriptions"] and readers:
        db.audit_later(user.id, "view_prescriptions", case_id=case_id)
    return {"prescriptions": result["prescriptions"], "can_write": result["can_write"]}


@router.post("/cases/{case_id}/prescriptions")
async def start(case_id: str, content: Content, user: User = Depends(current_user)):
    """A doctor who may see the case starts a prescription (a draft) in their clinic."""
    _require_doctor(user)
    # the patient's saved details fill in what the draft does not say (one statement)
    row = await _one(
        f"""
        WITH saved AS (
            SELECT {_PROFILE_JSON.format(pp="pp")} AS details
            FROM cases c JOIN patient_profiles pp ON pp.user_id = c.patient_user_id WHERE c.case_id = $1
        ), given AS (
            SELECT coalesce(jsonb_object_agg(key, value), '{{}}'::jsonb) AS details
            FROM jsonb_each(coalesce($4::jsonb -> 'patient', '{{}}'::jsonb))
            WHERE value NOT IN ('""'::jsonb, 'null'::jsonb)
        ), p AS (
            INSERT INTO prescriptions (case_id, patient_user_id, org_id, doctor_id, content)
            SELECT c.case_id, c.patient_user_id, {_ACCESS_ORG}, $3,
                   jsonb_set($4::jsonb, '{{patient}}',
                             coalesce((SELECT details FROM saved), '{{}}'::jsonb) || (SELECT details FROM given))
            FROM cases c WHERE c.case_id = $1 AND {_ACCESS_ORG} IS NOT NULL
            RETURNING *
        )
        SELECT {_AS_JSON} FROM p {_JOINS}
        """,
        _key(case_id, "case"), [_key(o) for o in user.orgs_with("doctor")], _key(user.id), content.model_dump(mode="json"),
    )
    if row is None:
        raise HTTPException(404, "No such case")
    db.audit_later(user.id, "start_prescription", case_id=case_id, org_id=row["org_id"],
                   detail={"prescription_id": row["prescription_id"]})
    return row


async def _author_update(prescription_id, user: User, set_sql, extra_where, *args, also=""):
    """UPDATE one of the user's own prescriptions and return it (jsonb), or None. `also`:
    more CTEs that use the updated row (p), in the same statement."""
    return await _one(
        f"""
        WITH p AS (
            UPDATE prescriptions SET {set_sql}, updated_at = now()
            WHERE prescription_id = $1 AND doctor_id = $2 AND {extra_where}
            RETURNING *
        ) {also}
        SELECT {_AS_JSON} FROM p {_JOINS}
        """,
        _key(prescription_id), _key(user.id), *args,
    )


async def _why_not(prescription_id, user: User, wanted_status):
    """Only after an update found nothing: say why (one more query, on the error path only)."""
    database = await db.pool()
    row = await database.fetchrow("SELECT doctor_id, status FROM prescriptions WHERE prescription_id = $1",
                                  _key(prescription_id))
    if row is None or str(row["doctor_id"]) != user.id:
        raise HTTPException(404, "No such prescription (only its author can change it)")
    if row["status"] != wanted_status:
        raise HTTPException(409, {"draft": "It is already signed: void it and write a new one.",
                                  "signed": "Only a signed prescription can be voided."}[wanted_status])
    return row


@router.put("/prescriptions/{prescription_id}")
async def save_draft(prescription_id: str, content: Content, user: User = Depends(current_user)):
    row = await _author_update(prescription_id, user, "content = $3", "status = 'draft'", content.model_dump(mode="json"))
    if row is None:
        await _why_not(prescription_id, user, "draft")
        raise HTTPException(409, "The prescription changed meanwhile: reload it")
    return row


@router.post("/prescriptions/{prescription_id}/sign")
async def sign(prescription_id: str, content: Content, user: User = Depends(current_user)):
    """Save the final content and sign: the prescriber's details are copied in, and it can no
    longer change. Needs the doctor's registration number and qualification."""
    problems = problems_before_signing(content)
    if problems:
        raise HTTPException(400, {"code": "incomplete", "message": " ".join(problems), "problems": problems})
    row = await _author_update(
        prescription_id, user,
        """content = $3, status = 'signed', signed_at = now(),
           prescriber = (SELECT jsonb_build_object(
                             'name', coalesce(nullif(du.name, ''), du.email), 'qualification', d.qualification,
                             'specialty', d.specialty, 'registration_number', d.registration_number,
                             'registration_council', d.registration_council, 'clinic_name', so.name,
                             'clinic_address', so.address, 'clinic_phone', so.phone, 'clinic_email', so.email,
                             'clinic_registration', so.registration_number)
                         FROM neon_auth."user" du JOIN doctor_profiles d ON d.user_id = du.id,
                              organizations so
                         WHERE du.id = prescriptions.doctor_id AND so.org_id = prescriptions.org_id)""",
        """status = 'draft' AND EXISTS (SELECT 1 FROM doctor_profiles d WHERE d.user_id = $2
                                        AND d.registration_number <> '' AND d.qualification <> '')""",
        content.model_dump(mode="json"),
        # the patient details the doctor confirmed become the patient's saved details (empty
        # fields keep what was saved), so the next prescription starts with them
        also=""", saved AS (
            INSERT INTO patient_profiles AS pp (user_id, date_of_birth, sex, weight_kg, phone, address, abha_number,
                                                updated_by)
            SELECT p.patient_user_id, (p.content #>> '{patient,date_of_birth}')::date,
                   coalesce(p.content #>> '{patient,sex}', ''), coalesce(p.content #>> '{patient,weight_kg}', ''),
                   coalesce(p.content #>> '{patient,phone}', ''), coalesce(p.content #>> '{patient,address}', ''),
                   coalesce(p.content #>> '{patient,abha_number}', ''), $2
            FROM p WHERE p.patient_user_id IS NOT NULL
            ON CONFLICT (user_id) DO UPDATE SET
                date_of_birth = coalesce(excluded.date_of_birth, pp.date_of_birth),
                sex = coalesce(nullif(excluded.sex, ''), pp.sex),
                weight_kg = coalesce(nullif(excluded.weight_kg, ''), pp.weight_kg),
                phone = coalesce(nullif(excluded.phone, ''), pp.phone),
                address = coalesce(nullif(excluded.address, ''), pp.address),
                abha_number = coalesce(nullif(excluded.abha_number, ''), pp.abha_number),
                updated_at = now(), updated_by = excluded.updated_by
        ), visit AS (
            -- the patient was seen: the case's booked visit at this clinic, due today or
            -- earlier (clinic's calendar), is completed (the patient cannot cancel it now)
            UPDATE appointments a SET status = 'completed', completed_at = now(), completed_by = $2
            FROM p LEFT JOIN clinic_schedules cs ON cs.org_id = p.org_id
            WHERE a.case_id = p.case_id AND a.org_id = p.org_id AND a.status = 'booked'
              AND (a.starts_at AT TIME ZONE coalesce(cs.timezone, 'Asia/Kolkata'))::date
                  <= (now() AT TIME ZONE coalesce(cs.timezone, 'Asia/Kolkata'))::date
        )""",
    )
    if row is None:
        await _why_not(prescription_id, user, "draft")
        raise HTTPException(400, {"code": "doctor_profile", "message":
                                  "Add your registration number and qualification first (they go on every prescription)."})
    db.audit_later(user.id, "sign_prescription", case_id=row["case_id"], org_id=row["org_id"],
                   detail={"prescription_id": row["prescription_id"], "medicines": len(content.medicines)})
    return row


class Void(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


@router.post("/prescriptions/{prescription_id}/void")
async def void(prescription_id: str, body: Void, user: User = Depends(current_user)):
    row = await _author_update(prescription_id, user, "status = 'void', voided_at = now(), void_reason = $3",
                               "status = 'signed'", body.reason.strip())
    if row is None:
        await _why_not(prescription_id, user, "signed")
        raise HTTPException(409, "The prescription changed meanwhile: reload it")
    db.audit_later(user.id, "void_prescription", case_id=row["case_id"], org_id=row["org_id"],
                   detail={"prescription_id": row["prescription_id"], "reason": body.reason.strip()})
    return row


@router.delete("/prescriptions/{prescription_id}")
async def delete_draft(prescription_id: str, user: User = Depends(current_user)):
    deleted = await _one(
        "DELETE FROM prescriptions WHERE prescription_id = $1 AND doctor_id = $2 AND status = 'draft' RETURNING case_id",
        _key(prescription_id), _key(user.id))
    if deleted is None:
        await _why_not(prescription_id, user, "draft")
        raise HTTPException(409, "The prescription changed meanwhile: reload it")
    db.audit_later(user.id, "delete_prescription_draft", case_id=deleted, detail={"prescription_id": prescription_id})
    return {"deleted": True}


class PatientProfile(BaseModel):
    date_of_birth: Optional[date] = None
    sex: Literal["", "female", "male", "other"] = ""
    weight_kg: str = Field("", max_length=10)
    phone: str = Field("", max_length=20)
    address: str = Field("", max_length=300)
    abha_number: str = Field("", max_length=20)

    _check_abha = field_validator("abha_number")(classmethod(lambda cls, v: _abha(v)))


@router.get("/me/patient-profile")
async def get_patient_profile(user: User = Depends(current_user)):
    database = await db.pool()
    row = await database.fetchrow(
        "SELECT date_of_birth, sex, weight_kg, phone, address, abha_number FROM patient_profiles WHERE user_id = $1",
        _key(user.id, "user"))
    return dict(row) if row else PatientProfile().model_dump()


@router.put("/me/patient-profile")
async def save_patient_profile(profile: PatientProfile, user: User = Depends(current_user)):
    if not user.has_role("patient"):
        raise HTTPException(403, "Only patients have these details")
    values = profile.model_dump()
    database = await db.pool()
    await database.execute(
        """
        INSERT INTO patient_profiles (user_id, date_of_birth, sex, weight_kg, phone, address, abha_number, updated_by)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $1)
        ON CONFLICT (user_id) DO UPDATE SET date_of_birth = $2, sex = $3, weight_kg = $4, phone = $5, address = $6,
            abha_number = $7, updated_at = now(), updated_by = $1
        """,
        _key(user.id, "user"), values["date_of_birth"], values["sex"], values["weight_kg"].strip(),
        values["phone"].strip(), values["address"].strip(), values["abha_number"].strip(),
    )
    db.audit_later(user.id, "save_patient_profile")
    return values


@router.get("/prescriptions")
async def my_prescriptions(user: User = Depends(current_user)):
    """The patient's own prescriptions (signed and voided), newest first."""
    return await _one(
        f"""
        SELECT coalesce(jsonb_agg({_AS_JSON} ORDER BY p.signed_at DESC), '[]'::jsonb)
        FROM (SELECT * FROM prescriptions WHERE patient_user_id = $1 AND status <> 'draft'
              ORDER BY signed_at DESC LIMIT 50) p {_JOINS}
        """,
        _key(user.id, "user"),
    )
