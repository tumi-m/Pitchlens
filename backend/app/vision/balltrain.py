"""Fine-tune the ball detector on the reviewer's own labels, and only keep it if it is better.

1. build_dataset: every labelled frame of every match is cut into the same
   overlapping tiles the detector sees at inference (ball.tile_frame), with a
   YOLO box around each labelled ball. Tiles without the ball are negatives:
   they teach the model what boots, socks, lights and line crossings look like.
   Matches are split into training and validation by match, never by
   neighbouring frames (with a single match, by time: the last 30%).
2. train: Ultralytics fine-tuning from the current ball weights.
3. score_detector: frame-level ball recall/precision on the validation labels,
   for the current and the new weights. The new weights are kept only when they
   find more balls without more false ones (see `better`).
"""

import json
import math
import random
import shutil
from pathlib import Path

import cv2
import numpy as np

from app.vision import balllabels
from app.vision.ball import tile_frame
from app.vision.faint import ball_size_prior


def labelled_jobs(root):
    """Match folders with ball labels, a finished result and the footage."""
    out = []
    for directory in sorted(Path(root).iterdir()):
        if not (directory / "balllabels.json").is_file() or not (directory / "result.json").is_file():
            continue
        if not (directory / "video").is_file():
            continue
        if balllabels.load(directory / "balllabels.json").get("labels"):
            out.append(directory)
    return out


def _frames_of(directory):
    result = json.loads((directory / "result.json").read_text())
    labels = balllabels.load(directory / "balllabels.json").get("labels", {})
    items = []
    for key, label in labels.items():
        i = int(key)
        if i < len(result.get("frames") or []):
            items.append((i, label))
    items.sort()
    return result, items


def split(jobs, val_fraction=0.3):
    """[(directory, indices or None)] for training and validation: by match when possible."""
    if len(jobs) >= 2:
        k = max(1, round(len(jobs) * val_fraction))
        return [(d, None) for d in jobs[:-k]], [(d, None) for d in jobs[-k:]], "by-match"
    if not jobs:
        return [], [], "none"
    _, items = _frames_of(jobs[0])
    cut = int(len(items) * (1 - val_fraction))
    indices = [i for i, _ in items]
    return [(jobs[0], set(indices[:cut]))], [(jobs[0], set(indices[cut:]))], "by-time-within-one-match"


def _box(label, scale, x0, y0, diameter, tile_w, tile_h):
    cx, cy = label["x"] * scale - x0, label["y"] * scale - y0
    if not (0 <= cx < tile_w and 0 <= cy < tile_h):
        return None
    d = max(4.0, diameter * scale)
    return (cx / tile_w, cy / tile_h, min(1.0, d / tile_w), min(1.0, d / tile_h))


def build_dataset(train, val, out, negatives_per_positive=1.0, seed=0):
    """Write a YOLO dataset to `out`; returns counts. Validation frames are also kept whole."""
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    rng = random.Random(seed)
    counts = {"train": {"positive": 0, "negative": 0}, "val": {"positive": 0, "negative": 0}, "valFrames": 0}
    whole = []
    for part, members in (("train", train), ("val", val)):
        (out / "images" / part).mkdir(parents=True)
        (out / "labels" / part).mkdir(parents=True)
        for directory, indices in members:
            result, items = _frames_of(directory)
            height = float((result.get("video") or {}).get("height") or 360)
            for i, label in items:
                if indices is not None and i not in indices:
                    continue
                frame = balllabels.read_frame(directory / "video", balllabels.source_frame(result, i))
                if frame is None:
                    continue
                diameter = ball_size_prior(result["frames"][i].get("players") or [], height)
                stem = f"{directory.name[:8]}-{i}"
                if part == "val":
                    path = out / "val_frames" / f"{stem}.jpg"
                    path.parent.mkdir(exist_ok=True)
                    cv2.imwrite(str(path), frame)
                    whole.append({"image": path.name, "label": label, "height": height})
                for t, (x0, y0, scale, crop) in enumerate(tile_frame(frame)):
                    th, tw = crop.shape[:2]
                    box = _box(label, scale, x0, y0, diameter, tw, th) if label.get("visible") else None
                    if box is None and rng.random() > negatives_per_positive * 0.5:
                        continue  # keep a share of the empty tiles
                    name = f"{stem}-{t}"
                    cv2.imwrite(str(out / "images" / part / f"{name}.jpg"), crop)
                    (out / "labels" / part / f"{name}.txt").write_text("" if box is None else "0 %.6f %.6f %.6f %.6f\n" % box)
                    counts[part]["positive" if box else "negative"] += 1
    (out / "val_frames.json").write_text(json.dumps(whole))
    counts["valFrames"] = len(whole)
    (out / "data.yaml").write_text(
        f"path: {out.resolve()}\ntrain: images/train\nval: images/val\nnames:\n  0: ball\n"
    )
    return counts


def score_detector(detector, dataset):
    """Frame-level ball recall/precision of a detector on the dataset's whole validation frames.

    The top-scoring detection of each frame counts (the tracker sees more, so
    this is a conservative proxy for what the engine will retain).
    """
    dataset = Path(dataset)
    frames = json.loads((dataset / "val_frames.json").read_text())
    hit = visible = absent = false = detections = 0
    for item in frames:
        image = cv2.imread(str(dataset / "val_frames" / item["image"]))
        found = detector.detect_batch([image], threshold=0.15)[0]
        best = max(found, key=lambda b: b["confidence"]) if found else None
        tol = balllabels.ball_tolerance(item["height"])
        label = item["label"]
        if best:
            detections += 1
        if label.get("visible"):
            visible += 1
            if best and math.dist((best["x"], best["y"]), (label["x"], label["y"])) <= tol:
                hit += 1
        else:
            absent += 1
            false += bool(best)
    return {
        "frames": len(frames),
        "recall": round(hit / visible, 3) if visible else None,
        "precision": round(hit / detections, 3) if detections else None,
        "falseDetections": round(false / absent, 3) if absent else None,
        "visible": visible,
        "absent": absent,
    }


def better(candidate, baseline, min_gain=0.03, max_precision_loss=0.02):
    """Keep new weights only if they find clearly more balls without more false ones."""
    if not candidate.get("visible") or candidate.get("recall") is None:
        return False
    if baseline.get("recall") is None:
        return True
    precision_ok = (candidate.get("precision") or 0) >= (baseline.get("precision") or 0) - max_precision_loss
    return candidate["recall"] >= baseline["recall"] + min_gain and precision_ok


def train(dataset, base_weights, out, epochs=60, imgsz=640, device="cpu", batch=16):
    """Fine-tune and return the path of the best weights."""
    from ultralytics import YOLO

    model = YOLO(str(base_weights))
    model.train(
        data=str(Path(dataset) / "data.yaml"),
        epochs=epochs,
        imgsz=imgsz,
        device=device,
        batch=batch,
        project=str(out),
        name="ball",
        exist_ok=True,
        patience=15,
        # Small, faint objects: no mosaic downscaling tricks that shrink the ball further.
        mosaic=0.5,
        scale=0.2,
        fliplr=0.5,
        hsv_v=0.3,
        verbose=False,
        plots=False,
    )
    best = Path(out) / "ball" / "weights" / "best.pt"
    return best if best.is_file() else Path(out) / "ball" / "weights" / "last.pt"


def single_class_ball(weights):
    """Ultralytics names the trained class 'ball'; the detector looks for a 'ball' class."""
    from ultralytics import YOLO

    names = YOLO(str(weights)).names
    return any("ball" in str(n).lower() for n in names.values())


def seed_everything(seed=0):
    random.seed(seed)
    np.random.seed(seed)


def _zip(directory):
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for path in sorted(Path(directory).rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(directory))
    return buffer.getvalue()


def _registry_path(models):
    return Path(models) / "ball-model.json"


def registry(models):
    try:
        return json.loads(_registry_path(models).read_text())
    except (OSError, ValueError):
        return {"active": None, "runs": []}


def run_training(root, models, base_weights, epochs=60, owner=None, remote=None, device="cpu", now=0.0, progress=None):
    """Build the dataset from every labelled match, fine-tune, validate, keep if better.

    `remote(dataset_zip, base_bytes, epochs)` runs training on a GPU (Modal) and
    returns the same dict as modal_app.train_ball; without it, training runs here.
    """
    import hashlib

    from app.vision.ball import TiledBallDetector

    say = progress or (lambda message: None)
    jobs = labelled_jobs(root)
    if owner:
        jobs = [d for d in jobs if json.loads((d / "status.json").read_text()).get("owner") == owner]
    train_set, val_set, how = split(jobs)
    if not train_set or not val_set:
        raise ValueError("Label the ball in at least one match first (about 150 frames).")
    work = Path(root) / "training"
    work.mkdir(exist_ok=True)
    dataset = work / "dataset"
    say("Building the training set from your labels")
    counts = build_dataset(train_set, val_set, dataset)
    if counts["train"]["positive"] < 20 or counts["valFrames"] < 10:
        raise ValueError(
            f"Too few labels to train ({counts['train']['positive']} balls to learn from, "
            f"{counts['valFrames']} frames to check against). Label more frames."
        )
    if remote:
        say("Training on the GPU")
        out = remote(_zip(dataset), Path(base_weights).read_bytes(), epochs)
        weights_bytes, baseline, candidate = out["weights"], out["baseline"], out["candidate"]
    else:
        say("Training on this machine")
        best = train(dataset, base_weights, work / "runs", epochs=epochs, device=device)
        weights_bytes = best.read_bytes()
        baseline = score_detector(TiledBallDetector(base_weights, device), dataset)
        candidate = score_detector(TiledBallDetector(best, device), dataset)
    name = f"ball-{hashlib.sha256(weights_bytes).hexdigest()[:16]}.pt"
    Path(models).mkdir(parents=True, exist_ok=True)
    (Path(models) / name).write_bytes(weights_bytes)
    kept = better(candidate, baseline)
    reg = registry(models)
    reg["runs"].append(
        {
            "at": now,
            "weights": name,
            "base": Path(base_weights).name,
            "matches": len(jobs),
            "split": how,
            "counts": counts,
            "baseline": baseline,
            "candidate": candidate,
            "kept": kept,
            "epochs": epochs,
        }
    )
    if kept:
        reg["active"] = name
    temporary = _registry_path(models).with_suffix(".tmp")
    temporary.write_text(json.dumps(reg, indent=1))
    temporary.replace(_registry_path(models))
    return reg["runs"][-1]
