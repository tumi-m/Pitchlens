"""Estimated cost of each GPU run, a 24-hour spend ledger and the daily GPU budget.

Prices are Modal's list prices (docs/COST.md), overridable per card with
VISION_GPU_PRICES ("L4=0.80,H100=3.95", dollars per hour). The ledger
(gpu-spend.json in the data folder) survives restarts: a run is written when it
starts and refreshed while it runs, so a worker that dies mid-run still counts
the GPU time it started. Recording is best-effort: callers catch OSError and
ValueError from the ledger, and a ledger that cannot be read counts as empty
(the budget then fails open, with a warning), so it never stops a run.
"""

import json
import logging
import math
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

# USD per second, Modal list prices (docs/COST.md "Prices").
GPU_PRICES = {
    "T4": 0.000164,
    "L4": 0.000222,
    "A10": 0.000306,
    "A10G": 0.000306,
    "L40S": 0.000542,
    "A100": 0.000583,
    "A100-40GB": 0.000583,
    "A100-80GB": 0.000694,
    "H100": 0.001097,
}
CPU_CORE_SECOND = 0.0000131
MEMORY_GIB_SECOND = 0.00000222
# Railway egress for the video the GPU downloads, per GB.
EGRESS_PER_GB = 0.05
# Assumed when the GPU did not report its own use (docs/COST.md).
ASSUMED_CORES = 1.5
ASSUMED_GIB = 4.0
WINDOW = 86400
KEEP = 8 * 86400  # per-day totals for the last week
# A run whose worker died: counted up to its last heartbeat plus this (Modal stops an
# ephemeral app once its client's heartbeats stop).
HEARTBEAT_SLACK = 300
HEARTBEAT_SECONDS = 30

LEDGER = "gpu-spend.json"
_lock = threading.Lock()


def gpu_spec(value):
    """Modal's rule for a GPU string: case-insensitive, optional ":count"."""
    value = (value or "").strip()
    count = 1
    if ":" in value:
        value, raw = value.split(":", 1)
        try:
            count = max(1, int(raw))
        except ValueError:
            count = 1
    return value.upper().rstrip("!"), count


def _overrides():
    prices = {}
    for part in os.getenv("VISION_GPU_PRICES", "").split(","):
        name, _, hourly = part.partition("=")
        try:
            value = float(hourly)
        except ValueError:
            continue
        if name.strip() and math.isfinite(value) and value >= 0:
            prices[name.strip().upper()] = value / 3600
    return prices


def gpu_price(value):
    """(USD per second for this GPU string, whether the price is known).

    An unknown card is priced as the dearest known one, so the budget never under-counts.
    """
    name, count = gpu_spec(value)
    table = {**GPU_PRICES, **_overrides()}
    if name in table:
        return table[name] * count, True
    return max(table.values()) * count, False


def estimate(gpu, seconds, cpu_seconds=None, max_rss_bytes=None, download_bytes=0):
    """Estimated dollars for `seconds` of one container on `gpu`."""
    seconds = max(0.0, float(seconds or 0))
    per_second, known = gpu_price(gpu)
    # Modal bills physical cores (2 vCPUs each); the GPU reports vCPU (process) seconds.
    cores = ASSUMED_CORES * seconds if cpu_seconds is None else max(float(cpu_seconds) / 2, 0.125 * seconds)
    gib = ASSUMED_GIB if max_rss_bytes is None else max(float(max_rss_bytes) / 2**30, 0.125)
    usd = seconds * per_second + cores * CPU_CORE_SECOND + gib * seconds * MEMORY_GIB_SECOND
    usd += (download_bytes or 0) / 1e9 * EGRESS_PER_GB
    return round(usd, 5), known


def daily_budget():
    """VISION_DAILY_GPU_BUDGET_USD (None = no limit). An invalid value is no limit, with a warning."""
    raw = os.getenv("VISION_DAILY_GPU_BUDGET_USD", "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or value < 0:
        logging.warning("VISION_DAILY_GPU_BUDGET_USD=%r is not a dollar amount; no GPU budget applies", raw)
        return None
    return value or None


class Ledger:
    def __init__(self, root):
        self.path = Path(root) / LEDGER

    def _read(self):
        try:
            data = json.loads(self.path.read_text())
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as exc:
            logging.warning("Could not read the GPU spend ledger %s; counting it as empty: %s", self.path, exc)
            return []
        return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []

    def _write(self, entries, now):
        entries = [e for e in entries if _end(e) >= now - KEEP]
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(entries, separators=(",", ":")))
        temporary.replace(self.path)

    def open(self, kind, job, gpu):
        now = time.time()
        entry = {"id": f"{kind}-{job}-{now:.3f}", "kind": kind, "job": job, "gpu": gpu,
                 "startedAt": round(now, 3), "heartbeatAt": round(now, 3), "open": True, "usd": 0.0}
        with _lock:
            entries = self._read()
            entries.append(entry)
            self._write(entries, now)
        return entry["id"]

    def heartbeat(self, run_id):
        now = time.time()
        with _lock:
            entries = self._read()
            for e in entries:
                if e.get("id") == run_id and e.get("open"):
                    e["heartbeatAt"] = round(now, 3)
                    e["usd"] = estimate(e.get("gpu"), now - e["startedAt"])[0]
            self._write(entries, now)

    def close(self, run_id, record):
        now = time.time()
        with _lock:
            entries = self._read()
            for e in entries:
                if e.get("id") == run_id:
                    e.update(record, open=False, endedAt=round(now, 3))
            self._write(entries, now)

    def recover(self):
        """At startup: a run left open by a dead worker ends at its last heartbeat (plus slack)."""
        now = time.time()
        with _lock:
            entries = self._read()
            changed = False
            for e in entries:
                if e.get("open"):
                    end = min(now, float(e.get("heartbeatAt") or e["startedAt"]) + HEARTBEAT_SLACK)
                    seconds = max(0.0, end - e["startedAt"])
                    e.update(open=False, endedAt=round(end, 3), seconds=round(seconds, 1),
                             usd=estimate(e.get("gpu"), seconds)[0], outcome="worker-restarted")
                    changed = True
            if changed:
                self._write(entries, now)

    def spent(self, now=None):
        """(dollars in the last 24 hours, the entries counted), open runs at their latest estimate."""
        now = time.time() if now is None else now
        counted = [e for e in self._read() if _end(e, now) >= now - WINDOW]
        return round(sum(float(e.get("usd") or 0) for e in counted), 5), counted

    def resets_at(self, budget, now=None):
        """When enough spend leaves the 24-hour window to go back under `budget`."""
        total, counted = self.spent(now)
        for e in sorted(counted, key=lambda e: _end(e, now)):
            if total < budget:
                break
            total -= float(e.get("usd") or 0)
            if total < budget:
                return round(_end(e, now) + WINDOW)
        return None

    def days(self, now=None, count=7):
        now = time.time() if now is None else now
        totals = {}
        for e in self._read():
            day = datetime.fromtimestamp(_end(e, now), timezone.utc).date().isoformat()
            t = totals.setdefault(day, {"date": day, "usd": 0.0, "runs": 0, "gpuSeconds": 0.0, "training": 0})
            t["usd"] = round(t["usd"] + float(e.get("usd") or 0), 4)
            t["runs"] += 1
            t["training"] += e.get("kind") == "training"
            t["gpuSeconds"] = round(t["gpuSeconds"] + float(e.get("seconds") or 0), 1)
        return sorted(totals.values(), key=lambda t: t["date"], reverse=True)[:count]

    def recent(self, count=10):
        keys = ("kind", "job", "gpu", "startedAt", "endedAt", "seconds", "usd", "outcome", "open", "priceKnown")
        return [{k: e.get(k) for k in keys if k in e} for e in self._read()[-count:]][::-1]


def _end(entry, now=None):
    if entry.get("open"):
        return float(entry.get("heartbeatAt") or entry.get("startedAt") or 0)
    return float(entry.get("endedAt") or entry.get("startedAt") or 0)
