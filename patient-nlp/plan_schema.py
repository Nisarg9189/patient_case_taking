"""Pydantic model for one planning call (see patient_extraction.plan_turn and PLANNING_PROMPT).

Field order matters: next_step comes before the checklist, so the next question can be
used as soon as it has been streamed, and later_questions is last so it never delays it.

Everything the interview runs on is required. The few fields it can do without have
defaults, so a plan is not thrown away (and the patient kept waiting for a retry) because
Gemma left one out: it sometimes omits next_step.reason, and at temperature 0 the retry
repeats the omission.
"""
from typing import Literal, Optional

from facts_schema import ChecklistItem, Strict

TopicStatus = Literal["covered", "unknown_or_declined", "not_relevant", "pending"]


class AnswerCheck(Strict):
    status: Literal["answered", "partially_answered", "not_answered", "no_question"]
    note: Optional[str] = None


class NextStep(Strict):
    action: Literal["ask", "finish"]
    item: Optional[ChecklistItem]
    question: Optional[str]
    reason: str = ""  # only for the logs


class Checklist(Strict):
    # same items, in the same order, as patient_extraction.CHECKLIST_ITEMS
    chief_complaint: TopicStatus
    onset_or_progression: TopicStatus
    severity_rating: TopicStatus
    associated_symptoms: TopicStatus
    current_medications: TopicStatus
    allergies: TopicStatus
    medical_history: TopicStatus
    vital_signs: TopicStatus
    oxygen_saturation: TopicStatus
    smoking_or_alcohol: TopicStatus
    recent_travel_or_sick_contacts: TopicStatus
    family_history: TopicStatus
    vaccination_status: TopicStatus


class LaterQuestion(Strict):
    item: ChecklistItem
    question: str


class Plan(Strict):
    answer_check: AnswerCheck
    next_step: NextStep
    checklist: Checklist
    later_questions: list[LaterQuestion] = []


class EarlyPlan(Strict):
    """The part of a Plan that is used before the rest has been streamed."""
    answer_check: AnswerCheck
    next_step: NextStep
