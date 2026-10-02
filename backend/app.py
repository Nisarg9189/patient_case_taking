"""Web backend for the clinic app.

Run from this folder, with the patient-nlp virtualenv:

    ../patient-nlp/.venv/bin/uvicorn app:app --port 8000

The patient interview is held by the Azure AI Foundry voice agent at /ws/voice-agent (see
voice_agent.py for the messages between the browser and this server). The agent's tools are the
MCP server in patient-nlp/mcp_server.py, served by this app at /mcp-server/mcp when MCP_SECRET
is set. The rest is the clinic: cases, appointments, scheduling, prescriptions (the routers).
"""
import asyncio
import contextlib
import os
import sys
from pathlib import Path

PATIENT_NLP = Path(__file__).resolve().parent.parent / "patient-nlp"
sys.path.insert(0, str(PATIENT_NLP))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PATIENT_NLP / ".env")

from fastapi import FastAPI  # noqa: E402

import db  # noqa: E402
from api import router as api_router  # noqa: E402
from booking_api import router as booking_router  # noqa: E402
from prescriptions import router as prescriptions_router  # noqa: E402
import voice_agent  # noqa: E402

# The MCP server the Foundry voice agent calls (patient-nlp/mcp_server.py) runs inside this app,
# which is awake whenever a patient is talking. It is served only when MCP_SECRET is set.
mcp_app = None
if os.getenv("MCP_SECRET"):
    from mcp_server import mcp  # noqa: E402

    mcp_app = mcp.http_app(path="/mcp")


async def _summary_worker():
    """summary_agent.py's Kafka consumer, in this process (RUN_SUMMARY_WORKER=1): for hosting
    that scales to zero, where a separate worker would have nothing to wake it up. It runs
    while the app runs, so a finished interview is summarised within seconds; cases sent
    while the app was stopped are read when it starts (the consumer group remembers)."""
    import summary_agent
    while True:
        try:
            await summary_agent.summarize_patient_case()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"\n⚠️ Summary worker stopped: {e!r}; restarting in 30 s")
            await asyncio.sleep(30)


@contextlib.asynccontextmanager
async def lifespan(app):
    worker = asyncio.create_task(_summary_worker()) if os.getenv("RUN_SUMMARY_WORKER") == "1" else None
    async with mcp_app.lifespan(app) if mcp_app else contextlib.nullcontext():
        yield
    if worker:
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await worker
    await voice_agent.close()
    await db.close()


app = FastAPI(lifespan=lifespan)
app.include_router(api_router)
app.include_router(booking_router)
app.include_router(prescriptions_router)
app.include_router(voice_agent.router)
if mcp_app:
    app.mount("/mcp-server", mcp_app)  # the MCP endpoint is /mcp-server/mcp


@app.get("/api/health")
async def health():
    return {"ok": True, "azure_voice_agent": voice_agent.configured()}


def serve_frontend():
    """Serve the built React app (frontend/dist) from this server, when FRONTEND_DIST is set.

    Used in the Docker deployment, so the page and /ws/voice-agent share one origin. Mounted
    last: routes added before it (/api/..., /ws/...) take priority over the files.
    """
    dist = os.getenv("FRONTEND_DIST")
    if dist and Path(dist).is_dir():
        from fastapi.staticfiles import StaticFiles
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")


serve_frontend()
