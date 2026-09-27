import os
import ssl
from dotenv import load_dotenv
from aiokafka import AIOKafkaProducer, AIOKafkaConsumer

load_dotenv()

KAFKA_HOST = os.getenv("KAFKA_HOST")
KAFKA_PORT = os.getenv("KAFKA_PORT")
KAFKA_USER = os.getenv("KAFKA_USER")
KAFKA_PASSWORD = os.getenv("KAFKA_PASSWORD")
KAFKA_CA_CERT = os.getenv("KAFKA_CA_CERT")

# a relative path in .env is relative to this folder, so the backend can run from anywhere
if KAFKA_CA_CERT and not os.path.isabs(KAFKA_CA_CERT):
    KAFKA_CA_CERT = os.path.join(os.path.dirname(os.path.abspath(__file__)), KAFKA_CA_CERT)

BOOTSTRAP_SERVERS = f"{KAFKA_HOST}:{KAFKA_PORT}"

def create_ssl_context():

    context = ssl.create_default_context(
        cafile=KAFKA_CA_CERT
    )

    return context


def create_producer():
    return AIOKafkaProducer(
        bootstrap_servers=BOOTSTRAP_SERVERS,
        security_protocol="SASL_SSL",
        sasl_mechanism="PLAIN",
        sasl_plain_username=KAFKA_USER,
        sasl_plain_password=KAFKA_PASSWORD,
        ssl_context=create_ssl_context(),
    )


def create_consumer(topic, group_id):
    return AIOKafkaConsumer(
        topic,
        bootstrap_servers=BOOTSTRAP_SERVERS,
        security_protocol="SASL_SSL",
        sasl_mechanism="PLAIN",
        sasl_plain_username=KAFKA_USER,
        sasl_plain_password=KAFKA_PASSWORD,
        ssl_context=create_ssl_context(),
        group_id=group_id,
        auto_offset_reset="earliest",
    )