"""The real backend (backend/app.py) with fake providers, plus a server-side monitor.

Run from this folder with the patient-nlp virtualenv (port 8001, so the real backend on
8000 can keep running):

    ../patient-nlp/.venv/bin/uvicorn fake_backend:app --port 8001 --log-level warning

Every 5 s a line of server stats is printed and appended to server_stats.csv:
active interviews, event-loop lag, worker threads busy/queued, CPU and memory.
The same numbers are at GET /api/loadtest/stats.
"""
import asyncio
import contextlib
import csv
import os
import resource
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "backend"))
sys.path.insert(0, str(HERE))

import app as backend  # noqa: E402  (loads patient-nlp and its .env)
import audio_io  # noqa: E402
import fake_providers  # noqa: E402

fake_providers.install(backend)

app = backend.app

STATS_INTERVAL_SECONDS = 5
LAG_PROBE_SECONDS = 0.05
STATS_CSV = HERE / "server_stats.csv"

_lags = []
_latest = {}


def _executor_stats():
    executor = getattr(asyncio.get_running_loop(), "_default_executor", None)
    if executor is None:
        return {"threads": 0, "max_threads": min(32, (os.cpu_count() or 1) + 4), "queued": 0}
    return {"threads": len(executor._threads), "max_threads": executor._max_workers,
            "queued": executor._work_queue.qsize()}


async def _probe_loop_lag():
    """How late a 50 ms sleep wakes up: time the event loop was busy with other work."""
    while True:
        started = time.perf_counter()
        await asyncio.sleep(LAG_PROBE_SECONDS)
        _lags.append((time.perf_counter() - started - LAG_PROBE_SECONDS) * 1000)


async def _report():
    new_file = not STATS_CSV.exists()
    last_cpu, last_at = time.process_time(), time.perf_counter()
    with STATS_CSV.open("a", newline="") as handle:
        writer = None
        while True:
            await asyncio.sleep(STATS_INTERVAL_SECONDS)
            lags, _lags[:] = sorted(_lags), []
            cpu, at = time.process_time(), time.perf_counter()
            stats = {
                "time": time.strftime("%H:%M:%S"),
                "interviews": len(audio_io._browser_audio),  # open interview WebSockets
                "loop_lag_p50_ms": round(statistics.median(lags), 1) if lags else 0,
                "loop_lag_p99_ms": round(lags[int(len(lags) * 0.99) - 1], 1) if lags else 0,
                "loop_lag_max_ms": round(lags[-1], 1) if lags else 0,
                **_executor_stats(),
                "cpu_percent": round(100 * (cpu - last_cpu) / (at - last_at), 1),
                # macOS reports bytes, Linux kilobytes
                "max_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                                    / (1 << 20 if sys.platform == "darwin" else 1 << 10)),
            }
            last_cpu, last_at = cpu, at
            _latest.clear()
            _latest.update(stats)
            if writer is None:
                writer = csv.DictWriter(handle, fieldnames=list(stats))
                if new_file:
                    writer.writeheader()
            writer.writerow(stats)
            handle.flush()
            print("📊 " + "  ".join(f"{key}={value}" for key, value in stats.items()), flush=True)


_original_lifespan = app.router.lifespan_context


@contextlib.asynccontextmanager
async def lifespan(app_):
    tasks = [asyncio.create_task(_probe_loop_lag()), asyncio.create_task(_report())]
    async with _original_lifespan(app_):
        yield
    for task in tasks:
        task.cancel()


app.router.lifespan_context = lifespan


@app.get("/api/loadtest/stats")
async def loadtest_stats():
    return _latest
