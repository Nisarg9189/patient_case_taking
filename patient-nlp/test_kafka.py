import asyncio
import json

from kafka_client import create_producer


async def main():

    producer = create_producer()

    await producer.start()

    try:
        message = {
            "session_id": "test-001",
            "question": "How severe is your fever?",
            "answer": "My fever is moderate."
        }

        await producer.send_and_wait(
            "patient-answers",
            json.dumps(message).encode("utf-8")
        )

        print("✅ Message sent successfully")

    finally:
        await producer.stop()


asyncio.run(main())