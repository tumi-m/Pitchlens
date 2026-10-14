"""Smoke test of the real vision stack on the player-load path (no mocks).

Runs the engine with ultralytics, torch and OpenCV as installed (CI's
vision-smoke workflow mirrors vision.Dockerfile) on the review fixture, with
the ball search off and only the player weights (models/yolo11s.pt):

    python backend/scripts/smoke_player_load.py [video]

It passes when the engine got as far as detecting players and then either
wrote a player-load result (ballSearch "none", no ball time, a distance
measurement) or stopped because the model saw nobody on the pitch (the fixture
is short, so that is an honest outcome). Anything else exits non-zero.
"""

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
VIDEO = ROOT.parent / "frontend" / "tests" / "fixtures" / "review.mp4"
DETECTING = "Detecting players and tracking kits"
NOBODY = "No on-pitch players detected"


def fail(message):
    print(f"FAIL: {message}", file=sys.stderr)
    return 1


def main(video=VIDEO):
    from app.vision.engine import run_video

    stages = []

    def progress(**update):
        if update.get("stage"):
            stages.append(update["stage"])

    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "result.json"
        try:
            run_video(str(video), output, progress=progress, sample_fps=3, profile="general", ball_search="none")
        except ValueError as exc:
            if NOBODY not in str(exc):
                return fail(f"run_video stopped with an unexpected error: {exc}")
            if DETECTING not in stages:
                return fail(f"stopped before player detection (stages: {stages})")
            print(f"OK: player detection ran; the model saw no on-pitch players ({exc})")
            return 0
        if DETECTING not in stages:
            return fail(f"never reported '{DETECTING}' (stages: {stages})")
        if not output.is_file():
            return fail("no result was written")
        result = json.loads(output.read_text())
    if result.get("ballSearch") != "none" or result.get("ballInference") != "skipped":
        return fail(f"not a player-load result: ballSearch={result.get('ballSearch')!r}")
    performance = result.get("performance") or {}
    if "ballInferenceSeconds" not in performance or performance["ballInferenceSeconds"] is not None:
        return fail(f"ballInferenceSeconds should be null on a player-load job, got {performance.get('ballInferenceSeconds', 'missing')!r}")
    load = (result.get("metrics") or {}).get("playerLoad")
    if not isinstance(load, dict) or "metres" not in load:
        return fail("metrics.playerLoad is missing")
    print(f"OK: {len(result.get('frames') or [])} frames, distance {load['metres']} m, {performance.get('totalSeconds')} s")
    return 0


if __name__ == "__main__":
    sys.exit(main(*(Path(arg) for arg in sys.argv[1:2])))
