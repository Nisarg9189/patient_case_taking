# The interview web app in one image: the React page and the FastAPI backend.
# Built from the project root (see docker-compose.yml): docker build -f deploy/Dockerfile .

# ---- 1. build the React app
FROM node:22-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- 2. the backend
FROM python:3.14-slim
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY patient-nlp/requirements.txt patient-nlp/requirements.txt
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r patient-nlp/requirements.txt -r backend/requirements.txt

# code only: API keys come from the environment at run time (deploy/app.env), never the image
COPY patient-nlp/*.py patient-nlp/
COPY patient-nlp/certs patient-nlp/certs
COPY backend/app.py backend/app.py
COPY --from=frontend /frontend/dist frontend/dist
ENV FRONTEND_DIST=/app/frontend/dist

RUN useradd --create-home --uid 1000 app
USER app
WORKDIR /app/backend
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"

# one process: each interview lives on one WebSocket, and the load test showed one process
# handles 250+ interviews (async clients); Caddy in front terminates HTTPS
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
