from dotenv import load_dotenv
from fastmcp import FastMCP
from redis_test import redis_db
from redis_test import Finding, Findings
from case_store import save_original
from kafka_client import create_producer
from patient_extraction import empty_case
import asyncio
import json
from contextlib import asynccontextmanager
from facts_schema import ChecklistItem
from typing import Literal
from pydantic import BaseModel
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
import os

load_dotenv()

class TopicUpdate(BaseModel):
    item: ChecklistItem
    status: Literal["covered", "unknown_or_declined", "not_relevant"]

@asynccontextmanager
async def lifespan(server):
    global producer
    producer = create_producer()
    await producer.start()

    try:
        yield
    finally:
        await producer.stop()

# create instance of mcp
mcp = FastMCP("patient-case-taking", lifespan=lifespan, auth=StaticTokenVerifier(
    tokens={
        os.environ["MCP_SECRET"]: {
        "client_id": "foundry",
        "scopes" : []
        }
    }
))

r = redis_db()

@mcp.tool()
async def get_current_case_record(case_id: str) -> Findings:
    """Retrieve all clinical findings currently recorded for the patient case.
    
    Use this during an ongoing patient interview when you need to review
    information that has already been collected.
    """
    return await r.get_record_notes(case_id)

@mcp.tool()
async def record_answer(case_id: str, new_findings: list[Finding], topics: list[TopicUpdate]) -> dict:
    """
    Save everything one patient answer gave, in one call: the new clinical findings and the
    status of every checklist topic the answer touched.

    Call it once after each patient answer. new_findings may be an empty list when the answer
    holds nothing new; topics lists each topic the answer touched (covered = answered,
    unknown_or_declined = the patient did not know or refused, not_relevant = does not apply
    to this complaint). Returns the topics still pending and the next one to ask about.
    """

    if not await r.get_owner(case_id):
        return {"ok": False, "error": "unknown or expired case_id"}

    writes = [r.set_topics(case_id, {t.item: t.status for t in topics})] if topics else []
    if new_findings:
        writes.append(r.set_record_note(case_id, Findings(findings=new_findings)))
    await asyncio.gather(*writes)

    checklist = await r.get_checklist(case_id)
    pending = [i for i, s in checklist.items() if s == "pending"]
    if pending:
        now = f"Now speak: a very short acknowledgement, then one question about {pending[0]}."
    else:
        now = "All topics are done. Now read the summary back to the patient and ask them to confirm it."
    return {"ok": True, "pending": pending, "next_topic": pending[0] if pending else None, "now": now}

@mcp.tool()
async def flag_urgent(case_id: str, symptom: str, evidence: str,
                      reason: Literal["red_flag", "patient_emphasized"] = "red_flag") -> dict:
    """Call at once when the patient reports a possible emergency (chest pain now,
    breathlessness or difficulty breathing, coughing up blood, fainting, collapse,
    confusion, blue lips, sudden weakness or numbness, thoughts of self-harm), or stresses
    that a symptom is severe, worsening or worrying (reason patient_emphasized).
    symptom = the symptom in a few words. evidence = the patient's own words."""

    if not await r.get_owner(case_id):
        return {"ok": False, "error": "unknown or expired case_id"}

    await r.add_flag(case_id, {"symptom": symptom.strip()[:100], "reason": reason,
                               "evidence": evidence.strip()[:300]})
    return {"ok": True}

@mcp.tool
async def finish_interview(case_id: str) -> dict:
    """Call once, after the patient has confirmed the read-back summary
    (or at the end of an urgent stop). It stores the interview for the doctor."""
    
    record = await r.get_record_notes(case_id)
    owner = await r.get_owner(case_id)          # {"user_id", "org_id"}
    
    if not owner:
        return {"ok": False, "error": "unknown or expired case_id"}
    
    checklist = await r.get_checklist(case_id)
    flags = await r.get_flags(case_id)
    urgent = any(f["reason"] == "red_flag" for f in flags)
    missing = [i for i in ("allergies", "current_medications") if checklist[i] == "pending"]
    if missing and not urgent:
        return {"ok": False, "error": f"Ask the patient about {', '.join(missing)} first, then call again."}

    if not await r.r.set(f"case:{case_id}:finished", "1", nx=True, ex=3600):
        return {"ok": True}

    notes = {}
    for f in record.findings:
        notes.setdefault(f.section, []).append(f.text)

    case = empty_case()
    complaint = "; ".join(notes.get("presenting_complaint", []))
    if complaint:
        case["chief_complaint"] = {"text": complaint, "evidence": complaint}
    case["interview_notes"] = notes             # every finding, by section
    case["important_reported_symptoms"] = flags  # shown as Flags in the summary

    

    try:
        await save_original(case_id, case, checklist, [], False, None, owner["user_id"], owner["org_id"],
                            doctor_id=owner.get("doctor_id"), shared_documents=owner.get("document_ids"))
        
        await producer.send_and_wait("case-summary", json.dumps(
            {"session_id": case_id, "patient_id": case_id, "case": case, "review": None}
        ).encode("utf-8"))
    except Exception as e:
        print(f"finish_interview failed for {case_id}: {e!r}")
        await r.r.delete(f"case:{case_id}:finished")
        return {"ok": False, "error": "could not queue the summary"}

    await r.mark_stored(case_id)                # the web app waits for this key to move the patient on
    return {"ok": True}


@mcp.tool()
async def get_interview_checklist(case_id: str) -> dict:
    """The interview's topics and their status (covered, unknown_or_declined, not_relevant,
    pending). Ask about the pending ones; never ask again about the others."""
    checklist = await r.get_checklist(case_id)
    return {"checklist": checklist, "pending": [i for i, s in checklist.items() if s == "pending"]}


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8001)