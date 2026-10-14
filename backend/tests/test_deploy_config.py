"""Deployment config: the Railway drain window and watch patterns, and the worker image's layers."""

import re
import tomllib
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def _railway():
    return tomllib.loads((BACKEND / "railway.toml").read_text())


def _instructions():
    """vision.Dockerfile as one string per instruction (continuations joined, comments dropped)."""
    out, current = [], ""
    for raw in (BACKEND / "vision.Dockerfile").read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        current = f"{current} {line.rstrip(chr(92)).strip()}".strip()
        if not line.endswith("\\"):
            out.append(current)
            current = ""
    return out


def _graceful_shutdown_seconds():
    cmd = next(i for i in _instructions() if i.startswith("CMD"))
    return int(re.search(r"--timeout-graceful-shutdown (\d+)", cmd).group(1))


def test_a_redeploy_gives_running_analyses_time_to_record_the_shutdown():
    deploy = _railway()["deploy"]
    drain = deploy.get("drainingSeconds")
    # uvicorn's graceful timeout, then stop_jobs waits up to 10 s, plus a margin.
    assert isinstance(drain, int) and not isinstance(drain, bool)
    assert drain >= _graceful_shutdown_seconds() + 11
    assert deploy["numReplicas"] == 1


def test_only_worker_changes_redeploy_the_worker():
    # Matched from the repository root, even with Root Directory "backend".
    assert _railway()["build"]["watchPatterns"] == ["/backend/**"]


def test_weights_are_downloaded_before_the_app_code_is_copied():
    steps = _instructions()
    copy_app = next(n for n, s in enumerate(steps) if s.startswith("COPY app/"))
    setup_vision = next(n for n, s in enumerate(steps) if s.startswith("RUN") and "scripts/setup_vision.py" in s)
    football = [n for n, s in enumerate(steps) if s.startswith("RUN") and "scripts/setup_football.py" in s]
    assert setup_vision < copy_app and football[0] < copy_app
    # Only the two standard-library setup scripts are in the image before them.
    copies = [s.split()[1:] for s in steps[:setup_vision] if s.startswith("COPY")]
    assert copies[-1] == ["scripts/setup_vision.py", "scripts/setup_football.py", "./scripts/"]
    assert all(c[0] in ("requirements-vision.txt", "scripts/setup_vision.py") for c in copies)
    # A cached layer that lacks the optional broadcast weights is retried after a code change.
    retry = [steps[n] for n in football if n > copy_app]
    assert len(retry) == 1
    assert "! -f models/roboflow-football-player.pt" in retry[0]
    assert "! -f models/roboflow-football-ball.pt" in retry[0]
    assert '"$INSTALL_BROADCAST" = "1"' in retry[0] and "|| echo" in retry[0]
    cmd = next(s for s in steps if s.startswith("CMD"))
    assert "exec uvicorn app.vision.server:app" in cmd
    assert "--workers 1 --timeout-graceful-shutdown 20" in cmd


def test_ci_builds_the_image_and_probes_the_worker_when_the_image_changes():
    yaml = __import__("pytest").importorskip("yaml")
    workflow = yaml.safe_load((BACKEND.parent / ".github" / "workflows" / "vision-image.yml").read_text())
    triggers = workflow[True]  # YAML 1.1 reads the key "on" as true
    paths = ["backend/vision.Dockerfile", "backend/requirements-vision.txt", "backend/scripts/setup_*.py",
             "backend/.dockerignore", ".github/workflows/vision-image.yml"]
    assert triggers["pull_request"]["paths"] == paths and triggers["push"] == {"branches": ["main"], "paths": paths}
    job = workflow["jobs"]["vision-image"]
    # A Hugging Face outage during the build must not turn pull requests red.
    assert job["continue-on-error"] is True
    runs = [step.get("run", step.get("uses", "")) for step in job["steps"]]
    order = ["actions/checkout", "docker build -f backend/vision.Dockerfile --build-arg INSTALL_BROADCAST=0",
             "docker run -d --name vision", "127.0.0.1:8100/healthz", "127.0.0.1:8100/health >", "docker logs vision"]
    found = [next(n for n, run in enumerate(runs) if text in run) for text in order]
    assert found == sorted(found) and job["steps"][-1]["if"] == "failure()"
    assert '"available") is True' in runs[found[4]]
