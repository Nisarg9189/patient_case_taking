"""Summarise a stepped run: patient-side results per step next to the server stats.

    .venv/bin/python summarize.py results/step_stats.csv [server_stats.csv]

The optional second argument (stage 1) is the fake backend's server_stats.csv or its log
(the 📊 lines); server stats are grouped by the number of open interviews at the time.
"""
import csv
import re
import sys
from collections import defaultdict


def patient_results(path):
    rows = defaultdict(dict)
    with open(path) as handle:
        for row in csv.DictReader(handle):
            match = re.match(r"(.+) \[(\d+) users\]$", row["Name"])
            if not match:
                continue
            name, users = match.group(1), int(match.group(2))
            rows[users][name] = row
    return rows


def server_stats(path):
    if path.endswith(".csv"):
        with open(path) as handle:
            samples = [{k: float(v) for k, v in row.items() if k != "time"} for row in csv.DictReader(handle)]
    else:
        samples = []
        with open(path, errors="replace") as handle:
            for line in handle:
                if "📊" in line:
                    fields = dict(re.findall(r"(\w+)=([\d.]+)", line.split("📊", 1)[1]))
                    samples.append({k: float(v) for k, v in fields.items()})
    return samples


def main(stats_path, server_path=None):
    patients = patient_results(stats_path)
    samples = server_stats(server_path) if server_path else []

    print(f"{'users':>5} | {'wait p50':>8} {'p95':>6} {'max':>6} {'n':>4} | {'interviews ok/fail':>18} | "
          f"{'queued max':>10} {'lag p99 max':>11} {'cpu% avg':>8} {'rss MB':>6}")
    for users in sorted(patients):
        rows = patients[users]
        wait = rows.get("patient wait", {})
        whole = rows.get("whole interview", {})
        # server samples taken while about this many interviews were open
        near = [s for s in samples if abs(s["interviews"] - users) <= max(1, users * 0.1)]
        ok = int(whole.get("Request Count", 0) or 0) - int(whole.get("Failure Count", 0) or 0)
        print(
            f"{users:>5} | "
            f"{float(wait.get('50%', 0)) / 1000:>7.1f}s {float(wait.get('95%', 0)) / 1000:>5.1f}s "
            f"{float(wait.get('Max Response Time', 0)) / 1000:>5.1f}s {int(wait.get('Request Count', 0) or 0):>4} | "
            f"{ok:>8}/{int(whole.get('Failure Count', 0) or 0):<9} | "
            + (f"{max(s['queued'] for s in near):>10.0f} {max(s['loop_lag_p99_ms'] for s in near):>9.0f}ms "
               f"{sum(s['cpu_percent'] for s in near) / len(near):>8.0f} {max(s['max_rss_mb'] for s in near):>6.0f}"
               if near else "  (no server samples at this level)")
        )
    others = {name for rows in patients.values() for name in rows} - {"patient wait", "whole interview"}
    for name in sorted(others):
        counts = {u: int(patients[u][name]["Request Count"]) for u in sorted(patients) if name in patients[u]}
        print(f"  {name}: " + ", ".join(f"{c} at {u} users" for u, c in counts.items()))


if __name__ == "__main__":
    main(*sys.argv[1:3])  # stats file, optional server stats
