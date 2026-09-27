import asyncio

from kafka_client import create_consumer


async def main():

    consumer = create_consumer(
        "patient-answers",
        "test-consumer"
    )

    await consumer.start()

    try:
        print("Waiting for messages...")

        async for message in consumer:

            print("\nReceived:")
            print(message.value.decode("utf-8"))

    finally:
        await consumer.stop()


asyncio.run(main())