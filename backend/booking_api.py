"""Appointments: clinics set their hours, patients book a time after the interview.

  GET    /api/clinics                          clinics that take bookings (signed-in users)
  GET    /api/clinics/{org_id}/slots           free slots in the clinic's booking window
  POST   /api/appointments                     a patient books a slot (after an interview)
  GET    /api/appointments                     the patient's own appointments
  DELETE /api/appointments/{appointment_id}    cancel: the patient before the visit starts, or the
                                               clinic's front desk or admins before it ends;
                                               never a completed visit
  POST   /api/appointments/{appointment_id}/complete  clinic staff: the visit took place
  POST   /api/appointments/{appointment_id}/refer  a doctor of the visit's clinic sends it (and the
                                               interview) to another doctor, at a free time there
  GET    /api/orgs/{org_id}/appointments       one day of the clinic (staff), ?day=YYYY-MM-DD
  GET    /api/orgs/{org_id}/schedule           opening hours, slot settings, doctors' availability (admins)
  PUT    /api/orgs/{org_id}/schedule
  PUT    /api/orgs/{org_id}/doctors/{doctor_id}/availability

The slot rules are in scheduling.py. Front desk sees who comes when, not clinical content
(nor a referral's note, which is written for the receiving doctor; the patient does not see
the note either).
"""
import asyncio
import uuid
from datetime import date, datetime, timezone
from typing import Annotated, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, StringConstraints, field_validator

import db
import scheduling
from api import _require_manager
from auth import User, current_user

router = APIRouter(prefix="/api")

STAFF_ROLES = ("doctor", "nurse", "front_desk", "clinic_admin")  # see the clinic's appointments
DESK_ROLES = ("front_desk", "clinic_admin")  # may cancel any of the clinic's appointments

HHMM = Annotated[str, StringConstraints(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]


def _org_key(org_id):
    try:
        return str(uuid.UUID(org_id))
    except ValueError:
        raise HTTPException(404, "No such clinic")


def _check_range(pair, what):
    if pair[0] >= pair[1]:
        raise ValueError(f"{what}: the start ({pair[0]}) must be before the end ({pair[1]})")
    return pair


# ---- patients

@router.get("/clinics")
async def list_clinics(user: User = Depends(current_user)):
    return await scheduling.bookable_clinics()


@router.get("/clinics/{org_id}/slots")
async def clinic_slots(org_id: str, user: User = Depends(current_user)):
    return await scheduling.open_slots(_org_key(org_id))


class Booking(BaseModel):
    org_id: str
    starts_at: datetime
    doctor_id: Optional[str] = None  # None: any available doctor
    case_id: Optional[str] = None  # the interview this visit is for


@router.post("/appointments")
async def book_appointment(booking: Booking, user: User = Depends(current_user)):
    if not user.has_role("patient"):
        raise HTTPException(403, "Only patients can book appointments")
    if booking.starts_at.tzinfo is None:
        raise HTTPException(400, "starts_at needs a time zone")
    doctor_id = None
    if booking.doctor_id:
        try:
            doctor_id = str(uuid.UUID(booking.doctor_id))
        except ValueError:
            raise HTTPException(404, "No such doctor at this clinic")
    try:
        appointment = await scheduling.book(_org_key(booking.org_id), doctor_id, booking.starts_at, user.id, booking.case_id)
    except scheduling.BookingError as e:
        raise HTTPException(e.status, str(e))
    db.audit_later(user.id, "book_appointment", case_id=appointment["case_id"], org_id=appointment["org_id"],
                   detail={"appointment_id": str(appointment["appointment_id"]),
                           "starts_at": appointment["starts_at"].isoformat(), "doctor_id": str(appointment["doctor_id"])})
    return appointment


def _without_note(appointment):
    """For people who do not see clinical content: no referral note, no complaint."""
    appointment.pop("complaint", None)
    if appointment.get("referred_from"):
        appointment["referred_from"] = {k: v for k, v in appointment["referred_from"].items() if k != "note"}
    return appointment


@router.get("/appointments")
async def my_appointments(user: User = Depends(current_user)):
    return [_without_note(a) for a in await scheduling.for_patient(user.id)]


@router.delete("/appointments/{appointment_id}")
async def cancel_appointment(appointment_id: str, user: User = Depends(current_user)):
    # the permission check is part of the cancelling statement: one round trip
    cancelled = await scheduling.cancel(appointment_id, user.id, user.orgs_with(*DESK_ROLES), user.platform_admin)
    if cancelled is None:  # say why (only on this error path: one more query)
        appointment = await scheduling.get(appointment_id)
        allowed = appointment is not None and (
            str(appointment["patient_user_id"]) == user.id
            or user.platform_admin
            or str(appointment["org_id"]) in user.orgs_with(*DESK_ROLES)
        )
        if not allowed:
            raise HTTPException(404, "No such appointment")
        is_staff = user.platform_admin or str(appointment["org_id"]) in user.orgs_with(*DESK_ROLES)
        raise HTTPException(409, _why_not_cancelled(appointment, is_staff))
    db.audit_later(user.id, "cancel_appointment", case_id=cancelled["case_id"], org_id=cancelled["org_id"],
                   detail={"appointment_id": appointment_id})
    return {"cancelled": True}


def _why_not_cancelled(appointment, is_staff):
    now = datetime.now(timezone.utc)
    if appointment["status"] == "completed":
        return "This visit is completed: it cannot be cancelled"
    if appointment["status"] == "referred":
        return "This visit was referred to another doctor"
    if appointment["status"] == "cancelled":
        return "This appointment is already cancelled"
    if appointment["ends_at"] <= now:
        return "This visit is over: it cannot be cancelled"
    if not is_staff and appointment["starts_at"] <= now:
        return "Your visit has already started: please speak to the clinic"
    return "This appointment cannot be cancelled"


@router.post("/appointments/{appointment_id}/complete")
async def complete_appointment(appointment_id: str, user: User = Depends(current_user)):
    """Clinic staff mark a visit as completed (it took place): the patient can no longer
    cancel or change it."""
    done = await scheduling.complete(appointment_id, user.id, user.orgs_with(*STAFF_ROLES), user.platform_admin)
    if done is None:
        appointment = await scheduling.get(appointment_id)
        if appointment is None or not (user.platform_admin or str(appointment["org_id"]) in user.orgs_with(*STAFF_ROLES)):
            raise HTTPException(404, "No such appointment")
        raise HTTPException(409, f"This visit is {appointment['status']}: only a booked visit can be marked completed")
    db.audit_later(user.id, "complete_appointment", case_id=done["case_id"], org_id=done["org_id"],
                   detail={"appointment_id": appointment_id})
    return {"completed": True}


class Referral(BaseModel):
    org_id: str
    doctor_id: str
    starts_at: datetime
    note: str = Field(min_length=1, max_length=2000)  # for the receiving doctor: why, what to look at


@router.post("/appointments/{appointment_id}/refer")
async def refer_appointment(appointment_id: str, referral: Referral, user: User = Depends(current_user)):
    if not user.has_role("doctor"):
        raise HTTPException(404, "No such appointment")
    if referral.starts_at.tzinfo is None:
        raise HTTPException(400, "starts_at needs a time zone")
    note = referral.note.strip()
    if not note:
        raise HTTPException(400, "Write a note for the receiving doctor")
    try:
        doctor_id = str(uuid.UUID(referral.doctor_id))
    except ValueError:
        raise HTTPException(404, "No such doctor at that hospital")
    try:
        new, moved = await scheduling.refer(appointment_id, _org_key(referral.org_id), doctor_id,
                                            referral.starts_at, note, user.id, user.orgs_with("doctor"))
    except scheduling.BookingError as e:
        raise HTTPException(e.status, str(e))
    db.audit_later(user.id, "refer_appointment", case_id=new["case_id"], org_id=new["org_id"],
                   detail={"from_appointment_id": appointment_id, "to_appointment_id": str(new["appointment_id"]),
                           "to_org_id": str(new["org_id"]), "to_doctor_id": doctor_id, "moved": moved})
    return {"appointment": new, "moved": moved}


# ---- clinic staff

@router.get("/orgs/{org_id}/appointments")
async def clinic_appointments(org_id: str, day: Optional[date] = None, days: Optional[int] = Query(None, ge=1, le=14),
                              user: User = Depends(current_user)):
    """One day ({timezone, day, ..., appointments}); with ?days=N, {"days": [N of those]}."""
    org_id = _org_key(org_id)
    if not (user.platform_admin or org_id in user.orgs_with(*STAFF_ROLES)):
        raise HTTPException(403, "Only this clinic's staff can see its appointments")
    result = await scheduling.for_clinic_days(org_id, day, days or 1)
    if not (org_id in user.orgs_with("doctor", "nurse")):
        for one in result:
            one["appointments"] = [_without_note(a) for a in one["appointments"]]
    db.audit_later(user.id, "list_appointments", org_id=org_id,
                   detail={"day": result[0]["day"].isoformat(), "days": len(result),
                           "count": sum(len(one["appointments"]) for one in result)})
    return {"days": result} if days else result[0]


# ---- clinic admins: hours, slots, doctors

class Schedule(BaseModel):
    timezone: str
    slot_minutes: int = Field(ge=5, le=240)
    patients_per_slot: int = Field(ge=1, le=50)
    days_ahead: int = Field(ge=1, le=90)
    opening_hours: list[Optional[tuple[HHMM, HHMM]]] = Field(min_length=7, max_length=7)  # Monday first

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, name):
        try:
            ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"unknown time zone {name!r} (use a name like Asia/Kolkata)")
        return name

    @field_validator("opening_hours")
    @classmethod
    def ordered_hours(cls, days):
        return [_check_range(day, "opening hours") if day else None for day in days]


class Availability(BaseModel):
    weekly: list[list[tuple[HHMM, HHMM]]] = Field(min_length=7, max_length=7)  # Monday first
    days_off: list[date] = Field(default_factory=list, max_length=366)

    @field_validator("weekly")
    @classmethod
    def ordered_ranges(cls, days):
        for ranges in days:
            if len(ranges) > 6:
                raise ValueError("at most 6 time ranges a day")
            for pair in ranges:
                _check_range(pair, "doctor's hours")
        return [sorted(ranges) for ranges in days]


@router.get("/orgs/{org_id}/schedule")
async def get_clinic_schedule(org_id: str, user: User = Depends(current_user)):
    _require_manager(user, org_id)
    schedule, doctors = await asyncio.gather(scheduling.get_schedule(org_id), scheduling.doctors(org_id))
    return {"schedule": schedule, "doctors": doctors}


@router.put("/orgs/{org_id}/schedule")
async def save_clinic_schedule(org_id: str, schedule: Schedule, user: User = Depends(current_user)):
    _require_manager(user, org_id)
    await scheduling.save_schedule(org_id, schedule.model_dump(mode="json"), user.id)
    db.audit_later(user.id, "save_schedule", org_id=org_id, detail=schedule.model_dump(mode="json"))
    return {"saved": True}


@router.put("/orgs/{org_id}/doctors/{doctor_id}/availability")
async def save_doctor_availability(org_id: str, doctor_id: str, availability: Availability,
                                   user: User = Depends(current_user)):
    _require_manager(user, org_id)
    if not (scheduling._uuid(doctor_id) and await scheduling.save_availability(
            org_id, doctor_id, availability.model_dump(mode="json")["weekly"], availability.days_off, user.id)):
        raise HTTPException(404, "This person is not a doctor of the clinic")
    db.audit_later(user.id, "save_doctor_availability", org_id=org_id,
                   detail={"doctor_id": doctor_id, **availability.model_dump(mode="json")})
    return {"saved": True}
