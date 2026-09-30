import json

from dotenv import load_dotenv
from typing_extensions import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command, interrupt
from patient_extraction import (
    start_conversation, plan_turn, extract_facts, apply_facts, enforce_safety,
    empty_case, end_conversation, NO_ANSWER, CHECKLIST_ITEMS, SAFETY_QUESTIONS,
)
from typing import Annotated
import asyncio
import re
from google import genai
import os
from langgraph.graph.message import add_messages
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from langgraph.checkpoint.memory import InMemorySaver
from gemini_voice_agent import (
    ask_next_question_voice_agent, close_producer, prepare_next_session, close_spare_session,
)
from audio_io import close_local_audio
import uuid
import redis.asyncio as redis
import traceback

from latency_log import now, log_step, log_since
from kafka_client import create_producer


load_dotenv()

redis_client = redis.Redis(
    host=os.getenv("REDIS_HOST"),
    port=int(os.getenv("REDIS_PORT")),
    username=os.getenv("REDIS_USERNAME"),
    password=os.getenv("REDIS_PASSWORD"),
    decode_responses=True,
)


client = genai.Client(
    api_key=os.getenv("GEMINI_API_KEY")
)


MODEL = os.getenv("GEMINI_MODEL") or "gemini-3.8-live"


class State(TypedDict):
    patient_id: str
    session_id: str
    openai_conversation_id: str
    turn_id: str
    answered_at: float
    messages: Annotated[list[BaseMessage], add_messages]
    patient_text: str
    current_question: str
    patient_answer: str
    extracted_data: dict
    checklist: dict
    next_questions: list[str]
    no_of_questions_remaining_to_ask: int
    agent_error: str | None
    agent_errors: int
    interview_aborted: bool
    abort_reason: str
    case_taking_complete: bool
    # the patient's check of the case before it is final (patient_review)
    patient_review: dict | None     # {summary section label: text} as the patient saved it
    review_confirmed: bool
    i: int


def question_answer_pairs(messages):
    """[(question or None, answer), ...] for every patient message, in order."""
    pairs = []
    for i, message in enumerate(messages):
        if message.type == "human":
            previous = messages[i - 1] if i > 0 else None
            question = previous.content if previous is not None and previous.type == "ai" else None
            pairs.append((question, message.content))
    if not pairs:
        raise ValueError("no patient message in state")
    return pairs


# Fact extraction runs in the background while the next question is being asked and
# answered; the finished facts are merged on the next turn. Tasks live in this process,
# like the InMemorySaver checkpoints.
_pending_facts: dict[str, asyncio.Task] = {}


FACTS_RETRY_DELAY_SECONDS = 3


async def _extract_facts_task(case, question, answer, earlier_turns):
    known_symptoms = [s["name"] for s in case["symptoms"]]
    known_conditions = [c["condition"] for c in case["medical_history"]]
    try:
        new_facts = await extract_facts(answer, question, earlier_turns, known_symptoms, known_conditions)
    except Exception as e:
        # the patient has moved on, so a short wait costs nothing
        print(f"\n⚠️ Fact extraction failed, retrying once: {e!r}")
        await asyncio.sleep(FACTS_RETRY_DELAY_SECONDS)
        try:
            new_facts = await extract_facts(answer, question, earlier_turns, known_symptoms, known_conditions)
        except Exception as e:
            # the interview goes on without this answer's facts; for allergies and medicines
            # apply_facts() records the topic as asked without an answer (never a false "no")
            print(f"\n❌ Fact extraction failed twice, facts from this answer are missing: {e!r}\n"
                  f"   question: {question!r}\n   answer: {answer!r}")
            new_facts = None
    return new_facts, question, answer


async def merge_pending_facts(session_id, case):
    task = _pending_facts.pop(session_id, None)
    if task is None:
        return case
    new_facts, question, answer = await task
    return apply_facts(case, new_facts, question, answer)


# Pipelining: the patient never waits for the planning call on their latest answer.
# The next question comes from the latest FINISHED plan (its next_step and
# later_questions), skipping topics asked since that plan was made; the plan for the
# latest answer runs in the background and steers the question after next. The
# interview only waits for planning on the first question, when the finished plan has
# nothing left to ask, and before finishing (the safety check needs everything).
#
# A planning call has to be complete before the next message goes into the same
# conversation, so each turn first awaits the previous one (normally long done).
# (index of the answer it plans from, task, urgent: set once a patient waits for it)
_pending_plans: dict[str, tuple[int, asyncio.Task, asyncio.Event]] = {}

# latest finished plan per session: {"plan": ..., "answers_seen": index of the answer it saw}
_latest_plans: dict[str, dict] = {}

# checklist item of each question asked, aligned with question_answer_pairs()
# (index 0 is the opening question, about the chief complaint: when it is asked again,
# that counts as asking chief_complaint, so a plan made earlier does not ask it a third time)
_asked_items: dict[str, list] = {}


async def finish_pending_plan(session_id):
    """Wait for the previous turn's planning call and keep it as the latest finished plan.
    A failure only loses that plan: the one before it stays in use."""
    pending = _pending_plans.pop(session_id, None)
    if pending is None:
        return
    answers_seen, task, urgent = pending
    urgent.set()  # this turn waits for it
    try:
        _latest_plans[session_id] = {"plan": await task, "answers_seen": answers_seen}
    except Exception as e:
        print(f"\n⚠️ Planning call failed, using the plan before it: {e!r}")


def forget_session(session_id):
    """Drop an interview's in-process state (used when a browser disconnects mid-interview;
    a normal finish already clears it in finish_interview)."""
    pending = _pending_plans.pop(session_id, None)
    if pending is not None:
        pending[1].cancel()
    facts = _pending_facts.pop(session_id, None)
    if facts is not None:
        facts.cancel()
    _latest_plans.pop(session_id, None)
    _asked_items.pop(session_id, None)


# For safety topics an answer without a name is not enough ("I have a rash" to the allergy
# question was once recorded as an allergy to an "unspecified" substance).
SAFETY_FOLLOW_UPS = {
    "allergies": "What are you allergic to? Please name the medicine, food or other thing.",
    "current_medications": "What are the names of the medicines you are taking?",
}


def reask_if_unanswered(next_step, answer_status, pairs, asked_items):
    """Ask again once before moving on when the last question was not really answered.

    The planner decides re-asks, but it does not always follow its own rule (it moved on
    after one off-topic answer and marked the topic unknown), so the rule is also applied
    here: not answered -> the same question again; allergies or medicines only partly
    answered (no name) -> a clarifying question. Not when the topic was already asked
    twice, or when the planner is asking about the same topic itself.
    `asked_items` is aligned with `pairs` (the topic of each question).
    """
    question, asked_item = pairs[-1][0], asked_items[-1]
    if not question:
        return next_step
    if answer_status == "not_answered":
        again = question
    elif answer_status == "partially_answered" and asked_item in SAFETY_FOLLOW_UPS:
        again = SAFETY_FOLLOW_UPS[asked_item]
    else:
        return next_step
    already_asked_again = (len(pairs) >= 2 and pairs[-2][0] == question) or \
        (asked_item is not None and asked_items.count(asked_item) >= 2)
    planner_reasks = next_step["action"] == "ask" and next_step["item"] == asked_item
    if already_asked_again or planner_reasks:
        return next_step
    return {"action": "ask", "item": asked_item, "question": again,
            "reason": f"{answer_status.replace('_', ' ')}, asked again (checked in code)"}


# Asked right after the opening answer while the first planning call runs (that wait was
# ~3s of silence). The planner chose severity as its first question in every test.
FIRST_FOLLOW_UP = {
    "action": "ask",
    "item": "severity_rating",
    "question": "On a scale from 0 to 10, where 10 is the worst, how bad is it right now?",
    "reason": "first follow-up, asked while the first plan is made",
}


# Last resort if planning fails completely (plan_turn already retries; Ollama Cloud now and
# then never answers a request): the first topic not asked yet, in priority order.
FALLBACK_QUESTIONS = {
    "onset_or_progression": "When did this start, and is it getting better or worse?",
    "severity_rating": FIRST_FOLLOW_UP["question"],
    "associated_symptoms": "Have you noticed any other symptoms?",
    "current_medications": SAFETY_QUESTIONS["current_medications"],
    "allergies": SAFETY_QUESTIONS["allergies"],
    "medical_history": "Do you have any long-term medical conditions or past surgeries?",
    "vital_signs": "Have you measured your temperature, pulse or blood pressure recently? What were the readings?",
    "oxygen_saturation": "Have you checked your oxygen level with a pulse oximeter? What was it?",
    "smoking_or_alcohol": "Do you smoke or vape, and do you drink alcohol?",
    "recent_travel_or_sick_contacts": "Have you travelled recently or been around anyone who is sick?",
    "family_history": "Do any health conditions run in your family?",
    "vaccination_status": "Are your vaccinations up to date?",
}


def fallback_plan(asked_items):
    """A plan made without the model: ask the first topic not asked yet; finish once every
    topic has been asked. enforce_safety() still checks allergies and medicines."""
    pending = [item for item in CHECKLIST_ITEMS if item != "chief_complaint" and item not in asked_items]
    if pending:
        next_step = {"action": "ask", "item": pending[0], "question": FALLBACK_QUESTIONS[pending[0]],
                     "reason": "planning failed: next topic not asked yet"}
    else:
        next_step = {"action": "finish", "item": None, "question": None,
                     "reason": "planning failed: every topic asked"}
    return {
        "answer_check": {"status": "answered", "note": None},
        "next_step": next_step,
        "checklist": {item: "pending" if item in pending else "covered" for item in CHECKLIST_ITEMS},
        "later_questions": [],
    }


async def plan_or_fallback(plan_task, asked_items):
    try:
        return await plan_task
    except Exception as e:
        print(f"\n❌ Planning failed, continuing without it: {e!r}", flush=True)
        return fallback_plan(asked_items)


def states_a_problem(opening_answer):
    """Whether the opening answer can be followed by FIRST_FOLLOW_UP without waiting for the
    planner: not a question back ("Can you ask me again?") and at least a few words. Otherwise
    the planner decides (it asks the opening again)."""
    text = opening_answer.strip().lower()
    return text != NO_ANSWER and not text.endswith("?") and len(re.findall(r"[a-z0-9']+", text)) >= 3


def next_from_finished_plan(latest, pairs, asked_items):
    """The next question from a finished plan, or None if it has nothing left to ask.

    The plan saw the answers up to latest["answers_seen"]; topics asked after that are
    skipped. Its own re-ask (via reask_if_unanswered) comes first, then its next_step,
    then its later_questions.
    """
    plan, seen = latest["plan"], latest["answers_seen"]
    asked_since = {item for item in asked_items[seen + 1:] if item}

    candidates = [reask_if_unanswered(plan["next_step"], plan["answer_check"]["status"],
                                      pairs[:seen + 1], asked_items[:seen + 1])]
    if candidates[0] is not plan["next_step"] and plan["next_step"]["action"] == "ask":
        candidates.append(plan["next_step"])
    candidates += [{"action": "ask", "item": q["item"], "question": q["question"], "reason": "planned ahead"}
                   for q in plan.get("later_questions", [])[:3]]

    for step in candidates:
        if step["action"] == "ask" and step["question"] and step["item"] not in asked_since:
            return step
    return None


def _set_once(future, value):
    if not future.done():
        future.set_result(value)


async def extract_data(state: State) -> dict:
    session_id = state["session_id"]

    # one OpenAI conversation per interview session; it holds every earlier turn
    conversation_id = (state.get("openai_conversation_id")
                       or await start_conversation(session_id))

    pairs = question_answer_pairs(state["messages"])
    question, answer = pairs[-1]
    answer_index = len(pairs) - 1
    earlier_turns = [(q, a) for q, a in pairs[:-1] if a != NO_ANSWER]
    asked_items = _asked_items.setdefault(session_id, ["chief_complaint"])

    turn_id = state.get("turn_id")
    node_started_at = now()

    # the first question's Gemini session connects while this turn's planning runs
    prepare_next_session(client, MODEL, session_id)

    await finish_pending_plan(session_id)
    log_since(turn_id, "wait for previous planning call", node_started_at)

    # the previous answer's facts; normally done while this question was being asked
    facts_wait_started_at = now()
    case = await merge_pending_facts(session_id, state.get("extracted_data") or empty_case())
    log_since(turn_id, "wait for previous answer's facts", facts_wait_started_at)

    facts_task = None
    if answer == NO_ANSWER:
        case = apply_facts(case, None, question, answer)
    else:
        facts_task = asyncio.create_task(_extract_facts_task(case, question, answer, earlier_turns))

    # the planning call for this answer always starts; whether we wait for it is decided below
    planning_started_at = now()
    next_step_ready = asyncio.get_running_loop().create_future()
    urgent = asyncio.Event()  # set once the patient waits for this plan: it then goes first
    plan_task = asyncio.create_task(plan_turn(
        conversation_id, answer, question,
        lambda partial: _set_once(next_step_ready, partial),
        urgent.is_set,
    ))

    next_step = None
    if answer == NO_ANSWER and question and not (answer_index >= 1 and pairs[answer_index - 1][0] == question):
        # nothing was heard: ask the same question again right away (once)
        next_step = {"action": "ask", "item": asked_items[answer_index], "question": question,
                     "reason": "no answer, asked again"}
    elif session_id in _latest_plans:
        next_step = next_from_finished_plan(_latest_plans[session_id], pairs, asked_items)
    elif answer_index == 0 and states_a_problem(answer):
        next_step = dict(FIRST_FOLLOW_UP)

    if next_step is not None:
        log_step(turn_id, "next question chosen without waiting for planning", 0.0)
    else:
        # first question, or the finished plan has nothing left: wait for this answer's plan
        urgent.set()
        await asyncio.wait({next_step_ready, plan_task}, return_when=asyncio.FIRST_COMPLETED)
        partial = next_step_ready.result() if next_step_ready.done() else await plan_or_fallback(plan_task, asked_items)
        log_since(turn_id, "planning call until next question", planning_started_at)
        next_step = reask_if_unanswered(partial["next_step"], partial["answer_check"]["status"],
                                        pairs, asked_items[:answer_index + 1])

    if next_step["action"] == "finish":
        # the safety check before finishing needs the full plan and this answer's facts
        urgent.set()
        plan = await plan_or_fallback(plan_task, asked_items)
        _latest_plans[session_id] = {"plan": plan, "answers_seen": answer_index}
        if facts_task is not None:
            new_facts, _, _ = await facts_task
            case = apply_facts(case, new_facts, question, answer)
        checklist, next_step = enforce_safety(case, plan["checklist"], next_step)
        log_since(turn_id, "finish check: full plan + last facts", planning_started_at)
    else:
        _pending_plans[session_id] = (answer_index, plan_task, urgent)
        if facts_task is not None:
            _pending_facts[session_id] = facts_task
        # the newest checklist is still being written, so report the latest finished one
        latest = _latest_plans.get(session_id)
        checklist = enforce_safety(case, latest["plan"]["checklist"], next_step)[0] if latest else {}

    complete = next_step["action"] == "finish" or not next_step["question"]
    if not complete:
        asked_items.append(next_step["item"])

    log_since(turn_id, "extract_data total", node_started_at)
    log_since(turn_id, "answer -> next question decided", state.get("answered_at"))

    return {
        "openai_conversation_id": conversation_id,
        "extracted_data": case,
        "checklist": checklist,
        "next_questions": [] if complete else [next_step["question"]],
        "no_of_questions_remaining_to_ask": sum(v == "pending" for v in checklist.values()),
        "case_taking_complete": complete,
    }


async def prepare_review(state: State) -> dict:
    """The questions are done: fold in the last answer's facts, so the patient reviews the whole case.
    (A separate node from patient_review: a node that pauses is run again from the start
    when it resumes, and the pending facts can only be merged once.)"""
    if state["session_id"] in _pending_facts:
        return {"extracted_data": await merge_pending_facts(
            state["session_id"], state.get("extracted_data") or empty_case())}
    return {}


def patient_review(state: State) -> dict:
    """Pause until the patient has checked the case and pressed Save (LangGraph interrupt).

    The caller shows the interrupt's value (the case) to the patient and resumes the graph with
    Command(resume={"confirmed": bool, "sections": {label: text} | None}). Only an interview
    that ran to the end gets here; a timeout or an abort goes straight to finish_interview.
    """
    answer = interrupt({"case": state.get("extracted_data"), "checklist": state.get("checklist")})
    return {"patient_review": answer.get("sections"), "review_confirmed": bool(answer.get("confirmed"))}


def router_case_taking_complete(state: State) -> str:
    if state["case_taking_complete"]:
        return "END"

    return "next_question"


def router_next_question(state: State) -> str:
    if state["current_question"] is None:
        return "extract_data"

    return "ask_next_question"


def next_question(state: State) -> State:
    if state["next_questions"]:
        state["current_question"] = state["next_questions"].pop(0)
    else:
        state["current_question"] = None

    return state


MAX_CONSECUTIVE_AGENT_ERRORS = 3
AGENT_RETRY_DELAY_SECONDS = 2


async def ask_next_question(state: State) -> dict:
    errors_so_far = state.get("agent_errors", 0)
    if errors_so_far:
        await asyncio.sleep(AGENT_RETRY_DELAY_SECONDS * errors_so_far)

    result = await ask_next_question_voice_agent(
        state,
        client,
        MODEL,
    )

    # a technical failure (voice agent down) is retried; it is not the patient failing to answer
    if result.get("agent_error"):
        return {**result, "agent_errors": errors_so_far + 1}

    return {**result, "agent_error": None, "agent_errors": 0}


def abort_interview(state: State) -> dict:
    return {
        "interview_aborted": True,
        "abort_reason": (
            f"{state['agent_errors']} consecutive errors, last: {state['agent_error']}"
        ),
    }


async def finish_interview(state: State) -> dict:
    # every way the interview ends passes through here
    update = {}

    # let a still-streaming planning call finish before its conversation is deleted
    await finish_pending_plan(state["session_id"])
    _latest_plans.pop(state["session_id"], None)
    _asked_items.pop(state["session_id"], None)

    # an answer given just before a timeout or abort may still be extracting
    if state["session_id"] in _pending_facts:
        update["extracted_data"] = await merge_pending_facts(
            state["session_id"], state.get("extracted_data") or empty_case()
        )
        
    producer = create_producer()
    await producer.start()
    
    try:
        await producer.send_and_wait(
            "case-summary",
            json.dumps({
                "session_id": state["session_id"],
                "patient_id": state["patient_id"],
                "case": state.get("extracted_data") or empty_case(),
                "review": state.get("patient_review"),  # the patient's corrections, if they saved any
            }).encode("utf-8")
        )
    finally:
        await producer.stop()

    conversation_id = state.get("openai_conversation_id")
    if not conversation_id:
        return update

    try:
        await end_conversation(conversation_id)
    except Exception as e:
        # keep the id so the conversation can still be deleted later
        print(f"\n⚠️ Could not delete OpenAI conversation {conversation_id}: {e}")
        return update

    return {**update, "openai_conversation_id": ""}


def router_after_ask(state: State) -> str:
    if state.get("agent_error"):
        if state.get("agent_errors", 0) >= MAX_CONSECUTIVE_AGENT_ERRORS:
            return "abort"
        return "retry"

    if state.get("case_taking_complete"):
        return "END"

    if not state.get("patient_answer", "").strip():
        return "no_answer"

    # JEV only logs its opinion; the planner's answer_check decides whether to re-ask
    return "answered"


def no_answer(state: State) -> dict:
    # the placeholder turn tells the planner this question went unanswered; it decides
    # whether to ask again (once) or mark the topic unknown
    return {
        "messages": [
            {"role": "assistant", "content": state["current_question"]},
            {"role": "user", "content": NO_ANSWER},
        ],
    }


workflow = StateGraph(State)


workflow.add_node(
    "extract_data",
    extract_data
)

workflow.add_node(
    "ask_next_question",
    ask_next_question
)

workflow.add_node(
    "next_question",
    next_question
)

workflow.add_node(
    "no_answer",
    no_answer
)

workflow.add_node(
    "abort_interview",
    abort_interview
)

workflow.add_node(
    "finish_interview",
    finish_interview
)

workflow.add_node(
    "prepare_review",
    prepare_review
)

workflow.add_node(
    "patient_review",
    patient_review
)


workflow.add_edge(
    START,
    "extract_data"
)


workflow.add_conditional_edges(
    "extract_data",
    router_case_taking_complete,
    {
        "END": "prepare_review",
        "next_question": "next_question",
    }
)


# a finished interview: the patient checks the case, then it is final
workflow.add_edge(
    "prepare_review",
    "patient_review"
)


workflow.add_edge(
    "patient_review",
    "finish_interview"
)


workflow.add_conditional_edges(
    "next_question",
    router_next_question,
    {
        "extract_data": "extract_data",
        "ask_next_question": "ask_next_question",
    }
)


workflow.add_conditional_edges(
    "ask_next_question",
    router_after_ask,
    {
        "END": "finish_interview",
        "retry": "ask_next_question",
        "abort": "abort_interview",
        "no_answer": "no_answer",
        "answered": "next_question",
    }
)


workflow.add_edge(
    "abort_interview",
    "finish_interview"
)


workflow.add_edge(
    "finish_interview",
    END
)


workflow.add_edge(
    "no_answer",
    "next_question"
)


checkpointer = InMemorySaver()


chain = workflow.compile(
    checkpointer=checkpointer
)


async def handle_jev_result(data):
    # advisory only: the interview does not wait for JEV
    turn_id = data["turn_id"]

    log_since(turn_id, "Redis delivery (JEV done -> graph)", data.get("jev_done_at"))
    log_since(turn_id, "answer -> JEV result in graph", data.get("answered_at"))

    print(
        f"\n🔎 JEV (advisory) turn {turn_id[:8]}: {data['relevance']} "
        f"(confidence {float(data['confidence']):.2f}) | answer: {data.get('answer', '')!r}"
    )


async def redis_result_consumer():

    # start after the newest existing entry and then track the id explicitly;
    # re-reading with "$" on every call skips results that arrive between reads
    latest = await redis_client.xrevrange("jev_results", count=1)
    last_id = latest[0][0] if latest else "0-0"

    print("Waiting for JEV results from Redis...")

    while True:

        try:
            # must stay below redis-py's socket_timeout (5s by default), or every
            # idle read ends in TimeoutError
            results = await redis_client.xread(
                {
                    "jev_results": last_id
                },
                block=3000
            )
        except Exception as e:
            # a dropped connection must not end the consumer; redis-py reconnects
            # on the next command and last_id makes it pick up what was missed
            print(f"\n⚠️ Redis read failed, retrying: {e!r}")
            await asyncio.sleep(1)
            continue

        for stream_name, messages in results or []:

            for message_id, data in messages:

                last_id = message_id

                try:
                    await handle_jev_result(data)
                except Exception:
                    print(f"\n❌ Failed to process JEV result {message_id}:")
                    traceback.print_exc()


def save_graph_png():
    png_data = chain.get_graph().draw_mermaid_png()

    with open("langgraph.png", "wb") as f:
        f.write(png_data)

    print("Graph saved as langgraph.png")


async def main():

    save_graph_png()

    consumer_task = asyncio.create_task(
        redis_result_consumer()
    )

    try:

        session_id = "abcde"

        result = await chain.ainvoke(
            {
                "messages": [
                    HumanMessage(
                        content="I have had fever for 3 days and a dry cough."
                    )
                ],
                "patient_id": "12345",
                "session_id": session_id,
                "turn_id": str(uuid.uuid4()),
                "patient_text": "",
                "current_question": None,
                "patient_answer": "",
                "extracted_data": {},
                "next_questions": [],
                "no_of_questions_remaining_to_ask": 0,
                "case_taking_complete": False,
                "i": 0,
            },
            config={
                "configurable": {
                    "thread_id": session_id
                }
            }
        )

        # the graph pauses at patient_review; a terminal run has no screen for it, so it goes on unreviewed
        if chain.get_state({"configurable": {"thread_id": session_id}}).next:
            result = await chain.ainvoke(
                Command(resume={"confirmed": False, "sections": None}),
                config={"configurable": {"thread_id": session_id}},
            )

        # the whole interview runs inside this call now: nothing waits for JEV
        print()
        print("================================")
        print("INTERVIEW RESULT")
        print("================================")
        print(result)

    finally:

        consumer_task.cancel()

        try:
            await consumer_task
        except asyncio.CancelledError:
            pass

        await close_producer()

        await close_local_audio()

        await close_spare_session()

        await redis_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())