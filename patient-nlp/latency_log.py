"""Per-turn latency logs. Every line starts with ⏱, so a run can be filtered with `grep ⏱`.

Lines are tagged with the first 8 characters of the answer's turn_id, so the steps from
workflow.py and jev_wroker.py that belong to the same answer can be matched up.
Timestamps are time.time() (wall clock), comparable across processes on one machine.
"""
import time


def now():
    return time.time()


def log_step(turn_id, step, seconds):
    tag = f"[{turn_id[:8]}]" if turn_id else "[--------]"
    print(f"⏱ {tag} {step}: {seconds:.2f}s", flush=True)


def log_since(turn_id, step, started_at):
    """Log the time since `started_at` (a now() timestamp); does nothing if it is missing."""
    if started_at:
        log_step(turn_id, step, now() - float(started_at))
