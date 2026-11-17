# The interview web app in one image: the React page and the FastAPI backend.
# Built from the project root (see docker-compose.yml): docker build -f deploy/Dockerfile .

# ---- 1. build the React app
FROM node:22-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# the Neon Auth URL is built into the page: pass --build-arg VITE_NEON_AUTH_URL=... (the CI
# image build does), or keep frontend/.env.local in the build context (the EC2 deploy does)
ARG VITE_NEON_AUTH_URL
RUN if [ -z "$VITE_NEON_AUTH_URL" ]; then unset VITE_NEON_AUTH_URL; fi && npm run build

# ---- 2. the backend
FROM python:3.14-slim
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Redis (the interview state) and Kafka (the queue to the summary worker) run inside this
# container, so there is no hosted trial that can expire; see deploy/start.sh. Kafka needs Java.
ARG KAFKA_VERSION=3.9.1
RUN apt-get update \
    && apt-get install -y --no-install-recommends redis-server default-jre-headless curl ca-certificates \
    && curl -fsSL "https://archive.apache.org/dist/kafka/${KAFKA_VERSION}/kafka_2.13-${KAFKA_VERSION}.tgz" \
       | tar -xz -C /opt \
    && mv /opt/kafka_2.13-${KAFKA_VERSION} /opt/kafka \
    && apt-get purge -y curl && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY patient-nlp/requirements.txt patient-nlp/requirements.txt
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r patient-nlp/requirements.txt -r backend/requirements.txt

# code only: API keys come from the environment at run time (deploy/app.env), never the image
COPY patient-nlp/*.py patient-nlp/
COPY patient-nlp/certs patient-nlp/certs
COPY backend/*.py backend/
COPY deploy/kafka.properties /app/kafka.properties
COPY --chmod=755 deploy/start.sh /app/start.sh
COPY --from=frontend /frontend/dist frontend/dist
ENV FRONTEND_DIST=/app/frontend/dist

RUN useradd --create-home --uid 1000 app
USER app
WORKDIR /app/backend
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"

# start.sh starts Redis and Kafka, then runs one uvicorn process: each interview lives on one
# WebSocket, and the load test showed one process handles 250+ interviews (async clients);
# Caddy in front terminates HTTPS. EMBEDDED_SERVICES=0 uses the REDIS_* / KAFKA_* settings instead.
CMD ["/app/start.sh"]
