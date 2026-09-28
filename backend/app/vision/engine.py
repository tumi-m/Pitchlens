"""Local player/ball detection and camera-compensated short-term tracking."""

import hashlib
import json
import math
import os
import time
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
from sklearn.cluster import KMeans
from threadpoolctl import threadpool_limits

from app.vision.ball import create_ball_detector
from app.vision.ball_tracking import BallTracker
from app.vision.faint import ball_size_prior, difference_candidates, recover_ball
from app.vision.metrics import derive_metrics
from app.vision.profiles import model_paths
from app.vision.tracking import MotionTracker, camera_motion


def file_sha256(path):
    """Stream the hash: reading a 500 MB match into memory can exhaust a small container."""
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def worker_threads():
    """Hosted containers can use more cores than the laptop default of four."""
    default = min(4, os.cpu_count() or 2)
    try:
        return max(1, int(os.getenv("VISION_THREADS") or default))
    except ValueError:
        return default


def probe(path):
    cap = cv2.VideoCapture(str(path))
    try:
        fps = cap.get(cv2.CAP_PROP_FPS)
        count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        width, height = int(cap.get(3)), int(cap.get(4))
        ok, _ = cap.read()
        fourcc = int(cap.get(cv2.CAP_PROP_FOURCC) or 0).to_bytes(4, "little")
        if not ok and fourcc.upper() == b"AV01":
            raise ValueError(
                "AV1 video cannot be decoded here. Export or re-save it as an H.264 MP4."
            )
        if not ok or not math.isfinite(fps) or fps <= 0 or count <= 0:
            raise ValueError("Video cannot be decoded.")
        duration = count / fps
        if duration > 4 * 3600 or width > 4096 or height > 4096:
            raise ValueError(
                "Local vision supports up to four hours and 4096-pixel video dimensions."
            )
        return {
            "fps": fps,
            "frameCount": int(count),
            "width": width,
            "height": height,
            "duration": duration,
        }
    finally:
        cap.release()


def colour_signature(frame):
    """Normalised hue/saturation histogram: stable under pans, changes at cuts."""
    small = cv2.resize(frame, (160, 90))
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [30, 16], [0, 180, 0, 256])
    return cv2.normalize(hist, hist).astype(np.float32)


def vote_teams(frames):
    """Give every observation of a track the team it was assigned most often.

    Per-frame colour on a 50-pixel player is noisy; the track as a whole is not.
    Only tracks with at least 3 confident votes and a 60% majority are relabelled.
    """
    votes = {}
    for f in frames:
        for p in f["players"]:
            if p["team"] in (0, 1):
                votes.setdefault(p["id"], [0, 0])[p["team"]] += 1
    decided = {}
    for track, (a, b) in votes.items():
        if a + b >= 3 and max(a, b) / (a + b) >= 0.6:
            decided[track] = 0 if a >= b else 1
    for f in frames:
        for p in f["players"]:
            if p["id"] in decided and p.get("role") in (None, "person", "player"):
                p["team"] = decided[p["id"]]
    return frames


def field_mask(frame):
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([25, 35, 30]), np.array([95, 255, 255]))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((13, 13), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    result = np.zeros(mask.shape, np.uint8)
    if contours:
        largest = max(contours, key=cv2.contourArea)
        if cv2.contourArea(largest) > mask.size * 0.15:
            cv2.drawContours(result, [largest], -1, 255, -1)
    return result


def pitch_colour(frame, mask):
    """Median LAB colour of the detected playing surface in this frame, if any."""
    if mask is None or not mask.any():
        return None
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    return np.median(lab[mask > 0][::17], axis=0).astype(float)


def jersey(frame, box, pitch=None):
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    patch = frame[
        max(0, int(y1 + h * 0.15)) : int(y1 + h * 0.55),
        max(0, int(x1 + w * 0.2)) : int(x2 - w * 0.2),
    ]
    if patch.size == 0:
        return None
    # White and dark kits are valid colours too. A saturation gate discarded them.
    # Grass showing around a small player drags every kit towards grey-green:
    # drop pitch-coloured pixels before taking the median.
    # Compare against this frame's measured pitch colour, not a fixed green
    # band, so a green or mint kit that differs from the grass survives.
    pixels = patch.reshape(-1, 3)
    if pitch is not None:
        lab = cv2.cvtColor(patch, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(float)
        grass = np.linalg.norm(lab - pitch, axis=1) < 18
        if (~grass).sum() >= 6:
            pixels = pixels[~grass]
    if len(pixels) < 3:
        return None
    colour = np.median(pixels, axis=0).astype(np.uint8)
    return cv2.cvtColor(colour.reshape(1, 1, 3), cv2.COLOR_BGR2LAB).reshape(3).astype(float)


def inside_field(mask, box):
    x1, y1, x2, y2 = box
    x, y = int((x1 + x2) / 2), int(y2 - 2)
    h, w = mask.shape
    return 0 <= x < w and 0 <= y < h and bool(mask[y, x]) and y2 - y1 >= 12


def train_colours(features, n_clusters=4):
    if len(features) < 15:
        raise ValueError("Too few visible players to identify kits. Choose a clearer match video.")
    features = np.array(features)
    # Tiny kit datasets suffer severe native-thread overhead on some desktop runtimes.
    with threadpool_limits(limits=1):
        km = KMeans(n_clusters=n_clusters, n_init=10, random_state=42).fit(features)
    counts = Counter(km.labels_)
    selected = [i for i, _ in counts.most_common(2)]
    if len(selected) < 2:
        raise ValueError("The two kits could not be separated reliably by colour.")
    centres = km.cluster_centers_[selected]
    if np.linalg.norm(centres[0] - centres[1]) < 25:
        raise ValueError("The two kits could not be separated reliably by colour.")
    colours = []
    for c in centres:
        rgb = cv2.cvtColor(np.uint8(c).reshape(1, 1, 3), cv2.COLOR_LAB2RGB).reshape(3)
        colours.append("#" + "".join(f"{v:02x}" for v in rgb))
    return centres, colours


def assign_team(feature, centres, use_hue=True):
    if feature is None:
        return -1
    distances = np.linalg.norm(centres - feature, axis=1)
    idx = int(np.argmin(distances))
    feature_hsv = cv2.cvtColor(
        cv2.cvtColor(np.uint8(feature).reshape(1, 1, 3), cv2.COLOR_LAB2BGR), cv2.COLOR_BGR2HSV
    )[0, 0]
    centre_hsv = cv2.cvtColor(
        cv2.cvtColor(np.uint8(centres[idx]).reshape(1, 1, 3), cv2.COLOR_LAB2BGR), cv2.COLOR_BGR2HSV
    )[0, 0]
    hue = abs(int(feature_hsv[0]) - int(centre_hsv[0]))
    hue = min(hue, 180 - hue)
    # Hue is unstable for low-saturation colours, including striped navy/white kits.
    hue_matches = not use_hue or hue <= 16 or (feature_hsv[1] < 80 and centre_hsv[1] < 80)
    return (
        idx if distances[idx] < 50 and abs(distances[0] - distances[1]) > 12 and hue_matches else -1
    )


def run_video(
    path,
    output,
    progress=lambda **kw: None,
    cancelled=lambda: False,
    sample_fps=3,
    max_seconds=None,
    profile="general",
):
    import torch
    from ultralytics import YOLO, settings

    settings.update({"sync": False})
    torch.set_num_threads(worker_threads())
    cv2.setNumThreads(1)
    model_path, ball_path = [p.resolve() for p in model_paths(profile)]
    if not model_path.is_file():
        raise ValueError("Vision weights are missing. Run python scripts/setup_vision.py first.")
    if not math.isfinite(sample_fps) or not 1 <= sample_fps <= 15:
        raise ValueError("Sampling rate must be between 1 and 15 frames/sec")
    if max_seconds is not None and (not math.isfinite(max_seconds) or max_seconds <= 0):
        raise ValueError("Diagnostic duration must be positive")
    meta = probe(path)
    duration = min(meta["duration"], max_seconds) if max_seconds else meta["duration"]
    stride = max(1, math.ceil(meta["fps"] / sample_fps))
    effective_fps = meta["fps"] / stride
    device = os.getenv("VISION_DEVICE", "cpu")
    model = YOLO(str(model_path))
    if not ball_path.is_file():
        raise ValueError(
            "Football ball weights are missing. Run python scripts/setup_vision.py first."
        )
    ball_detector = create_ball_detector(ball_path, device)
    names = model.names
    people = [
        i for i, n in names.items() if n.lower() in ("person", "player", "goalkeeper", "referee")
    ]
    if not people:
        raise ValueError("Player model must contain a named person/player class.")

    def detect_batch(batch):
        """Player detection for a list of frames in one model call."""
        results = model.predict(
            batch,
            conf=0.15,
            imgsz=1280 if "player" in names.values() else 960,
            classes=people,
            device=device,
            # FP16 on a GPU roughly doubles throughput at no practical accuracy cost.
            quantize=16 if device.startswith("cuda") else None,
            verbose=False,
        )
        return [
            (
                r.boxes.xyxy.cpu().numpy(),
                r.boxes.conf.cpu().numpy(),
                r.boxes.cls.cpu().numpy().astype(int),
            )
            for r in results
        ]

    def detect(frame):
        return detect_batch([frame])[0]

    # Batches amortise model overhead; a GPU processes 8 frames almost as fast as one.
    batch_size = int(os.getenv("VISION_BATCH", "8" if device.startswith("cuda") else "2"))

    progress(stage="Learning kit colours from the footage", progress=1)
    cap = cv2.VideoCapture(str(path))
    features = []
    try:
        # Spread the fit over the video so pre-match lineups do not determine every kit.
        for index, t in enumerate(np.linspace(min(60, duration * 0.1), max(0, duration - 0.5), 24)):
            if cancelled():
                raise InterruptedError("Analysis cancelled")
            cap.set(cv2.CAP_PROP_POS_MSEC, float(t * 1000))
            ok, frame = cap.read()
            if not ok:
                continue
            boxes, scores, classes = detect(frame)
            mask = field_mask(frame)
            pitch = pitch_colour(frame, mask)
            for box, c in zip(boxes, classes):
                if names[c].lower() in ("person", "player") and inside_field(mask, box):
                    f = jersey(frame, box, pitch)
                    if f is not None:
                        features.append(f)
            progress(
                stage=f"Learning kit colours · sample {index + 1}/24",
                progress=round(1 + (index + 1) / 24 * 4, 1),
            )
    finally:
        cap.release()
    # A role-aware detector has already removed officials and goalkeepers from fitting.
    # Four clusters otherwise split one striped kit and discard part of that team.
    role_aware = "player" in [name.lower() for name in names.values()]
    centres, colours = train_colours(features, n_clusters=2 if role_aware else 4)
    cap = cv2.VideoCapture(str(path))
    tracker = MotionTracker()
    ball_tracker = BallTracker()
    frames = []
    scene = 0
    frame_num = 0
    previous_gray = None
    previous_frame = None
    previous_boxes = []
    last_t = -1.0
    started = time.monotonic()
    reported = started
    progress(
        stage="Detecting players, tracking kits and following the ball",
        progress=5,
        processedSeconds=0,
    )
    matrices = []  # per sampled frame: affine mapping the previous frame into this one
    pending = []  # decoded frames awaiting a batched detection

    def process(batch):
        nonlocal previous_gray, previous_frame, previous_boxes, scene, reported
        detections = detect_batch([b[1] for b in batch])
        ball_batches = ball_detector.detect_batch([b[1] for b in batch], threshold=0.05)
        for (t, frame), (boxes, scores, classes), raw_candidates in zip(
            batch, detections, ball_batches
        ):
            mask = field_mask(frame)
            pitch = pitch_colour(frame, mask)
            gray = colour_signature(frame)
            matrix, motion_ok = camera_motion(previous_frame, frame, previous_boxes)
            # A fast pan or zoom defeats motion estimation but keeps the colour
            # make-up of the shot; a real cut changes it. Pixel differences alone
            # flagged every quick pan as a cut and reset all tracks.
            cut = (
                previous_gray is not None
                and not motion_ok
                and cv2.compareHist(gray, previous_gray, cv2.HISTCMP_BHATTACHARYYA) > 0.5
            )
            if cut:
                scene += 1
            previous_gray = gray
            observations = [
                {
                    "team": assign_team(jersey(frame, box, pitch), centres, use_hue=not role_aware)
                    if names[c].lower() in ("person", "player")
                    else -1,
                    "role": names[c].lower(),
                    "box": [round(float(x), 1) for x in box],
                    "confidence": round(float(score), 3),
                }
                for box, score, c in zip(boxes, scores, classes)
                if c in people and inside_field(mask, box)
            ]
            players = tracker.update(observations, t, matrix, cut=cut)
            # Weak neural candidates plus difference-imaging candidates; the
            # track-before-detect pass after the loop decides which are real.
            diameter = ball_size_prior(players, frame.shape[0])
            candidates = list(raw_candidates)
            if motion_ok and not cut:
                candidates += difference_candidates(
                    previous_frame, frame, matrix, players, diameter, mask=mask
                )
            candidates.sort(key=lambda c: -c["confidence"])
            candidates = candidates[:12]
            strong = [c for c in candidates if c["confidence"] >= 0.15]
            previous_frame = frame
            previous_boxes = [p["box"] for p in players]
            # Airborne balls can be outside the green surface; temporal association
            # resolves candidates instead of rejecting them by background colour.
            ball = ball_tracker.update(strong, t, matrix, frame.shape, cut=cut)
            matrices.append(None if cut or not motion_ok else matrix)
            frames.append(
                {
                    "t": round(t, 3),
                    "scene": scene,
                    "players": players,
                    "ball": ball,
                    "ballCandidates": candidates,
                }
            )
            # Report on wall time, not frame count: a slow CPU host can take
            # minutes per 50 frames, which looks frozen.
            now = time.monotonic()
            if now - reported >= 3:
                reported = now
                elapsed = now - started
                progress(
                    stage="Detecting players, tracking kits and following the ball",
                    progress=round(5 + t / duration * 88, 1),
                    processedSeconds=round(t, 1),
                    elapsedSeconds=round(elapsed),
                    etaSeconds=round(elapsed / max(t, 0.1) * (duration - t)),
                )

    try:
        while frame_num / meta["fps"] < duration:
            if cancelled():
                raise InterruptedError("Analysis cancelled")
            ok = cap.grab()
            if not ok:
                # Container frame counts are estimates: trimmed clips (edit lists)
                # routinely declare several seconds more than they hold. The real
                # end of the stream is the end of the analysis, reported below.
                if frames or pending:
                    break
                raise ValueError("Video decoding stopped before the requested duration.")
            if frame_num % stride:
                frame_num += 1
                continue
            ok, frame = cap.retrieve()
            if not ok:
                if frames or pending:
                    break
                raise ValueError("Video decoding stopped before the end.")
            # Phone footage is often variable frame rate: prefer the decoder's
            # presentation time so overlays line up with the video.
            position = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
            index_time = frame_num / meta["fps"]
            # Trust decoder timestamps only while they agree with the frame count:
            # fragmented/streaming MP4s can report drifting or offset positions,
            # which would misplace every overlay box.
            t = (
                position
                if math.isfinite(position)
                and position > last_t
                and abs(position - index_time) <= 0.5
                else index_time
            )
            if (frames or pending) and t <= last_t:
                t = last_t + 1 / meta["fps"]
            last_t = t
            pending.append((t, frame))
            if len(pending) >= batch_size:
                process(pending)
                pending = []
            frame_num += 1
        if pending:
            process(pending)
            pending = []
    finally:
        cap.release()
    if not frames or not any(f["players"] for f in frames):
        raise ValueError(
            "No on-pitch players detected. This video cannot be analysed by the installed model."
        )
    progress(stage="Confirming faint ball tracks across frames", progress=94)
    diagonal = math.hypot(meta["width"], meta["height"])
    # VISION_FAINT=0 disables track-before-detect recovery (for comparisons).
    faint = (
        recover_ball(frames, matrices, diagonal, effective_fps)
        if os.getenv("VISION_FAINT", "1") != "0"
        else {"recovered": 0, "inferred": 0, "disabled": True}
    )
    progress(stage="Measuring temporal observations", progress=96)
    analysed_duration = min(duration, max(frame_num / meta["fps"], last_t))
    vote_teams(frames)
    metrics = derive_metrics(frames, effective_fps, analysed_duration)
    result = {
        "schemaVersion": 1,
        "pipelineVersion": "local-vision-2.0",
        "profile": profile,
        "source": "computer-vision",
        "videoSha256": file_sha256(path),
        "model": model_path.name,
        "modelSha256": file_sha256(model_path),
        "ballModel": ball_path.name,
        "ballModelSha256": file_sha256(ball_path),
        "ballInference": "whole-frame-onnx" if ball_path.suffix == ".onnx" else "overlapping-tiles",
        "ballTracking": "camera-compensated-observations+track-before-detect",
        "ballRecovery": faint,
        "video": meta,
        "analysedDuration": analysed_duration,
        "sampleFps": effective_fps,
        "teams": [{"id": i, "label": f"Kit {'AB'[i]}", "colour": c} for i, c in enumerate(colours)],
        "metrics": metrics,
        "frames": frames,
        "limitations": [
            "Ball confidence scores are not calibrated probabilities.",
            "Faint ball positions are confirmed by consistency across frames (track-before-detect); short gaps on a confirmed path are bridged and marked inferred.",
            "Possession is visible ball-to-player proximity, not official match possession.",
            "Passes and turnovers are unreviewed temporal candidates, not verified match events.",
            "Track IDs change after occlusion and cuts; they are not player identities.",
            "Positions are image coordinates. Speed, distance, xG and score are not measured.",
            "Green-surface filtering can include sideline players or miss players near boundaries.",
        ]
        + (
            [
                f"The file declares {meta['duration']:.0f} s but decoding ended at "
                f"{analysed_duration:.0f} s; results cover the decodable part."
            ]
            if analysed_duration < duration * 0.97
            else []
        ),
    }
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(".tmp")
    temp.write_text(json.dumps(result, separators=(",", ":")))
    temp.replace(output)
    progress(stage="Analysis complete", progress=100)
    return result
