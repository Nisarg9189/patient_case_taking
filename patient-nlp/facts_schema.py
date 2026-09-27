"""Pydantic models for the facts extracted from one patient answer.

Field names match the case record built by patient_extraction.merge_into_case(), so
PatientFacts(...).model_dump() can be merged directly. Every field is required (optional
values are written as null), which strict structured output needs.
"""
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

# same topics, in the same order, as patient_extraction.CHECKLIST_ITEMS
ChecklistItem = Literal[
    "chief_complaint", "onset_or_progression", "severity_rating", "associated_symptoms",
    "current_medications", "allergies", "medical_history", "vital_signs", "oxygen_saturation",
    "smoking_or_alcohol", "recent_travel_or_sick_contacts", "family_history", "vaccination_status",
]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Quote(Strict):
    text: str
    evidence: str


class Symptom(Strict):
    name: str
    status: Literal["present", "absent", "uncertain"]
    location: Optional[str]
    character: Optional[str]
    severity: Optional[str]
    duration: Optional[str]
    onset: Optional[str]
    frequency: Optional[str]
    triggers: list[str]
    relieving_factors: list[str]
    evidence: str


class VitalSign(Strict):
    name: str
    value: str
    unit: Optional[str]
    approximate: bool
    measured_when: Optional[str]
    evidence: str


class Condition(Strict):
    condition: str
    # how long the patient has had it ("5 years"); a default so a reply without it still parses
    duration: Optional[str] = None
    evidence: str


class FamilyCondition(Strict):
    relative: str
    condition: str
    evidence: str


class SocialFact(Strict):
    topic: Literal["smoking", "vaping", "alcohol", "recreational_drugs", "occupation", "other"]
    detail: str
    evidence: str


class TravelOrContact(Strict):
    type: Literal["travel", "sick_contact"]
    detail: str
    evidence: str


class Vaccination(Strict):
    vaccine: str
    detail: str
    evidence: str


class RegularMedication(Strict):
    name: str
    dose: Optional[str]
    frequency: Optional[str]
    evidence: str


class RecentMedication(Strict):
    name: str
    dose: Optional[str]
    frequency: Optional[str]
    when: Optional[str]
    evidence: str


class Allergy(Strict):
    substance: str
    reaction: Optional[str]
    evidence: str


class Allergies(Strict):
    status: Literal["reported", "none_reported", "not_mentioned"]
    evidence: Optional[str]
    items: list[Allergy]


class RespiratoryInformation(Strict):
    cough: Optional[str]
    breathing_at_rest: Optional[str]
    breathing_on_exertion: Optional[str]
    sputum: Optional[str]
    wheeze: Optional[str]
    pain_on_breathing: Optional[str]
    other: Optional[str]
    evidence: list[str]


class FlaggedSymptom(Strict):
    symptom: str
    reason: Literal["patient_emphasized", "red_flag"]
    evidence: str


class NegativeAnswer(Strict):
    item: ChecklistItem
    answer: str
    evidence: str


class PatientFacts(Strict):
    """Facts stated in the latest patient answer (see EXTRACTION_PROMPT for the rules)."""

    chief_complaint: Optional[Quote]
    overall_severity: Optional[Quote]
    symptoms: list[Symptom]
    vital_signs: list[VitalSign]
    medical_history: list[Condition]
    family_history: list[FamilyCondition]
    social_history: list[SocialFact]
    travel_and_contacts: list[TravelOrContact]
    vaccinations: list[Vaccination]
    regular_medications: list[RegularMedication]
    recent_medications: list[RecentMedication]
    allergies: Allergies
    respiratory_information: RespiratoryInformation
    important_reported_symptoms: list[FlaggedSymptom]
    negative_answers: list[NegativeAnswer]
