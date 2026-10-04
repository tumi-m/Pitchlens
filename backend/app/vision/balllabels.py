"""Ball labels from the person who knows the match: ground truth, accuracy, training data.

The ball is the weakest link of automatic football analysis on amateur footage:
a few pixels across, it is easily confused with boots, socks and lights, and
public models were not trained on these venues. The scarce resource is not
compute but labelled examples from the venue itself, so the product collects
them: the reviewer is shown analysed frames spread over the match with the
engine's guess, and confirms it, clicks the ball, or marks it not visible.

Those labels give, for the first time, a measured ball accuracy for the match
(recall on visible balls, false detections where there is none), and they are
exported as a training set for fine-tuning the ball detector (see
backend/scripts/train_ball.py).
"""

import json
import math
import random

import cv2
import numpy as np

SCHEMA = 1
MAX_FRAMES = 400


def ball_tolerance(width, height):
    """Pixels within which a detection counts as the labelled ball (3 px at 360p,
    scaled by the short side so portrait video is not judged more loosely)."""
    return 3.0 * max(1.0, min(width or height, height or width) / 360.0)


def sample_frames(result, count=150, seed=0):
    """Analysed frames to label, spread evenly over the match (stratified random).

    Stratifying keeps every part of the match represented (night/day, near/far
    side, open play and stoppages) instead of whatever happens to be picked.
    Deterministic for a given seed, so a reviewer who comes back sees the same set.
    """
    frames = result.get("frames") or []
    n = len(frames)
    if not n:
        return []
    count = max(1, min(count, n, MAX_FRAMES))
    rng = random.Random(seed)
    edges = np.linspace(0, n, count + 1)
    picks = []
    for a, b in zip(edges[:-1], edges[1:]):
        lo, hi = int(math.floor(a)), max(int(math.floor(a)) + 1, int(math.floor(b)))
        picks.append(rng.randrange(lo, min(hi, n)))
    return sorted(set(picks))


def guess(frame):
    """The engine's observed ball in a sampled frame (inferred positions are not guesses)."""
    ball = frame.get("ball")
    if not ball or ball.get("inferred"):
        return None
    return {"x": round(float(ball["x"]), 1), "y": round(float(ball["y"]), 1), "confidence": ball.get("confidence")}


def source_frame(result, index):
    """Source video frame number of a sampled frame (older results: from its time)."""
    frame = result["frames"][index]
    number = frame.get("frame")
    if isinstance(number, int) and not isinstance(number, bool) and number >= 0:
        return number
    fps = float((result.get("video") or {}).get("fps") or 25)
    return int(round(frame["t"] * fps))


def read_frame(video_path, number):
    """Decode one source frame (BGR) by its number, or None. Slow for late frames;
    the labelling flow uses extract_frames, which reads the video in one pass."""
    out = extract_frames(video_path, {number: None}, start=0)
    return out.get(number)


def extract_frames(video_path, wanted, start=None, write=None):
    """Decode the wanted source frames in one sequential pass, numbered exactly as
    the engine numbers them: seek once to the analysis start, then count decoded
    frames. (Seeking to each frame number lands one frame late on trimmed,
    variable-frame-rate phone video.)

    wanted: {source frame number: key}. With `write(key, image)`, frames are
    handed over as they are decoded and not kept; otherwise {number: image}.
    """
    if not wanted:
        return {}
    cap = cv2.VideoCapture(str(video_path))
    out = {}
    try:
        n = 0
        first = min(wanted)
        if start and 0 < start <= first:
            cap.set(cv2.CAP_PROP_POS_FRAMES, start)
            n = start
        last = max(wanted)
        while n <= last:
            if n in wanted:
                ok, image = cap.read()
                if not ok:
                    break
                if write:
                    write(wanted[n], image)
                else:
                    out[n] = image
            elif not cap.grab():
                break
            n += 1
    finally:
        cap.release()
    return out


def analysis_start(result):
    """Source frame the engine seeked to before counting (its first sampled frame)."""
    frames = result.get("frames") or []
    number = frames[0].get("frame") if frames else None
    return number if isinstance(number, int) and not isinstance(number, bool) and number > 0 else 0


def frame_cache(directory):
    return directory / "labelframes"


def prepare_frames(directory, result, indices, video_path):
    """Write the label frames as JPEGs in one pass (skips those already written)."""
    cache = frame_cache(directory)
    cache.mkdir(exist_ok=True)
    wanted = {}
    for i in indices:
        if not (cache / f"{i}.jpg").is_file():
            wanted.setdefault(source_frame(result, i), i)

    def write(i, image):
        temporary = cache / f"{i}.tmp.jpg"
        cv2.imwrite(str(temporary), image, [cv2.IMWRITE_JPEG_QUALITY, 92])
        temporary.replace(cache / f"{i}.jpg")

    extract_frames(video_path, wanted, start=analysis_start(result), write=write)
    return cache


def clean_label(body, size):
    """Validate one label: {"index", "x", "y"} for a visible ball, or {"index", "visible": false}."""
    if not isinstance(body, dict):
        raise ValueError("A label must be an object")
    index = body.get("index")
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise ValueError("Unknown frame")
    if body.get("visible") is False:
        return {"index": index, "visible": False}
    w, h = size
    x, y = body.get("x"), body.get("y")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and -1 <= v <= 100000 and math.isfinite(v) for v in (x, y)):
        raise ValueError("The ball position must be numbers")
    if not (0 <= x <= w and 0 <= y <= h):
        raise ValueError("The ball position is outside the video")
    return {"index": index, "visible": True, "x": round(float(x), 1), "y": round(float(y), 1)}


def apply_label(store, label, now):
    """Upsert by frame (the latest answer wins); every answer stays in the history."""
    store.setdefault("schemaVersion", SCHEMA)
    store.setdefault("labels", {})
    store.setdefault("history", [])
    key = str(label["index"])
    store["labels"][key] = {k: v for k, v in label.items() if k != "index"}
    store["history"].append({**label, "at": now})
    if len(store["history"]) > 20000:
        raise ValueError("Too many label changes for one match")
    return store


def _wilson(successes, n, z=1.96):
    if n <= 0:
        return None
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return [round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3)]


def _rate(k, n):
    return {"value": round(k / n, 3) if n else None, "n": n, "interval95": _wilson(k, n)}


def metrics(result, store):
    """Ball accuracy of this analysis against the reviewer's labels.

    recall: of frames where the reviewer saw the ball, how often the engine's
      observed position was within tolerance;
    falseDetections: of frames where the reviewer saw no ball, how often the
      engine still reported one;
    precision: of the engine's observed positions on labelled frames, how many
      were the ball. Inferred (gap-filled) positions are scored separately.
    """
    frames = result.get("frames") or []
    video = result.get("video") or {}
    tol = ball_tolerance(float(video.get("width") or 640), float(video.get("height") or 360))
    hit = visible = absent = false_detections = detections = correct = 0
    inferred_hit = inferred_n = 0
    errors = []
    for key, label in (store or {}).get("labels", {}).items():
        i = int(key)
        if i >= len(frames):
            continue
        ball = frames[i].get("ball")
        observed = ball if ball and not ball.get("inferred") else None
        if label.get("visible"):
            visible += 1
            if ball and ball.get("inferred"):
                inferred_n += 1
                inferred_hit += math.dist((ball["x"], ball["y"]), (label["x"], label["y"])) <= tol
            if observed:
                detections += 1
                distance = math.dist((observed["x"], observed["y"]), (label["x"], label["y"]))
                if distance <= tol:
                    hit += 1
                    correct += 1
                    errors.append(distance)
        else:
            absent += 1
            if observed:
                detections += 1
                false_detections += 1
    return {
        "labelled": visible + absent,
        "visible": visible,
        "tolerancePixels": round(tol, 1),
        "recall": _rate(hit, visible),
        "precision": _rate(correct, detections),
        "falseDetections": _rate(false_detections, absent),
        "inferredAccuracy": _rate(inferred_hit, inferred_n),
        # Over correct detections only: how precisely a found ball is placed.
        "medianHitErrorPixels": round(float(np.median(errors)), 2) if errors else None,
    }


def load(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {"schemaVersion": SCHEMA, "labels": {}, "history": []}
