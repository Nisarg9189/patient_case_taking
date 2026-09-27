import asyncio
import json

from kafka_client import create_consumer
from workflow import chain


async def main():

    consumer = create_consumer(
        "relevance-results",
        "langgraph-relevance-worker"
    )

    await consumer.start()

    print("Waiting for relevance results...")

    try:

        async for message in consumer:

            data = json.loads(
                message.value.decode("utf-8")
            )

            print("\nReceived relevance result:")
            print(data)

            # Send event into LangGraph
            result = await chain.ainvoke({
                "session_id": data["session_id"],
                "current_question": data["question"],
                "patient_answer": data["answer"],
                "answer_relevance": data["relevance"],
                "relevance_confidence": data["confidence"],
            },
                config={
                    "configurable": {
                        "thread_id": data["session_id"]
                    }
                }
            )

            print("LangGraph result:")
            print(result)

    finally:
        await consumer.stop()


if __name__ == "__main__":
    asyncio.run(main())