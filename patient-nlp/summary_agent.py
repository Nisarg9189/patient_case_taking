from dotenv import load_dotenv
import os
from typing import Optional
from langchain_google_genai import ChatGoogleGenerativeAI
import json
from langchain_core.messages import SystemMessage, HumanMessage
from kafka_client import create_consumer
import case_store
from pydantic import BaseModel, Field

load_dotenv()

class summary_output(BaseModel):
    flags: list[str] = Field(default_factory=list, description="each item of important_reported_symptoms as the patient reported it, no interpretation; empty list if none")
    presenting_complaint: str = Field(description="one sentence: the main complaint, onset or duration, course, severity")
    history_of_presenting_complaint: Optional[str] = Field(None, description="2-4 sentences: each symptom with its character, location, timing, triggers and relieving factors; associated symptoms")
    allergies: str = Field(description='the named substances with their reactions, or exactly "None known (patient-reported)", "Asked, no answer" or "Not asked"')
    current_medicines: str = Field(description='from regular_medications and recent_medications, or exactly "None (patient-reported)", "Asked, no answer" or "Not asked"')
    past_medical_history: Optional[str] = Field(None, description="null if nothing in the record")
    family_history: Optional[str] = Field(None, description="null if nothing in the record")
    social_history: Optional[str] = Field(None, description="null if nothing in the record")
    travel_contacts: Optional[str] = Field(None, description="null if nothing in the record")
    vaccinations: Optional[str] = Field(None, description="null if nothing in the record")
    vitals_reported_by_patient: Optional[str] = Field(None, description="readings as the patient reported them; null if none")
    pertinent_negatives: Optional[str] = Field(None, description='symptoms with status "absent" and topics the patient said no to; null if none')
    

llm = ChatGoogleGenerativeAI(
    model="gemini-3.8-flash",
    google_api_key=os.getenv("GEMINI_API_KEY"),
    thinking_level="low"
)

SUMMARY_PROMPT = """
You write the short pre-visit summary a clinician reads before seeing the patient. The
input is the case record from a spoken intake interview (JSON). In it, every "evidence"
field is what the patient actually said.

RULES
- Use only what is in the case record. Never add a diagnosis, a likely cause, advice,
  treatment or reassurance, and never guess a value that is missing.
- Keep the patient's meaning and every number exactly as recorded; you may shorten the
  wording. Readings are "reported by the patient", not measured by staff.
- Allergies: always state exactly one of: the named substances with their reactions;
  "None known (patient-reported)"; "Asked, no answer" (when "allergies" is in
  asked_without_answer); "Not asked".
- Current medicines: the same rule, from regular_medications and recent_medications;
  "None (patient-reported)" when negative_answers has current_medications.
- Symptoms with status "absent" are pertinent negatives, not symptoms. For "uncertain"
  ones, say the patient was unsure.
- Flags: list items of important_reported_symptoms as the patient reported them, with
  no interpretation. Write "Flags: none" if there are none.
- A section with nothing in the record is left out, except Allergies and Current
  medicines, which are always shown.
- Write for a clinician: plain, compact sentences, no filler, under 200 words.

FORMAT (plain text, these headings, in this order)
Flags:
Presenting complaint: one sentence: the main complaint, onset or duration, course, severity.
History of presenting complaint: 2-4 sentences: each symptom with its character,
  location, timing, triggers and relieving factors; associated symptoms.
Allergies:
Current medicines:
Past medical history:
Family history:
Social history:
Travel / contacts:
Vaccinations:
Vitals reported by patient:
Pertinent negatives:
""".strip()


with_structured_llm = llm.with_structured_output(summary_output, method="json_schema")

async def summarize_patient_case() -> str:

    consumer = create_consumer(
        "case-summary",
        "summaty-agent"
    )
    
    await consumer.start()
    
    content = ""
    
    try:
        
        print("Waiting for Cases")
        
        async for msg in consumer:
            
            data = json.loads(msg.value.decode("utf-8"))
        
            message = "CASE RECORD (JSON):\n" + json.dumps(data["case"], ensure_ascii=False, indent=1)
            
            response = await with_structured_llm.ainvoke([SystemMessage(content=SUMMARY_PROMPT), HumanMessage(content=message)])
            content = response.model_dump()
            
            print(content)
            
            await case_store.save_summary(data['session_id'], content)
    
    finally:
        await consumer.stop()
    


if __name__ == "__main__":
    import asyncio
    
    asyncio.run(summarize_patient_case())