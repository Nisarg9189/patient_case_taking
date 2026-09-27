import asyncio
import os
import requests
import json

import redis.asyncio as redis

from dotenv import load_dotenv
from kafka_client import create_consumer, create_producer
from latency_log import now, log_since

load_dotenv()


async def main():

    consumer = create_consumer(
        "patient-answers",
        "jev-worker"
    )

    await consumer.start()

    producer = create_producer()
    await producer.start()

    redis_client = redis.Redis(
        host=os.getenv("REDIS_HOST"),
        port=int(os.getenv("REDIS_PORT")),
        username=os.getenv("REDIS_USERNAME"),
        password=os.getenv("REDIS_PASSWORD"),
        decode_responses=True,
    )

    try:

        print("Waiting for messages...")

        async for message in consumer:

            data = json.loads(
                message.value.decode("utf-8")
            )

            turn_id = data["turn_id"]
            log_since(turn_id, "Kafka delivery (sent -> JEV worker)", data.get("kafka_sent_at"))

            jev_started_at = now()

            response = requests.post(
                "https://jevmodel.org/v1/systemone",
                headers={
                    "Authorization":
                        f"Bearer {os.environ['JEVMODEL_API_KEY']}",
                    "Content-Type":
                        "application/json",
                },
                json={
                    "model": "jev-latest",
                    "state": {
                        "question_asked": data["question"],
                        "patient_answer": data["answer"],
                    },
                    "questions": {
                        "answer_relevance": {
                            "type": "choice",
                            "instructions":
                                "Determine whether the patient's answer is relevant to the question asked.",
                            "criteria": {
                                "relevant":
                                    "The answer directly answers the question.",
                                "irrelevant":
                                    "The answer does not address the question.",
                            },
                        }
                    },
                },
            )

            jev_response = response.json()

            log_since(turn_id, "JEV API call", jev_started_at)

            print("JEVModel response:", jev_response)

            result = jev_response["answers"]["answer_relevance"]

            # Send result to Redis Stream
            redis_started_at = now()
            message_id = await redis_client.xadd(
                "jev_results",
                {
                    "session_id": data["session_id"],
                    "turn_id": data["turn_id"],
                    "question": data["question"],
                    "answer": data["answer"],
                    "relevance": result["choice"],
                    "confidence": str(result["confidence"]),
                    # timing, for the graph's latency logs
                    "answered_at": str(data.get("answered_at") or ""),
                    "jev_done_at": str(now()),
                },
            )

            log_since(turn_id, "Redis write", redis_started_at)

            print(
                f"📤 JEV result → Redis "
                f"(message_id={message_id})"
            )

    finally:

        await consumer.stop()
        await producer.stop()
        await redis_client.aclose()


asyncio.run(main())