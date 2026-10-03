"""Basic connection example.
"""
from dotenv import load_dotenv
import redis.asyncio as redis
from pydantic import BaseModel, Field
from typing import Literal
import json
import asyncio
from patient_extraction import CHECKLIST_ITEMS
import os

load_dotenv()

class Finding(BaseModel):
    section: Literal[
        "presenting_complaint",
        "history_of_presenting_complaint",
        "allergies",
        "current_medicines",
        "past_medical_history",
        "family_history",
        "social_history",
        "travel_contacts",
        "vaccinations",
        "vitals_reported_by_patient",
        "pertinent_negatives"
    ] = Field(
        description=(
            "The clinical section where the extracted information belongs. "
            "Choose exactly one of the allowed sections."
        )
    )

    text: str = Field(
        description=(
            "A concise statement containing only the clinically relevant "
            "information explicitly provided by the patient. Do not infer "
            "or invent information that the patient did not state."
        )
    )


class Findings(BaseModel):
    findings: list[Finding] = Field(
        description=(
            "List of all clinically relevant findings extracted from the "
            "patient's response. Include multiple findings when the response "
            "contains information belonging to different clinical sections."
        )
    )

class redis_db:
    
    def __init__(self):
        self.r = redis.Redis(
            host=os.environ["REDIS_HOST"],
            port=int(os.environ["REDIS_PORT"]),
            decode_responses=True,
            username=os.environ["REDIS_USERNAME"],
            password=os.environ["REDIS_PASSWORD"],
        )
        
    
    async def get_record_notes(self, case_id: str) -> Findings:
        
        result = await self.r.get(
            f"case:{case_id}:record_notes"
        )
        
        if result is None:
            return Findings(findings=[])
        
        print(result)
        
        return Findings(
        findings=[
            Finding(**finding)
            for finding in json.loads(result)
        ]
    )
    
    async def set_record_note(self, case_id: str, new_findings: Findings) -> Findings:
    
        current = await self.get_record_notes(case_id)
        
        current.findings.extend(
            new_findings.findings
        )
        
        await self.r.set(
            f"case:{case_id}:record_notes",
            json.dumps(
                [finding.model_dump() for finding in current.findings]
            ),
            ex=3600
        )
        
        print(current)
        
        return current
    
    async def set_owner(self, case_id: str, user_id: str, org_id: str, doctor_id: str | None = None,
                        document_ids: list[str] | None = None) -> None:
        await self.r.set(
            f"case:{case_id}:owner",
            json.dumps({"user_id": user_id, "org_id": org_id, "doctor_id": doctor_id, "document_ids": document_ids}),
            ex=3600
        )
    
    async def get_owner(self, case_id: str) -> dict | None :
        result = await self.r.get(f"case:{case_id}:owner")
        return json.loads(result) if result else None
    
    async def get_checklist(self, case_id: str) -> dict:
        data = await self.r.hgetall(f"case:{case_id}:checklist")
        return {item: data.get(item, "pending") for item in CHECKLIST_ITEMS}

    async def set_topics(self, case_id: str, updates: dict) -> None:
        key = f"case:{case_id}:checklist"
        async with self.r.pipeline(transaction=True) as pipe:
            pipe.hset(key, mapping=updates)
            pipe.expire(key, 3600)
            await pipe.execute()

    async def mark_stored(self, case_id: str) -> None:
        await self.r.set(f"case:{case_id}:stored", "1", ex=3600)

    async def is_stored(self, case_id: str) -> bool:
        return bool(await self.r.exists(f"case:{case_id}:stored"))

    async def add_flag(self, case_id: str, flag: dict) -> None:
        key = f"case:{case_id}:flags"
        async with self.r.pipeline(transaction=True) as pipe:
            pipe.rpush(key, json.dumps(flag))
            pipe.expire(key, 3600)
            await pipe.execute()

    async def get_flags(self, case_id: str) -> list[dict]:
        result = await self.r.lrange(f"case:{case_id}:flags", 0, -1)
        return [json.loads(flag) for flag in result]

        
    

async def main():

    r = redis_db()
    
    new_findings = Findings(
    findings=[
        Finding(
            section="presenting_complaint",
            text="Patient reports fever and headache."
        ),
        Finding(
            section="history_of_presenting_complaint",
            text="Symptoms started 2 days ago."
        ),
        Finding(
            section="allergies",
            text="Patient reports an allergy to penicillin."
        )
    ]
)
    
    await r.set_record_note("1234", new_findings)
    
    result = await r.get_record_notes("123")
    
    print(result)
        
if __name__ == "__main__":
    
    asyncio.run(main())
