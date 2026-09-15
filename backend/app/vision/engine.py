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

from app.vision.ball import BallDetector
from app.vision.metrics import derive_metrics
from app.vision.tracking import MotionTracker, camera_motion


def probe(path):
    cap = cv2.VideoCapture(str(path))
    try:
        fps = cap.get(cv2.CAP_PROP_FPS)
        count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        width, height = int(cap.get(3)), int(cap.get(4))
        ok, _ = cap.read()
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


def jersey(frame, box):
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    patch = frame[
        max(0, int(y1 + h * 0.18)) : int(y1 + h * 0.48),
        max(0, int(x1 + w * 0.25)) : int(x2 - w * 0.25),
    ]
    if patch.size == 0:
        return None
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    pixels = patch[(hsv[:, :, 1] > 45) & (hsv[:, :, 2] > 55)]
    if len(pixels) < 3:
        return None
    colour = np.median(pixels, axis=0).astype(np.uint8)
    return cv2.cvtColor(colour.reshape(1, 1, 3), cv2.COLOR_BGR2LAB).reshape(3).astype(float)


def inside_field(mask, box):
    x1, y1, x2, y2 = box
    x, y = int((x1 + x2) / 2), int(y2 - 2)
    h, w = mask.shape
    return 0 <= x < w and 0 <= y < h and bool(mask[y, x]) and y2 - y1 >= 12


def train_colours(features):
    if len(features) < 15:
        raise ValueError("Too few visible players to identify kits. Choose a clearer match video.")
    features = np.array(features)
    km = KMeans(n_clusters=4, n_init=10, random_state=42).fit(features)
    counts = Counter(km.labels_)
    selected = [i for i, _ in counts.most_common(2)]
    centres = km.cluster_centers_[selected]
    if np.linalg.norm(centres[0] - centres[1]) < 25:
        raise ValueError("The two kits could not be separated reliably by colour.")
    colours = []
    for c in centres:
        rgb = cv2.cvtColor(np.uint8(c).reshape(1, 1, 3), cv2.COLOR_LAB2RGB).reshape(3)
        colours.append("#" + "".join(f"{v:02x}" for v in rgb))
    return centres, colours


def assign_team(feature, centres):
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
    return (
        idx if distances[idx] < 50 and abs(distances[0] - distances[1]) > 12 and hue <= 16 else -1
    )


def run_video(
    path,
    output,
    progress=lambda **kw: None,
    cancelled=lambda: False,
    sample_fps=3,
    max_seconds=None,
):
    import torch
    from ultralytics import YOLO, settings

    settings.update({"sync": False})
    torch.set_num_threads(min(4, os.cpu_count() or 2))
    cv2.setNumThreads(1)
    model_path = Path(os.environ.get("VISION_MODEL_PATH", "models/yolo11s.pt")).resolve()
    if not model_path.is_file():
        raise ValueError("Vision weights are missing. Run python scripts/setup_vision.py first.")
    if not math.isfinite(sample_fps) or not 1 <= sample_fps <= 15:
        raise ValueError("Sampling rate must be between 1 and 15 frames/sec")
    if max_seconds is not None and (not math.isfinite(max_seconds) or max_seconds <= 0):
        raise ValueError("Diagnostic duration must be positive")
    meta = probe(path)
    duration = min(meta["duration"], max_seconds) if max_seconds else meta["duration"]
    stride = max(1, round(meta["fps"] / sample_fps))
    effective_fps = meta["fps"] / stride
    device = os.getenv("VISION_DEVICE", "cpu")
    model = YOLO(str(model_path))
    ball_path = Path(os.getenv("VISION_BALL_MODEL_PATH", "models/football-ball.onnx")).resolve()
    if not ball_path.is_file():
        raise ValueError(
            "Football ball weights are missing. Run python scripts/setup_vision.py first."
        )
    ball_detector = BallDetector(ball_path)
    names = model.names
    people = [
        i for i, n in names.items() if n.lower() in ("person", "player", "goalkeeper", "referee")
    ]
    if not people:
        raise ValueError("Player model must contain a named person/player class.")

    def detect(frame):
        r = model.predict(
            frame, conf=0.18, imgsz=960, classes=people, device=device, verbose=False
        )[0]
        boxes = r.boxes.xyxy.cpu().numpy()
        scores = r.boxes.conf.cpu().numpy()
        classes = r.boxes.cls.cpu().numpy().astype(int)
        return boxes, scores, classes

    progress(stage="Learning kit colours from the footage", progress=1)
    cap = cv2.VideoCapture(str(path))
    features = []
    try:
        # Spread the fit over the video so pre-match lineups do not determine every kit.
        for t in np.linspace(min(60, duration * 0.1), max(0, duration - 0.5), 24):
            if cancelled():
                raise InterruptedError("Analysis cancelled")
            cap.set(cv2.CAP_PROP_POS_MSEC, float(t * 1000))
            ok, frame = cap.read()
            if not ok:
                continue
            boxes, scores, classes = detect(frame)
            mask = field_mask(frame)
            for box, c in zip(boxes, classes):
                if c in people and inside_field(mask, box):
                    f = jersey(frame, box)
                    if f is not None:
                        features.append(f)
    finally:
        cap.release()
    centres, colours = train_colours(features)
    cap = cv2.VideoCapture(str(path))
    tracker = MotionTracker()
    frames = []
    scene = 0
    frame_num = 0
    previous_gray = None
    previous_frame = None
    previous_boxes = []
    started = time.monotonic()
    try:
        while frame_num / meta["fps"] < duration:
            if cancelled():
                raise InterruptedError("Analysis cancelled")
            ok = cap.grab()
            if not ok:
                raise ValueError("Video decoding stopped before the requested duration.")
            if frame_num % stride:
                frame_num += 1
                continue
            ok, frame = cap.retrieve()
            if not ok:
                raise ValueError("Video decoding stopped before the end.")
            t = frame_num / meta["fps"]
            mask = field_mask(frame)
            gray = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (160, 90))
            matrix, motion_ok = camera_motion(previous_frame, frame, previous_boxes)
            cut = (
                previous_gray is not None
                and not motion_ok
                and np.mean(cv2.absdiff(gray, previous_gray)) > 38
            )
            if cut:
                scene += 1
            previous_gray = gray
            boxes, scores, classes = detect(frame)
            observations = [
                {
                    "team": assign_team(jersey(frame, box), centres),
                    "box": [round(float(x), 1) for x in box],
                    "confidence": round(float(score), 3),
                }
                for box, score, c in zip(boxes, scores, classes)
                if c in people and inside_field(mask, box)
            ]
            players = tracker.update(observations, t, matrix, cut=cut)
            previous_frame = frame
            previous_boxes = [p["box"] for p in players]
            candidates = [
                b
                for b in ball_detector.detect(frame, threshold=0.15)
                if mask[int(b["y"]), int(b["x"])]
            ]
            candidates.sort(key=lambda b: b["confidence"], reverse=True)
            ball = candidates[0] if candidates else None
            if len(candidates) > 1 and candidates[1]["confidence"] > 0.8 * ball["confidence"]:
                if math.hypot(candidates[1]["x"] - ball["x"], candidates[1]["y"] - ball["y"]) > 15:
                    ball = None
            frames.append({"t": round(t, 3), "scene": scene, "players": players, "ball": ball})
            if len(frames) % 50 == 0:
                elapsed = time.monotonic() - started
                progress(
                    stage="Detecting players, tracking kits and following the ball",
                    progress=round(5 + t / duration * 90, 1),
                    processedSeconds=round(t, 1),
                    elapsedSeconds=round(elapsed),
                    etaSeconds=round(elapsed / max(t, 0.1) * (duration - t)),
                )
            frame_num += 1
    finally:
        cap.release()
    if not frames or not any(f["players"] for f in frames):
        raise ValueError(
            "No on-pitch players detected. This video cannot be analysed by the installed model."
        )
    progress(stage="Measuring temporal observations", progress=96)
    analysed_duration = min(duration, (frame_num + 1) / meta["fps"])
    metrics = derive_metrics(frames, effective_fps, analysed_duration)
    result = {
        "schemaVersion": 1,
        "pipelineVersion": "local-vision-1.2",
        "source": "computer-vision",
        "model": model_path.name,
        "modelSha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
        "ballModel": ball_path.name,
        "ballModelSha256": hashlib.sha256(ball_path.read_bytes()).hexdigest(),
        "video": meta,
        "analysedDuration": analysed_duration,
        "sampleFps": effective_fps,
        "teams": [{"id": i, "label": f"Kit {'AB'[i]}", "colour": c} for i, c in enumerate(colours)],
        "metrics": metrics,
        "frames": frames,
        "limitations": [
            "Ball confidence scores are not calibrated probabilities.",
            "Possession is visible ball-to-player proximity, not official match possession.",
            "Passes and turnovers are unreviewed temporal candidates, not verified match events.",
            "Track IDs change after occlusion and cuts; they are not player identities.",
            "Positions are image coordinates. Speed, distance, xG and score are not measured.",
            "Green-surface filtering can include sideline players or miss players near boundaries.",
        ],
    }
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(".tmp")
    temp.write_text(json.dumps(result, separators=(",", ":")))
    temp.replace(output)
    progress(stage="Analysis complete", progress=100)
    return result
