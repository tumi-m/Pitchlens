"""GPU cost per run, the 24-hour spend ledger and VISION_DAILY_GPU_BUDGET_USD."""

import errno
import gzip
import json
import sys
import threading
import time

import pytest

from app.vision import gpucost
from tests.test_vision import (  # noqa: F401  (fixtures)
    RESULT, _disk, _FakeModal, _hosted_gpu, _queued_job, _start, _until, service, worker,
)


def test_prices_follow_modals_gpu_strings_and_unknown_cards_are_priced_high(monkeypatch):
    monkeypatch.delenv("VISION_GPU_PRICES", raising=False)
    assert gpucost.gpu_price("L4") == (0.000222, True)
    assert gpucost.gpu_price("l4") == (0.000222, True)
    assert gpucost.gpu_price("L4:2") == (0.000444, True)
    assert gpucost.gpu_price("H100!") == (0.001097, True)
    assert gpucost.gpu_price("a100-80gb")[0] == 0.000694
    price, known = gpucost.gpu_price("B200")
    assert not known and price == max(gpucost.GPU_PRICES.values())
    monkeypatch.setenv("VISION_GPU_PRICES", "L4=0.72,B200=6.25,bad,X=-1")
    assert gpucost.gpu_price("L4")[0] == pytest.approx(0.0002)
    assert gpucost.gpu_price("B200") == (pytest.approx(6.25 / 3600), True)


def test_the_estimate_reproduces_the_cost_page_for_the_measured_run():
    # COST.md: 989 s on an L4, 1.5 cores and 4 GiB assumed.
    usd, known = gpucost.estimate("L4", 989.2)
    assert known and usd == pytest.approx(989.2 * (0.000222 + 1.5 * 0.0000131 + 4 * 0.00000222), rel=1e-4)
    # The GPU's own CPU and memory use, and the video it downloaded, when it reports them.
    reported, _ = gpucost.estimate("L4", 100, cpu_seconds=50, max_rss_bytes=2 * 2**30, download_bytes=2e9)
    assert reported == pytest.approx(100 * 0.000222 + 25 * 0.0000131 + 2 * 100 * 0.00000222 + 0.1, rel=1e-4)


def _timed_fake(items, delay=0.0):
    fake = _FakeModal(items)
    inner = fake.remote_gen

    def remote_gen(*args):
        for item in inner(*args):
            time.sleep(delay)
            yield item

    fake.analyse.remote_gen = remote_gen
    return fake


TIMINGS = {"containerUptimeSeconds": 4.0, "firstYieldSeconds": 1.0, "importSeconds": 0.9, "downloadSeconds": 0.2,
           "downloadBytes": 90_000_000, "engineSeconds": 0.1, "compressSeconds": 0.01, "cpuSeconds": 0.5,
           "maxRssBytes": 2**30, "gpuName": "NVIDIA L4", "functionSeconds": 0.4}


def test_a_gpu_run_is_recorded_in_the_status_the_ledger_and_ops(worker, monkeypatch):
    server, client = worker
    _hosted_gpu(monkeypatch)
    monkeypatch.setenv("VISION_DAILY_GPU_BUDGET_USD", "5")
    fake = _timed_fake([{"stage": "Detecting players", "progress": 50},
                        {"result_gz": gzip.compress(b'{"frames": []}'), "timings": TIMINGS}], delay=0.2)
    monkeypatch.setitem(sys.modules, "app.vision.modal_app", fake)
    directory, status = _queued_job(server, owner="c" * 32)
    _, thread = _start(server, directory, status)
    thread.join(10)
    saved = _disk(directory)
    assert saved["status"] == "completed" and saved["engine"] == "gpu"
    run = saved["gpuRuns"][-1]
    assert run["gpu"] == "L4" and run["gpuName"] == "NVIDIA L4" and run["outcome"] == "completed"
    assert run["phases"]["downloadBytes"] == 90_000_000 and run["phases"]["engineSeconds"] == 0.1
    assert 0.3 <= run["wallSeconds"] <= run["appExitSeconds"] < 5 and run["usd"] > 0
    # The first item came 0.2 s after the call, inside the GPU's own 5 s before it: no queueing.
    assert run["queueSeconds"] == 0 and run["seconds"] == run["appExitSeconds"]
    # The result is the GPU's own, byte for byte: the record never reaches it.
    assert (directory / "result.json").read_text() == '{"frames": []}'
    ops = client.get("/ops").json()["gpuSpend"]
    assert ops["budgetUsd"] == 5 and ops["last24hUsd"] == pytest.approx(run["usd"]) and not ops["refusing"]
    assert ops["days"][0]["runs"] == 1 and ops["recentRuns"][0]["job"] == status["id"]


def test_failed_and_cancelled_gpu_runs_still_cost_what_they_ran(worker, monkeypatch):
    server, client = worker
    _hosted_gpu(monkeypatch)
    crash = {"error": "KeyError: 'x'", "user": False, "engine": True}
    fake = _timed_fake([{"stage": "Detecting players", "progress": 10}, crash], delay=0.2)
    monkeypatch.setitem(sys.modules, "app.vision.modal_app", fake)
    directory, status = _queued_job(server)
    _, thread = _start(server, directory, status)
    thread.join(10)
    saved = _disk(directory)
    assert saved["status"] == "failed" and saved["gpuRuns"][-1]["outcome"] == "GPUEngineError"
    assert saved["gpuRuns"][-1]["seconds"] >= 0.3  # no timings from the GPU: the whole wait counts
    spent, counted = server.spend_ledger().spent()
    assert len(counted) == 1 and spent == pytest.approx(saved["gpuRuns"][-1]["usd"])
    # Cancelled while the GPU is still starting (it sends nothing): the time it ran is counted.
    fake = _FakeModal([RESULT], gate=True)
    monkeypatch.setitem(sys.modules, "app.vision.modal_app", fake)
    directory, status = _queued_job(server, "7" * 32)
    event, thread = _start(server, directory, status)
    _until(lambda: fake.running)
    time.sleep(0.3)
    event.set()
    thread.join(5)
    fake.gate.set()
    saved = _disk(directory)
    assert saved["status"] == "cancelled" and saved["gpuRuns"][-1]["outcome"] == "InterruptedError"
    assert saved["gpuRuns"][-1]["seconds"] >= 0.3 and saved["gpuRuns"][-1]["usd"] > 0
    spent, counted = server.spend_ledger().spent()
    assert len(counted) == 2 and all(not e.get("open") for e in counted)


def test_a_gpu_refused_before_the_call_costs_nothing_and_leaves_no_open_run(worker, monkeypatch):
    server, client = worker
    _hosted_gpu(monkeypatch)
    fake = _FakeModal([RESULT])

    def refused():
        raise RuntimeError("AuthError: token invalid")
        yield  # pragma: no cover

    fake.app.run = __import__("contextlib").contextmanager(refused)
    monkeypatch.setitem(sys.modules, "app.vision.modal_app", fake)
    directory, status = _queued_job(server)
    server.work(directory, status, threading.Event())
    assert _disk(directory)["refusedBy"] == "gpu"
    spent, counted = server.spend_ledger().spent()
    assert spent == 0 and all(not e.get("open") for e in counted)


def test_the_budget_refuses_new_gpu_starts_keeps_the_upload_and_frees_up_later(worker, monkeypatch):
    server, client = worker
    _hosted_gpu(monkeypatch)
    monkeypatch.setenv("VISION_DAILY_GPU_BUDGET_USD", "1.50")
    ledger = server.spend_ledger()
    old = ledger.open("analysis", "a" * 32, "L4")
    ledger.close(old, {"seconds": 6000, "usd": 1.6, "outcome": "completed"})
    fake = _FakeModal([RESULT])
    monkeypatch.setitem(sys.modules, "app.vision.modal_app", fake)
    owner = "c" * 32
    directory, status = _queued_job(server, owner=owner, countedAt=server.record_start())
    server.work(directory, status, threading.Event())
    saved = _disk(directory)
    assert fake.entered == 0  # no GPU was asked
    assert saved["status"] == "failed" and saved["refusedBy"] == "budget"
    assert saved["stage"].startswith("This server has reached its GPU spending limit for today. Your upload is saved")
    assert (directory / "video").is_file() and server._recent_starts(time.time()) == []  # start given back
    job = client.get(f"/jobs/{status['id']}", headers={"x-pitchlens-owner": owner}).json()
    assert job["retryable"]
    # Retry is refused at once while the budget is spent, with when it frees up.
    refused = client.post(f"/jobs/{status['id']}/retry", headers={"x-pitchlens-owner": owner})
    assert refused.status_code == 429 and refused.json()["code"] == "gpu-budget"
    assert refused.json()["resetsAt"] == pytest.approx(time.time() + 86400, abs=60)
    ops = client.get("/ops").json()["gpuSpend"]
    assert ops["refusing"] and ops["last24hUsd"] == 1.6
    # A day later the spend has left the window and the retry runs.
    entries = json.loads((server.ROOT / gpucost.LEDGER).read_text())
    for e in entries:
        e["startedAt"] -= 86500
        e["endedAt"] -= 86500
    (server.ROOT / gpucost.LEDGER).write_text(json.dumps(entries))
    monkeypatch.setattr(server.pool, "submit", lambda fn, *args: fn(*args))
    assert client.post(f"/jobs/{status['id']}/retry", headers={"x-pitchlens-owner": owner}).status_code == 200
    assert _disk(directory)["status"] == "completed" and fake.entered == 1


def test_a_section_still_runs_on_the_cpu_when_the_budget_is_spent(worker, monkeypatch):
    server, client = worker
    _hosted_gpu(monkeypatch)
    monkeypatch.setenv("VISION_DAILY_GPU_BUDGET_USD", "0.01")
    ledger = server.spend_ledger()
    ledger.close(ledger.open("analysis", "a" * 32, "L4"), {"usd": 0.02, "seconds": 60})
    fake = _FakeModal([RESULT])
    monkeypatch.setitem(sys.modules, "app.vision.modal_app", fake)
    cpu = []
    monkeypatch.setattr(server, "run_video", lambda *a, **k: cpu.append(1))
    directory, status = _queued_job(server, maxSeconds=20)
    server.work(directory, status, threading.Event())
    assert cpu == [1] and fake.entered == 0 and _disk(directory)["engine"] == "cpu"


def test_training_counts_against_the_budget_and_is_refused_when_it_is_spent(worker, monkeypatch, tmp_path):
    from contextlib import nullcontext
    from types import SimpleNamespace

    from app.vision import balltrain

    server, client = worker
    _hosted_gpu(monkeypatch)
    monkeypatch.setenv("VISION_DAILY_GPU_BUDGET_USD", "1")
    monkeypatch.setenv("VISION_MODAL_TRAIN_GPU", "H100")
    base = tmp_path / "base.pt"
    base.write_bytes(b"w")
    monkeypatch.setattr("app.vision.profiles.model_paths", lambda profile="general": (base, base))
    monkeypatch.setitem(sys.modules, "app.vision.modal_train", SimpleNamespace(
        app=SimpleNamespace(run=nullcontext),
        train_ball=SimpleNamespace(remote=lambda *a: time.sleep(0.3) or {"weights": b""})))

    def run_training(root, models, base, remote=None, on_start=None, **kwargs):
        on_start()
        remote(b"", b"", 1)
        return {"kept": False}

    monkeypatch.setattr(balltrain, "run_training", run_training)
    assert client.post("/ball-model/train", json={}).status_code == 200
    _until(lambda: server.training.get("state") == "done")
    spent, counted = server.spend_ledger().spent()
    assert counted[0]["kind"] == "training" and counted[0]["gpu"] == "H100" and counted[0]["seconds"] >= 0.3
    assert server.training["gpuSeconds"] >= 0.3
    # The operator sees what it cost; the site's ball-model page does not.
    assert client.get("/ops").json()["training"]["gpuUsd"] > 0
    site = client.get("/ball-model", headers={"x-pitchlens-owner": "c" * 32}).json()["training"]
    assert site["state"] == "done" and "gpuUsd" not in site and "gpuSeconds" not in site
    server.spend_ledger().close(server.spend_ledger().open("analysis", "b" * 32, "L4"), {"usd": 1.0})
    refused = client.post("/ball-model/train", json={})
    assert refused.status_code == 429 and refused.json()["code"] == "gpu-budget"


def test_a_run_open_when_the_worker_died_still_counts_after_the_restart(worker, monkeypatch):
    server, client = worker
    _hosted_gpu(monkeypatch)
    monkeypatch.setenv("VISION_DAILY_GPU_BUDGET_USD", "1")
    ledger = server.spend_ledger()
    run_id = ledger.open("analysis", "a" * 32, "L4")
    entries = json.loads((server.ROOT / gpucost.LEDGER).read_text())
    # It had been running 4 hours, last heartbeat 3 hours in, when the process was killed.
    entries[0]["startedAt"] -= 4 * 3600
    entries[0]["heartbeatAt"] = entries[0]["startedAt"] + 3 * 3600
    (server.ROOT / gpucost.LEDGER).write_text(json.dumps(entries))
    server.recover_after_restart()
    spent, counted = server.spend_ledger().spent()
    assert counted[0]["id"] == run_id and not counted[0]["open"] and counted[0]["outcome"] == "worker-restarted"
    assert counted[0]["seconds"] == pytest.approx(3 * 3600 + gpucost.HEARTBEAT_SLACK, abs=2)
    assert spent == pytest.approx(gpucost.estimate("L4", 3 * 3600 + 300)[0], rel=1e-3) and spent > 1
    assert client.get("/ops").json()["gpuSpend"]["refusing"]


def test_heartbeats_keep_a_long_run_counted_while_it_runs(worker, monkeypatch):
    server, client = worker
    ledger = server.spend_ledger()
    run_id = ledger.open("analysis", "a" * 32, "L4")
    entries = json.loads((server.ROOT / gpucost.LEDGER).read_text())
    entries[0]["startedAt"] -= 3600
    (server.ROOT / gpucost.LEDGER).write_text(json.dumps(entries))
    ledger.heartbeat(run_id)
    spent, _ = ledger.spent()
    assert spent == pytest.approx(gpucost.estimate("L4", 3600)[0], rel=1e-3)


def test_an_invalid_budget_is_no_limit_and_is_logged(monkeypatch, caplog):
    monkeypatch.setenv("VISION_DAILY_GPU_BUDGET_USD", "five")
    assert gpucost.daily_budget() is None
    assert "not a dollar amount" in caplog.text
    monkeypatch.setenv("VISION_DAILY_GPU_BUDGET_USD", "0")
    assert gpucost.daily_budget() is None
    monkeypatch.setenv("VISION_DAILY_GPU_BUDGET_USD", "7.5")
    assert gpucost.daily_budget() == 7.5


def test_the_owner_never_sees_what_a_gpu_run_cost(worker, monkeypatch):
    server, client = worker
    _hosted_gpu(monkeypatch)
    fake = _timed_fake([{"stage": "Detecting players", "progress": 50},
                        {"result_gz": gzip.compress(b'{"frames": []}'), "timings": TIMINGS}])
    tags = []
    fake.app.app_id = "ap-test"
    fake.app.set_tags = tags.append
    monkeypatch.setitem(sys.modules, "app.vision.modal_app", fake)
    owner = "c" * 32
    directory, status = _queued_job(server, owner=owner)
    server.work(directory, status, threading.Event())
    saved = _disk(directory)
    # Recorded for the operator, tagged for Modal's billing report...
    assert saved["gpuRuns"][-1]["modalAppId"] == "ap-test" and saved["gpuRuns"][-1]["usd"] > 0
    assert tags == [{"pitchlens-job": status["id"], "pitchlens-kind": "analysis"}]
    # ...and never in what the owner can read or download.
    job = client.get(f"/jobs/{status['id']}", headers={"x-pitchlens-owner": owner})
    result = client.get(f"/jobs/{status['id']}/result", headers={"x-pitchlens-owner": owner})
    listing = client.get("/jobs", headers={"x-pitchlens-owner": owner})
    assert job.status_code == result.status_code == listing.status_code == 200
    for body in (job.text, result.text, listing.text):
        assert not any(word in body for word in ("usd", "gpuRuns", "gpuRun", "modalAppId"))


@pytest.mark.parametrize("failing", [("open", "heartbeat", "close"), ("heartbeat", "close")])
def test_a_full_volume_under_the_spend_ledger_never_stops_a_gpu_run(worker, monkeypatch, caplog, failing):
    server, client = worker
    _hosted_gpu(monkeypatch)
    monkeypatch.setenv("VISION_DAILY_GPU_BUDGET_USD", "5")
    monkeypatch.setattr(gpucost, "HEARTBEAT_SECONDS", 0)  # a heartbeat on every poll of the stream
    calls = []

    def full(name):
        def method(self, *args):
            calls.append(name)
            raise OSError(errno.ENOSPC, "No space left on device")
        return method

    for name in failing:
        monkeypatch.setattr(gpucost.Ledger, name, full(name))
    fake = _timed_fake([{"stage": "Detecting players", "progress": 50}, RESULT], delay=0.6)
    monkeypatch.setitem(sys.modules, "app.vision.modal_app", fake)
    directory, status = _queued_job(server)
    server.work(directory, status, threading.Event())
    saved = _disk(directory)
    assert (saved["status"], saved["engine"]) == ("completed", "gpu") and "refusedBy" not in saved
    assert (directory / "result.json").read_text() == '{"frames": []}'
    # Without an entry there is nothing to refresh or close.
    assert set(calls) == ({"open"} if "open" in failing else {"heartbeat", "close"}) and "Could not" in caplog.text
    assert saved["gpuRuns"][-1]["outcome"] == "completed"  # the status still has the record


def test_an_unreadable_ledger_lets_gpu_runs_through_with_a_warning(worker, monkeypatch, caplog):
    server, client = worker
    _hosted_gpu(monkeypatch)
    monkeypatch.setenv("VISION_DAILY_GPU_BUDGET_USD", "0.01")
    # Reading it raises IsADirectoryError (an OSError): fail open rather than refuse every GPU run.
    (server.ROOT / gpucost.LEDGER).mkdir()
    fake = _FakeModal([RESULT])
    monkeypatch.setitem(sys.modules, "app.vision.modal_app", fake)
    directory, status = _queued_job(server)
    server.work(directory, status, threading.Event())
    saved = _disk(directory)
    assert fake.entered == 1 and (saved["status"], saved["engine"]) == ("completed", "gpu")
    assert "Could not read the GPU spend ledger" in caplog.text
    assert client.get("/ops").json()["gpuSpend"]["last24hUsd"] == 0


@pytest.mark.parametrize("failing", [("open", "heartbeat", "close"), ("heartbeat", "close")])
def test_a_full_volume_under_the_spend_ledger_never_stops_training(worker, monkeypatch, tmp_path, caplog, failing):
    from contextlib import nullcontext
    from types import SimpleNamespace

    from app.vision import balltrain

    server, client = worker
    _hosted_gpu(monkeypatch)
    base = tmp_path / "base.pt"
    base.write_bytes(b"w")
    monkeypatch.setattr("app.vision.profiles.model_paths", lambda profile="general": (base, base))
    monkeypatch.setitem(sys.modules, "app.vision.modal_train", SimpleNamespace(
        app=SimpleNamespace(run=nullcontext),
        train_ball=SimpleNamespace(remote=lambda *a: time.sleep(0.3) or {"weights": b""})))
    monkeypatch.setattr(gpucost, "HEARTBEAT_SECONDS", 0.05)
    calls = []

    def full(name):
        def method(self, *args):
            calls.append(name)
            raise OSError(errno.ENOSPC, "No space left on device")
        return method

    for name in failing:
        monkeypatch.setattr(gpucost.Ledger, name, full(name))

    def run_training(root, models, base, remote=None, on_start=None, **kwargs):
        on_start()
        remote(b"", b"", 1)
        return {"kept": False}

    monkeypatch.setattr(balltrain, "run_training", run_training)
    assert client.post("/ball-model/train", json={}).status_code == 200
    _until(lambda: server.training.get("state") in ("done", "failed"))
    assert server.training["state"] == "done" and server.training["gpuSeconds"] >= 0.3
    assert set(calls) == ({"open"} if "open" in failing else {"heartbeat", "close"}) and "Could not" in caplog.text
