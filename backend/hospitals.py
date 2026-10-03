"""How a patient finds a hospital and a doctor: by state and district, best rated first.

  GET /api/states                          the states and union territories (for a clinic's settings)
  GET /api/hospitals/locations             the states and districts that have a listed hospital
  GET /api/hospitals?state=&district=&sort=rating|fee    the hospitals there, with rating and fee
  GET /api/hospitals/{org_id}              one hospital: details, its doctors, reviews, the caller's rating
  PUT /api/hospitals/{org_id}/rating       a patient rates a hospital they have booked a visit at (1-5)

A hospital is listed when its clinic admin has set its state and district (Clinic schedule page)
and it has at least one doctor. The interview a patient does after choosing a hospital and a
doctor goes to them (voice_agent.py), and the booking is with that doctor.
"""
import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

import db
from auth import User, current_user
from india import STATES

router = APIRouter(prefix="/api")

# only hospitals that can take the patient through the flow: a place, and a doctor
_LISTED = """
    o.state <> '' AND o.district <> ''
    AND EXISTS (SELECT 1 FROM memberships m WHERE m.org_id = o.org_id AND m.role = 'doctor')"""

_RATINGS = "LEFT JOIN (SELECT org_id, avg(rating)::float AS avg, count(*) AS n FROM hospital_ratings GROUP BY org_id) r USING (org_id)"


def _key(org_id):
    try:
        return uuid.UUID(str(org_id))
    except ValueError:
        raise HTTPException(404, "No such hospital")


def _rating(row):
    return {"rating": round(row["avg"], 1) if row["avg"] is not None else None, "rating_count": row["n"] or 0}


@router.get("/states")
async def states(user: User = Depends(current_user)):
    return STATES


@router.get("/hospitals/locations")
async def locations(user: User = Depends(current_user)):
    database = await db.pool()
    rows = await database.fetch(
        f"""
        SELECT o.state, min(o.district) AS district FROM organizations o
        WHERE {_LISTED} GROUP BY o.state, lower(o.district) ORDER BY o.state, min(o.district)
        """
    )
    grouped = {}
    for row in rows:
        grouped.setdefault(row["state"], []).append(row["district"])
    return [{"state": state, "districts": districts} for state, districts in grouped.items()]


@router.get("/hospitals")
async def hospitals(state: str, district: str, sort: str = Query("rating", pattern="^(rating|fee)$"),
                    user: User = Depends(current_user)):
    order = ("r.avg DESC NULLS LAST, r.n DESC NULLS LAST, o.name" if sort == "rating"
             else "o.consultation_fee ASC NULLS LAST, r.avg DESC NULLS LAST, o.name")
    database = await db.pool()
    rows = await database.fetch(
        f"""
        SELECT o.org_id, o.name, o.state, o.district, o.address, o.consultation_fee, r.avg, r.n,
               (SELECT count(*) FROM memberships m WHERE m.org_id = o.org_id AND m.role = 'doctor') AS doctor_count,
               ARRAY(SELECT DISTINCT p.specialty FROM memberships m JOIN doctor_profiles p ON p.user_id = m.user_id
                     WHERE m.org_id = o.org_id AND m.role = 'doctor' AND p.specialty <> '' ORDER BY 1) AS specialties
        FROM organizations o {_RATINGS}
        WHERE {_LISTED} AND o.state = $1 AND lower(o.district) = lower($2)
        ORDER BY {order}
        """,
        state, district,
    )
    return [{**{k: row[k] for k in ("org_id", "name", "state", "district", "address", "consultation_fee",
                                     "doctor_count", "specialties")}, **_rating(row)} for row in rows]


@router.get("/hospitals/{org_id}")
async def hospital(org_id: str, user: User = Depends(current_user)):
    org, me = _key(org_id), uuid.UUID(user.id)
    database = await db.pool()
    row, doctors, reviews, mine = await asyncio.gather(
        database.fetchrow(
            f"""
            SELECT o.org_id, o.name, o.state, o.district, o.address, o.phone, o.email, o.registration_number,
                   o.description, o.consultation_fee, r.avg, r.n,
                   EXISTS (SELECT 1 FROM appointments a WHERE a.org_id = o.org_id AND a.patient_user_id = $2
                           AND a.status IN ('booked', 'completed', 'referred')) AS can_rate
            FROM organizations o {_RATINGS} WHERE o.org_id = $1 AND {_LISTED}
            """,
            org, me,
        ),
        database.fetch(
            """
            SELECT m.user_id AS doctor_id, coalesce(nullif(u.name, ''), split_part(u.email, '@', 1)) AS name,
                   coalesce(p.qualification, '') AS qualification, coalesce(p.specialty, '') AS specialty
            FROM memberships m JOIN neon_auth."user" u ON u.id = m.user_id
            LEFT JOIN doctor_profiles p ON p.user_id = m.user_id
            WHERE m.org_id = $1 AND m.role = 'doctor' ORDER BY name
            """,
            org,
        ),
        database.fetch(
            "SELECT rating, comment, updated_at AS at FROM hospital_ratings WHERE org_id = $1 AND comment <> '' "
            "ORDER BY updated_at DESC LIMIT 5",
            org,
        ),
        database.fetchrow("SELECT rating, comment FROM hospital_ratings WHERE org_id = $1 AND patient_user_id = $2", org, me),
    )
    if row is None:
        raise HTTPException(404, "No such hospital")
    return {
        **{k: row[k] for k in ("org_id", "name", "state", "district", "address", "phone", "email",
                               "registration_number", "description", "consultation_fee", "can_rate")},
        **_rating(row),
        "doctors": [dict(d, doctor_id=str(d["doctor_id"])) for d in doctors],
        "reviews": [dict(r) for r in reviews],
        "my_rating": dict(mine) if mine else None,
    }


class Rating(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: str = Field("", max_length=500)


@router.put("/hospitals/{org_id}/rating")
async def rate(org_id: str, body: Rating, user: User = Depends(current_user)):
    if not user.has_role("patient"):
        raise HTTPException(403, "Only patients can rate a hospital.")
    org, me = _key(org_id), uuid.UUID(user.id)
    database = await db.pool()
    saved = await database.fetchval(
        """
        INSERT INTO hospital_ratings (org_id, patient_user_id, rating, comment)
        SELECT $1, $2, $3, $4
        WHERE EXISTS (SELECT 1 FROM appointments WHERE org_id = $1 AND patient_user_id = $2
                      AND status IN ('booked', 'completed', 'referred'))
        ON CONFLICT (org_id, patient_user_id) DO UPDATE SET rating = $3, comment = $4, updated_at = now()
        RETURNING rating
        """,
        org, me, body.rating, body.comment.strip(),
    )
    if saved is None:
        raise HTTPException(403, "You can rate a hospital after you have booked a visit there.")
    db.audit_later(user.id, "rate_hospital", org_id=org, detail={"rating": body.rating})
    return {"rating": saved}
