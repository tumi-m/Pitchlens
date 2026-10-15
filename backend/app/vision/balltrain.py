"""Fine-tune the ball detector on the reviewer's own labels, and only keep it if it is better.

1. split: labelled matches are divided by match into a training part and a
   test part (the test part needs about 30 frames with a visible ball). With
   one usable match, by time: the last 30% is the test part. Jobs on the same
   footage (re-runs, re-uploads) are one match, and nothing the active model
   (or the models it was fine-tuned from) trained on is ever a test frame.
2. build_dataset: every training frame is cut into the same overlapping tiles
   the detector sees at inference (ball.tile_frame), with a YOLO box around
   the labelled ball; empty tiles (boots, socks, lights, line crossings) are
   kept as negatives at a fixed ratio. A slice of the training frames is
   Ultralytics' own validation set for choosing the best epoch; the test
   frames are never shown to training at all.
3. train: Ultralytics fine-tuning from the current ball weights.
4. score_detector: frame-level ball recall/precision on the test frames, for
   the current and the new weights. The new weights are kept only when they
   find more balls without more false ones, by more than chance (see `better`).
"""

import contextlib
import json
import math
import os
import random
import shutil
from pathlib import Path

import cv2
import numpy as np

from app.vision import balllabels
from app.vision.ball import tile_frame
from app.vision.faint import ball_size_prior

MIN_TEST_VISIBLE = 30
MIN_TRAIN_VISIBLE = 20
MIN_MATCH_LABELS = 10
NOT_ENOUGH_HELD_OUT = "not enough held-out balls"
MATCH_WITHDRAWN = "match-withdrawn"


def _usable(directory):
    """The footage is still stored (deleted after the retention period)."""
    try:
        status = json.loads((directory / "status.json").read_text())
    except (OSError, ValueError):
        return False
    return (directory / "video").is_file() and not status.get("videoDeleted")


def consented(directory):
    """The match's owner ticked the training opt-in (status.json trainingConsent; absent = no)."""
    try:
        status = json.loads((directory / "status.json").read_text())
    except (OSError, ValueError):
        return False
    consent = status.get("trainingConsent") if isinstance(status, dict) else None
    return isinstance(consent, dict) and consent.get("agreed") is True


def labelled_jobs(root, usable=_usable):
    """Match folders with ball labels, a finished result, the footage and the owner's consent.

    A match without consent is neither trained nor tested on: its test frames
    would be in the dataset sent to the GPU too.
    """
    out = []
    for directory in sorted(Path(root).iterdir()):
        if not directory.is_dir() or not (directory / "balllabels.json").is_file() or not (directory / "result.json").is_file():
            continue
        if not consented(directory) or not usable(directory):
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


def _visible(items):
    """Labels with a visible ball among (directory, index, label, source frame) items."""
    return sum(1 for item in items if item[2].get("visible"))


def _chain_root(directory):
    """The upload a re-run was made from (re-runs of re-runs included)."""
    seen, current = set(), directory.name
    while current not in seen:
        seen.add(current)
        try:
            source = json.loads((directory.parent / current / "status.json").read_text()).get("sourceJobId")
        except (OSError, ValueError):
            break  # the original was deleted: its id still names the footage
        if not isinstance(source, str) or not source:
            break
        current = source
    return current


def _footage_groups(jobs):
    """Jobs grouped by footage: the same video hash, re-run chain or hard-linked file.

    Each group is one match: its labels are merged, one per source frame (a
    re-run labels the same pictures again), and each item stays tied to the
    job whose frame cache holds it. Returns [{"keys", "items": [(directory,
    index, label, source frame)]}] with items in source-frame order.
    """
    parent = list(range(len(jobs)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    owners, keys, items = {}, [], []
    for n, directory in enumerate(jobs):
        result, labelled = _frames_of(directory)
        own = {f"root:{_chain_root(directory)}"}
        if isinstance(result.get("videoSha256"), str) and result["videoSha256"]:
            own.add(f"sha:{result['videoSha256']}")
        try:
            stat = os.stat(directory / "video")
            inode = f"inode:{stat.st_dev}:{stat.st_ino}"  # matching only, never recorded
            own.add(inode)
        except OSError:
            pass
        keys.append(own)
        items.append([(directory, i, label, balllabels.source_frame(result, i)) for i, label in labelled])
        for key in own:
            if key in owners:
                parent[find(n)] = find(owners[key])
            else:
                owners[key] = n
    groups = {}
    for n in range(len(jobs)):
        group = groups.setdefault(find(n), {"keys": set(), "items": {}})
        group["keys"] |= {k for k in keys[n] if not k.startswith("inode:")}
        for item in items[n]:
            group["items"].setdefault(item[3], item)  # first job (by folder name) wins a duplicate
    return [
        {"keys": sorted(g["keys"]), "items": [g["items"][f] for f in sorted(g["items"])]}
        for g in groups.values()
    ]


def _per_job(items):
    """[(directory, indices)] for build_dataset, one entry per job folder."""
    by_job = {}
    for directory, i, _, _ in items:
        by_job.setdefault(directory, set()).add(i)
    return [(directory, by_job[directory]) for directory in sorted(by_job)]


def _trained(group, trained):
    """(whole group trained, source frames trained) by the active model's lineage."""
    whole, frames = False, set()
    for key in group["keys"]:
        if key in trained:
            if trained[key] is None:
                whole = True
            else:
                frames |= trained[key]
    return whole, frames


def plan(jobs, test_fraction=0.3, trained=None):
    """The split with what it trains on: {"train", "test", "how", "trainedGroups"}.

    trained: {group key: None (the whole match) or set of source frames} that
    the active model or its ancestors trained on; never put in the test part.
    """
    trained = trained or {}
    groups = []
    for group in _footage_groups(jobs):
        whole, frames = _trained(group, trained)
        eligible = [] if whole else [item for item in group["items"] if item[3] not in frames]
        groups.append({**group, "eligible": eligible})
    ranked = sorted(
        (g for g in groups if len(g["items"]) >= MIN_MATCH_LABELS),
        key=lambda g: (_visible(g["items"]), len(g["items"])),
    )
    if not ranked:
        return {"train": [], "test": [], "how": "none", "trainedGroups": []}
    if len(ranked) >= 2:
        test, seen = [], 0
        for group in sorted(ranked[:-1], key=lambda g: (_visible(g["eligible"]), len(g["eligible"]))):
            if seen >= MIN_TEST_VISIBLE:
                break
            if not group["eligible"]:
                continue
            test.append(group)
            seen += _visible(group["eligible"])
        train = [g for g in ranked if not any(g is t for t in test)]
        train_visible = sum(_visible(g["items"]) for g in train)
        if seen >= MIN_TEST_VISIBLE and train_visible >= MIN_TRAIN_VISIBLE:
            return {
                "train": [entry for g in train for entry in _per_job(g["items"])],
                # A test match's frames that an earlier model trained on are left out.
                "test": [entry for g in test for entry in _per_job(g["eligible"])],
                "how": "by-match",
                "trainedGroups": [{"keys": g["keys"], "frames": None} for g in train],
            }
    largest = ranked[-1]
    cut = int(len(largest["items"]) * (1 - test_fraction))
    early, late = largest["items"][:cut], largest["items"][cut:]
    allowed = {item[3] for item in largest["eligible"]}
    return {
        "train": _per_job(early),
        "test": _per_job([item for item in late if item[3] in allowed]),
        "how": "by-time-within-one-match",
        "trainedGroups": [{"keys": largest["keys"], "frames": sorted(item[3] for item in early)}],
    }


def split(jobs, test_fraction=0.3, trained=None):
    """(train, test, how): lists of (directory, indices).

    By match when the smaller matches can supply enough visible-ball test
    frames while the largest stays for training; otherwise by time inside the
    largest match (a weaker check, reported as such).
    """
    out = plan(jobs, test_fraction, trained)
    return out["train"], out["test"], out["how"]


def _box(label, scale, x0, y0, diameter, tile_w, tile_h):
    cx, cy = label["x"] * scale - x0, label["y"] * scale - y0
    if not (0 <= cx < tile_w and 0 <= cy < tile_h):
        return None
    d = max(4.0, diameter * scale)
    return (cx / tile_w, cy / tile_h, min(1.0, d / tile_w), min(1.0, d / tile_h))


def build_dataset(train, test, out, negatives_per_positive=1.0, val_fraction=0.15, seed=0, frame_guard=None):
    """Write a YOLO dataset to `out`; returns counts. Test frames are only kept whole, for scoring.

    Frames are read one at a time from the label cache (shared with the
    labelling screen, numbered exactly as the engine numbered them) and dropped
    after use: decoded 1080p frames are 6 MB each, and this runs inside the
    worker. Empty tiles wait on disk until the negatives are sampled.

    Every file is named after the full job id, so deleting a match can find its
    copies here. frame_guard(directory) gives the guard for that match's label
    frames (balllabels.prepare_frames).
    """
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    rng = random.Random(seed)
    counts = {"train": {"positive": 0, "negative": 0}, "val": {"positive": 0, "negative": 0}, "testFrames": 0}
    for part in ("train", "val"):
        (out / "images" / part).mkdir(parents=True)
        (out / "labels" / part).mkdir(parents=True)
    staging = out / "staging"
    staging.mkdir()
    empties = {"train": [], "val": []}

    def write(part, name, crop, box):
        cv2.imwrite(str(out / "images" / part / f"{name}.jpg"), crop)
        (out / "labels" / part / f"{name}.txt").write_text("" if box is None else "0 %.6f %.6f %.6f %.6f\n" % box)
        counts[part]["positive" if box else "negative"] += 1

    for directory, indices in train:
        result, items = _frames_of(directory)
        items = [(i, l) for i, l in items if indices is None or i in indices]
        cut = int(len(items) * (1 - val_fraction))  # the latest frames choose the epoch
        height = float((result.get("video") or {}).get("height") or 360)
        guard = frame_guard(directory) if frame_guard else None
        cache = balllabels.prepare_frames(directory, result, [i for i, _ in items], directory / "video", guard=guard)
        for k, (i, label) in enumerate(items):
            frame = cv2.imread(str(cache / f"{i}.jpg"))
            if frame is None:
                continue
            part = "train" if k < cut else "val"
            diameter = ball_size_prior(result["frames"][i].get("players") or [], height)
            for t, (x0, y0, scale, crop) in enumerate(tile_frame(frame)):
                th, tw = crop.shape[:2]
                box = _box(label, scale, x0, y0, diameter, tw, th) if label.get("visible") else None
                name = f"{directory.name}-{i}-{t}"
                if box is None:
                    cv2.imwrite(str(staging / f"{name}.jpg"), crop)
                    empties[part].append(name)
                else:
                    write(part, name, crop, box)
            del frame
    # Negatives at a fixed ratio to the positives (at least a few per part).
    for part, pool in empties.items():
        keep = min(len(pool), max(5, int(round(counts[part]["positive"] * negatives_per_positive))))
        for name in rng.sample(pool, keep):
            (staging / f"{name}.jpg").replace(out / "images" / part / f"{name}.jpg")
            (out / "labels" / part / f"{name}.txt").write_text("")
            counts[part]["negative"] += 1
    shutil.rmtree(staging)

    whole = []
    (out / "test_frames").mkdir()
    for directory, indices in test:
        result, items = _frames_of(directory)
        items = [(i, l) for i, l in items if indices is None or i in indices]
        video = result.get("video") or {}
        guard = frame_guard(directory) if frame_guard else None
        cache = balllabels.prepare_frames(directory, result, [i for i, _ in items], directory / "video", guard=guard)
        for i, label in items:
            source = cache / f"{i}.jpg"
            if not source.is_file():
                continue
            name = f"{directory.name}-{i}.jpg"
            shutil.copyfile(source, out / "test_frames" / name)  # already a JPEG: no re-encode
            whole.append({"image": name, "label": label, "width": video.get("width"), "height": video.get("height")})
    (out / "test_frames.json").write_text(json.dumps(whole))
    counts["testFrames"] = len(whole)
    (out / "data.yaml").write_text(
        f"path: {out.resolve()}\ntrain: images/train\nval: images/val\nnames:\n  0: ball\n"
    )
    return counts


def score_detector(detector, dataset):
    """Frame-level ball recall/precision of a detector on the dataset's held-out test frames.

    The top-scoring detection of each frame counts (the tracker sees more, so
    this is a conservative proxy for what the engine will retain). `hits` (one
    per visible test frame) and `falsePositives` (one per absent frame), in
    test_frames.json order, let two detectors be compared frame by frame.
    """
    dataset = Path(dataset)
    frames = json.loads((dataset / "test_frames.json").read_text())
    hit = visible = absent = false = detections = 0
    hits, false_positives = [], []
    for item in frames:
        image = cv2.imread(str(dataset / "test_frames" / item["image"]))
        found = detector.detect_batch([image], threshold=0.15)[0]
        best = max(found, key=lambda b: b["confidence"]) if found else None
        tol = balllabels.ball_tolerance(float(item.get("width") or 640), float(item.get("height") or 360))
        label = item["label"]
        if best:
            detections += 1
        if label.get("visible"):
            visible += 1
            found_it = bool(best and math.dist((best["x"], best["y"]), (label["x"], label["y"])) <= tol)
            hit += found_it
            hits.append(int(found_it))
        else:
            absent += 1
            false += bool(best)
            false_positives.append(int(bool(best)))
    return {
        "frames": len(frames),
        "recall": round(hit / visible, 3) if visible else None,
        "precision": round(hit / detections, 3) if detections else None,
        "falseDetections": round(false / absent, 3) if absent else None,
        "visible": visible,
        "absent": absent,
        "hits": hits,
        "falsePositives": false_positives,
    }


def paired_test(candidate, baseline):
    """Exact one-sided McNemar (sign) test on the visible test frames.

    b: frames only the candidate found; c: frames only the baseline found;
    p = P(X >= b) for X ~ Binomial(b + c, 1/2). None without frame-level hits.
    """
    new, old = candidate.get("hits"), baseline.get("hits")
    if not isinstance(new, list) or not isinstance(old, list) or len(new) != len(old):
        return None
    b = sum(1 for x, y in zip(new, old) if x and not y)
    c = sum(1 for x, y in zip(new, old) if y and not x)
    n = b + c
    p = sum(math.comb(n, k) for k in range(b, n + 1)) / 2**n if n else 1.0
    return {"b": b, "c": c, "p": round(p, 6)}


def better(candidate, baseline, min_gain=0.03, max_precision_loss=0.02, alpha=0.05):
    """Keep new weights only if they find clearly more balls without more false ones.

    "Clearly": on top of the gain and precision guards, the extra balls must
    not be chance. On 30 test balls, one more found is +3.3 points; a model no
    better than the old one passed the gain guard alone about a third of the
    time, and every later run fine-tunes from whatever was switched on.
    """
    if not candidate.get("visible") or candidate.get("recall") is None:
        return False
    if baseline.get("recall") is None:
        return True
    precision_ok = (candidate.get("precision") or 0) >= (baseline.get("precision") or 0) - max_precision_loss
    if not (candidate["recall"] >= baseline["recall"] + min_gain and precision_ok):
        return False
    test = paired_test(candidate, baseline)
    return test is not None and test["p"] < alpha


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


# Server-side only (never returned by GET /ball-model): which footage each kept
# model trained on, so a later run never tests on it. It names footage by
# video hash and job id, so it stays out of the public registry.
LINEAGE = "ball-lineage.json"


def _write(path, data, indent=None):
    temporary = Path(path).with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=indent))
    temporary.replace(path)


def lineage(models):
    try:
        data = json.loads((Path(models) / LINEAGE).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def trained_on(models, weights):
    """{group key: None (whole match) or set of source frames} that `weights` and
    the models it was fine-tuned from trained on."""
    records, out, seen = lineage(models), {}, set()
    while isinstance(weights, str) and weights in records and weights not in seen:
        seen.add(weights)
        record = records[weights] if isinstance(records[weights], dict) else {}
        for group in record.get("groups") or []:
            frames = group.get("frames")
            for key in group.get("keys") or []:
                if frames is None or out.get(key, set()) is None:
                    out[key] = None
                else:
                    out[key] = out.get(key, set()) | set(frames)
        weights = record.get("base")
    return out


def _public_score(score):
    """Scores as recorded in the registry: frame-level lists left out."""
    return {k: v for k, v in (score or {}).items() if k not in ("hits", "falsePositives")}


def run_training(
    root, models, base_weights, epochs=60, owner=None, remote=None, device="cpu", now=0.0, progress=None,
    usable=_usable, on_start=None, frame_guard=None, commit_guard=None,
):
    """Build the dataset from every labelled match whose owner agreed, fine-tune, validate, keep if better.

    `remote(dataset_zip, base_bytes, epochs)` runs training on a GPU (Modal) and
    returns the same dict as modal_app.train_ball; without it, training runs here.
    `on_start()` is called once the labels passed every check, right before the
    GPU (or this machine) starts training: the moment a run costs compute.
    `frame_guard` is passed to build_dataset. `commit_guard()` is held while the
    matches are checked again and the result is recorded: the worker passes the
    lock its DELETE holds, so a match cannot be deleted between the check and
    the switch (the worker's consent endpoint holds it too). Weights are
    written only when they are kept.
    """
    import hashlib

    from app.vision.ball import TiledBallDetector

    say = progress or (lambda message: None)
    jobs = labelled_jobs(root, usable)
    if owner:
        jobs = [d for d in jobs if json.loads((d / "status.json").read_text()).get("owner") == owner]
    base_name = Path(base_weights).name
    # The base is the model in use (or the stock one): neither it nor its
    # ancestors may be judged on frames they trained on.
    planned = plan(jobs, trained=trained_on(models, base_name))
    train_set, val_set, how = planned["train"], planned["test"], planned["how"]
    if not train_set:
        raise ValueError("Label the ball in at least one match and allow training on it first (about 150 frames).")
    record = {
        "at": now,
        "base": base_name,
        "matches": len(jobs),
        # A one-way fingerprint: the raw key would give access to that browser's matches.
        "ownerHash": hashlib.sha256(owner.encode()).hexdigest()[:16] if owner else None,
        "split": how,
        "epochs": epochs,
    }

    def refuse(visible, counts=None):
        # Too few held-out balls to tell a better model from luck: nothing is
        # trained or switched, and the registry is left as it is.
        return {
            **record,
            "counts": counts,
            "testVisible": visible,
            "kept": False,
            "reason": NOT_ENOUGH_HELD_OUT,
            "message": (
                f"Not enough held-out balls to judge a new model ({visible} visible in frames no model has "
                f"trained on, {MIN_TEST_VISIBLE} needed). Label the ball in a new match."
            ),
        }

    if not val_set:
        return refuse(0)

    def withdrawn():
        # Matches deleted, whose footage expired, or whose owner withdrew consent since the split was made.
        return [d for d, _ in train_set + val_set if not ((d / "status.json").is_file() and usable(d) and consented(d))]

    work = Path(root) / "training"
    work.mkdir(exist_ok=True)
    dataset = work / "dataset"
    try:
        say("Building the training set from your labels")
        counts = build_dataset(train_set, val_set, dataset, frame_guard=frame_guard)
        if counts["train"]["positive"] < 20 or counts["testFrames"] < 10:
            raise ValueError(
                f"Too few labels to train ({counts['train']['positive']} balls to learn from, "
                f"{counts['testFrames']} frames to check against). Label more frames."
            )
        if withdrawn():
            # Withdrawn while its frames were copied: they are never sent to the GPU.
            raise ValueError(
                "A match was deleted or withdrawn from training while the training set was being built; "
                "nothing was trained. Start training again."
            )
        test_visible = sum(1 for f in json.loads((dataset / "test_frames.json").read_text()) if f["label"].get("visible"))
        if test_visible < MIN_TEST_VISIBLE:
            shutil.rmtree(dataset, ignore_errors=True)
            return refuse(test_visible, counts)
    except BaseException as exc:
        # Frame copies of customers' footage never outlive a failed build (a full volume, a bad frame).
        shutil.rmtree(dataset, ignore_errors=True)
        # A deleted match's copies vanish under the build (DELETE removes them).
        if isinstance(exc, balllabels.FramesWithdrawn) or (isinstance(exc, OSError) and withdrawn()):
            raise ValueError(
                "A match was deleted while the training set was being built; nothing was trained. Start training again."
            ) from exc
        raise
    try:
        if on_start:
            on_start()
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
    finally:
        # Frame copies of customers' footage do not outlive the run.
        shutil.rmtree(dataset, ignore_errors=True)
        shutil.rmtree(work / "runs", ignore_errors=True)
    Path(models).mkdir(parents=True, exist_ok=True)
    with (commit_guard or contextlib.nullcontext)():
        reg = registry(models)
        if withdrawn():
            # A match was deleted or withdrawn from training during the run: a
            # model trained on it is never written, recorded as trained on it, or
            # switched on.
            reg["runs"].append(
                {
                    **record,
                    "counts": counts,
                    "testVisible": test_visible,
                    "baseline": _public_score(baseline),
                    "candidate": _public_score(candidate),
                    "kept": False,
                    "reason": MATCH_WITHDRAWN,
                    "message": (
                        "A match used for training was deleted or withdrawn from training during the run; "
                        "nothing was switched. Start training again."
                    ),
                }
            )
            _write(_registry_path(models), reg, indent=1)
            return reg["runs"][-1]
        name = f"ball-{hashlib.sha256(weights_bytes).hexdigest()[:16]}.pt"
        kept = better(candidate, baseline)
        if kept:
            # Rejected weights (derived from customers' frames, never loaded) are not kept.
            (Path(models) / name).write_bytes(weights_bytes)
            records = lineage(models)
            records[name] = {"base": base_name, "groups": planned["trainedGroups"]}
            _write(Path(models) / LINEAGE, records)
        reg["runs"].append(
            {
                **record,
                "weights": name,
                "counts": counts,
                "testVisible": test_visible,
                "baseline": _public_score(baseline),
                "candidate": _public_score(candidate),
                "pairedTest": paired_test(candidate, baseline),
                "kept": kept,
            }
        )
        if kept:
            reg["active"] = name
        _write(_registry_path(models), reg, indent=1)
    return reg["runs"][-1]
