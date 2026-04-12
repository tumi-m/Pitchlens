"""
Pitchlens CV Pipeline — video_processor.py

Phase 1: YOLOv11m + BoT-SORT (via ultralytics) + field masking + Kalman ball filter
Phase 2: Pitch homography (pixel -> real-world metres, 42m x 25m 5-a-side)
Phase 3: Zone pressure, velocity-based xG, per-player distance covered
"""

import asyncio
import cv2
import math
import os
import numpy as np
from collections import defaultdict
from typing import Optional

# ---------------------------------------------------------------------------
# Lazy model loading
# Models are NOT imported at module level — that blocks FastAPI startup while
# downloading weights. Instead, _get_models() loads them once on first inference
# call and caches them in module globals.
# ---------------------------------------------------------------------------
_track_model = None
_detect_model = None
_MODEL_NAME = "unloaded"

mock_firestore: dict = {}

INFER_SIZE  = 1280
VID_STRIDE  = 2      # Sample every 2nd frame — 15fps effective, full match coverage,
                     # same accuracy for tracking (players don't teleport in 1/30s)
PITCH_W_M   = 42.0   # 5-a-side width  (metres)
PITCH_H_M   = 25.0   # 5-a-side height (metres)
_H_SCALE    = 10.0   # pixels per metre in homography output space

# Highlights clip windows (seconds)
CLIP_GOAL_BEFORE   = 15.0
CLIP_GOAL_AFTER    = 8.0
CLIP_CHANCE_BEFORE = 10.0
CLIP_CHANCE_AFTER  = 5.0
CLIP_NEAR_BEFORE   = 6.0
CLIP_NEAR_AFTER    = 3.0


def _get_models():
    """Load YOLO models on first call; return cached instances on subsequent calls."""
    global _track_model, _detect_model, _MODEL_NAME
    if _track_model is not None:
        return _track_model, _detect_model

    from ultralytics import YOLO

    # Try YOLOv11m first — NMS-free architecture, ~15% better small-object recall vs v8m
    for weights, name in [("yolo11m.pt", "YOLOv11m"), ("yolov8m.pt", "YOLOv8m")]:
        try:
            _track_model  = YOLO(weights)
            _detect_model = YOLO(weights)
            _MODEL_NAME   = name
            print(f"[Pitchlens] Loaded {name}")
            return _track_model, _detect_model
        except Exception as e:
            print(f"[Pitchlens] {weights} failed: {e}")

    raise RuntimeError("Could not load any YOLO model. Check ultralytics install.")


def _get_tracker_yaml() -> str:
    """
    Return tracker config path. Prefer botsort.yaml (appearance embeddings +
    global motion compensation). Fall back to bytetrack.yaml if not found.
    """
    try:
        from ultralytics import YOLO
        import ultralytics
        pkg_dir = os.path.dirname(ultralytics.__file__)
        for name in ("botsort.yaml", "bytetrack.yaml"):
            p = os.path.join(pkg_dir, "cfg", "trackers", name)
            if os.path.exists(p):
                print(f"[Pitchlens] Using tracker: {name}")
                return name
    except Exception:
        pass
    print("[Pitchlens] Defaulting to bytetrack.yaml")
    return "bytetrack.yaml"


# ===========================================================================
# Phase 1 — Field masking
# ===========================================================================

def compute_field_mask(frame: np.ndarray) -> np.ndarray:
    """
    Segment the grass (green) area. Detections outside this mask are crowd /
    sideline staff / advertising — discarding them dramatically reduces false
    positives and fixes team classification noise.
    """
    hsv  = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([22, 25, 30]), np.array([95, 255, 255]))
    k    = np.ones((25, 25), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,  k)
    mask = cv2.dilate(mask, np.ones((40, 40), np.uint8))
    return mask


def in_field(cx: float, cy: float, mask: np.ndarray) -> bool:
    xi, yi = int(cx), int(cy)
    if xi < 0 or yi < 0 or xi >= mask.shape[1] or yi >= mask.shape[0]:
        return False
    return bool(mask[yi, xi])


# ===========================================================================
# Phase 1 — Kalman ball filter
# ===========================================================================

class BallKalmanFilter:
    """
    Constant-velocity Kalman filter. State: [x, y, vx, vy]. Measurement: [x, y].
    Coasts for up to MAX_COAST frames — fills short occlusion gaps without
    inventing positions that corrupt event detection.
    """
    MAX_COAST = 15

    def __init__(self):
        kf = cv2.KalmanFilter(4, 2)
        kf.measurementMatrix   = np.array([[1,0,0,0],[0,1,0,0]], np.float32)
        kf.transitionMatrix    = np.array([[1,0,1,0],[0,1,0,1],[0,0,1,0],[0,0,0,1]], np.float32)
        kf.processNoiseCov     = np.eye(4, dtype=np.float32) * 0.05
        kf.measurementNoiseCov = np.eye(2, dtype=np.float32) * 2.0
        self.kf     = kf
        self._init  = False
        self._coast = 0

    def update(self, x: float, y: float) -> tuple:
        m = np.array([[x], [y]], np.float32)
        if not self._init:
            self.kf.statePre  = np.array([[x],[y],[0],[0]], np.float32)
            self.kf.statePost = np.array([[x],[y],[0],[0]], np.float32)
            self._init = True
        self.kf.correct(m)
        self._coast = 0
        p = self.kf.predict()
        return float(p[0]), float(p[1])

    def predict(self) -> Optional[tuple]:
        if not self._init:
            return None
        self._coast += 1
        if self._coast > self.MAX_COAST:
            return None
        p = self.kf.predict()
        return float(p[0]), float(p[1])


# ===========================================================================
# Phase 2 — Pitch homography
# ===========================================================================

def _detect_pitch_corners(frame: np.ndarray) -> Optional[np.ndarray]:
    """
    Find the 4 corners of the visible pitch via grass segmentation + contour fitting.
    Returns [top-left, top-right, bot-right, bot-left] in pixel coords, or None.
    """
    h, w = frame.shape[:2]
    hsv  = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([22, 25, 30]), np.array([95, 255, 255]))
    k    = np.ones((20, 20), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,  k)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    largest = max(contours, key=cv2.contourArea)
    if cv2.contourArea(largest) < w * h * 0.15:
        return None

    hull   = cv2.convexHull(largest)
    peri   = cv2.arcLength(hull, True)
    approx = cv2.approxPolyDP(hull, 0.05 * peri, True)
    pts    = approx.reshape(-1, 2).astype(np.float32)

    if len(pts) < 4:
        pts = cv2.boxPoints(cv2.minAreaRect(hull)).astype(np.float32)

    if len(pts) > 4:
        pts = np.array([
            pts[pts[:, 1].argmin()],
            pts[pts[:, 0].argmax()],
            pts[pts[:, 1].argmax()],
            pts[pts[:, 0].argmin()],
        ], dtype=np.float32)

    pts = pts[:4]
    by_y = sorted(pts.tolist(), key=lambda p: p[1])
    top  = sorted(by_y[:2], key=lambda p: p[0])
    bot  = sorted(by_y[2:], key=lambda p: p[0])
    return np.array([top[0], top[1], bot[1], bot[0]], dtype=np.float32)


def compute_homography(frame: np.ndarray) -> Optional[np.ndarray]:
    """3x3 homography: pixel -> top-down pitch at _H_SCALE px/m. None on failure."""
    src = _detect_pitch_corners(frame)
    if src is None:
        return None
    W = PITCH_W_M * _H_SCALE
    H = PITCH_H_M * _H_SCALE
    dst = np.array([[0,0],[W,0],[W,H],[0,H]], dtype=np.float32)
    mat, status = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
    if mat is None or (status is not None and status.sum() < 3):
        return None
    return mat


def to_pitch(px: float, py: float, H: Optional[np.ndarray]) -> tuple:
    """Pixel -> pitch metres. Passthrough if H is None."""
    if H is None:
        return px, py
    pt = np.array([[[px, py]]], dtype=np.float32)
    tp = cv2.perspectiveTransform(pt, H)[0][0]
    return float(tp[0]) / _H_SCALE, float(tp[1]) / _H_SCALE


def clamp_pitch(x: float, y: float) -> tuple:
    return (round(min(PITCH_W_M, max(0.0, x)), 3),
            round(min(PITCH_H_M, max(0.0, y)), 3))


# ===========================================================================
# Jersey colour + team classification
# ===========================================================================

def extract_dominant_color(frame: np.ndarray, x1: int, y1: int, x2: int, y2: int):
    h   = y2 - y1
    ty1 = max(0, y1 + int(h * 0.25))
    ty2 = min(frame.shape[0], y1 + int(h * 0.60))
    tx1, tx2 = max(0, x1), min(frame.shape[1], x2)
    if ty2 <= ty1 or tx2 <= tx1:
        return None
    crop = frame[ty1:ty2, tx1:tx2]
    if crop.size == 0:
        return None
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    return (float(np.mean(hsv[:,:,0])),
            float(np.mean(hsv[:,:,1])),
            float(np.mean(hsv[:,:,2])))


def classify_teams(player_colors: dict) -> dict:
    if len(player_colors) < 2:
        return {pid: "home" for pid in player_colors}
    avgs = {pid: np.array(cols).mean(axis=0)
            for pid, cols in player_colors.items() if cols}
    if len(avgs) < 2:
        return {pid: "home" for pid in avgs}
    pids  = list(avgs.keys())
    feat  = np.array([avgs[p] for p in pids], dtype=np.float32)
    crit  = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 100, 0.2)
    _, labels, _ = cv2.kmeans(feat, 2, None, crit, 10, cv2.KMEANS_PP_CENTERS)
    return {pid: ("home" if labels[i][0] == 0 else "away") for i, pid in enumerate(pids)}


# ===========================================================================
# Phase 3 — Zone-based pressure (real momentum signal for frontend chart)
# ===========================================================================

def compute_zone_pressure(ball_positions: list, pitch_width: float, fps: float) -> list:
    """
    Split pitch into 6 longitudinal zones; track ball dwell per zone per minute.
    Positive value = home pressure (ball in away half), negative = away pressure.
    This replaces the Math.random() momentum chart in the frontend.
    """
    zone_w = pitch_width / 6
    minute_zones: dict = defaultdict(lambda: [0]*6)
    for bp in ball_positions:
        minute = max(1, int(bp["frame"] / fps / 60))
        zone   = min(5, int(bp["x"] / zone_w))
        minute_zones[minute][zone] += 1

    result = []
    for minute in sorted(minute_zones.keys()):
        z     = minute_zones[minute]
        total = sum(z) or 1
        hp    = (z[4] + z[5]) / total * 100   # ball in away half = home pressure
        ap    = (z[0] + z[1]) / total * 100   # ball in home half = away pressure
        result.append({"minute": minute, "value": round(hp - ap, 1)})
    return result


# ===========================================================================
# Phase 3 — Per-player distance covered
# ===========================================================================

def compute_player_distances(player_paths: dict, use_real: bool,
                              frame_w: int, frame_h: int) -> dict:
    out = {}
    for pid, positions in player_paths.items():
        total = 0.0
        for i in range(1, len(positions)):
            p1, p2 = positions[i-1], positions[i]
            if p2["frame"] - p1["frame"] > 5:
                continue
            dx, dy = p2["x"] - p1["x"], p2["y"] - p1["y"]
            if not use_real:
                dx = dx / frame_w * PITCH_W_M
                dy = dy / frame_h * PITCH_H_M
            total += math.hypot(dx, dy)
        out[pid] = round(total, 1)
    return out


# ===========================================================================
# Possession
# ===========================================================================

def compute_possession(ball_positions: list, player_frames: dict,
                       team_assignments: dict, proximity: float) -> dict:
    home = away = 0
    fp: dict = defaultdict(list)
    for pid, positions in player_frames.items():
        team = team_assignments.get(pid, "home")
        for p in positions:
            fp[p["frame"]].append((p["x"], p["y"], team))

    for bp in ball_positions:
        bx, by, bf = bp["x"], bp["y"], bp["frame"]
        md, nt = float("inf"), None
        for px, py, team in fp.get(bf, []):
            d = math.hypot(px-bx, py-by)
            if d < md:
                md, nt = d, team
        if nt and md < proximity:
            if nt == "home": home += 1
            else: away += 1

    total = home + away
    if total == 0:
        return {"home": 50, "away": 50}
    h = round(home / total * 100)
    return {"home": h, "away": 100 - h}


# ===========================================================================
# Ball interpolation
# ===========================================================================

def interpolate_ball(ball_positions: list, max_gap: int = 10) -> list:
    if len(ball_positions) < 2:
        return ball_positions
    sb  = sorted(ball_positions, key=lambda b: b["frame"])
    out = [sb[0]]
    for i in range(1, len(sb)):
        prev, curr = sb[i-1], sb[i]
        gap = curr["frame"] - prev["frame"]
        if 1 < gap <= max_gap:
            for f in range(prev["frame"]+1, curr["frame"]):
                t = (f - prev["frame"]) / gap
                out.append({"x": prev["x"] + (curr["x"]-prev["x"])*t,
                             "y": prev["y"] + (curr["y"]-prev["y"])*t,
                             "frame": f})
        out.append(curr)
    return out


# ===========================================================================
# Event detection
# ===========================================================================

def detect_goals(ball_positions, fw, fh, fps, use_real):
    if use_real:
        lx, rx   = 1.0, PITCH_W_M - 1.0
        ty, by_g = PITCH_H_M * 0.30, PITCH_H_M * 0.70
    else:
        lx, rx   = fw * 0.10, fw * 0.90
        ty, by_g = fh * 0.20, fh * 0.80

    def in_L(x, y): return x < lx  and ty < y < by_g
    def in_R(x, y): return x > rx  and ty < y < by_g

    sb = sorted(ball_positions, key=lambda b: b["frame"])
    events, last_gf, cl, cr = [], -999, 0, 0

    for bp in sb:
        bx, by, bf = bp["x"], bp["y"], bp["frame"]
        if in_L(bx, by):
            cl += 1; cr = 0
            if cl >= 2 and bf - last_gf > fps * 5:
                events.append({"minute": max(1,int(bf/fps/60)), "type":"Goal",
                                "team":"away", "frame":bf, "xG":0.90})
                last_gf = bf; cl = 0
        elif in_R(bx, by):
            cr += 1; cl = 0
            if cr >= 2 and bf - last_gf > fps * 5:
                events.append({"minute": max(1,int(bf/fps/60)), "type":"Goal",
                                "team":"home", "frame":bf, "xG":0.90})
                last_gf = bf; cr = 0
        else:
            cl = cr = 0

    tl = PITCH_W_M * 0.30 if use_real else fw * 0.30
    tr = PITCH_W_M * 0.70 if use_real else fw * 0.70
    for i in range(1, len(sb)):
        prev, curr = sb[i-1], sb[i]
        if curr["frame"] - prev["frame"] < 15:
            continue
        px, pf = prev["x"], prev["frame"]
        if px < tl and pf - last_gf > fps * 5 and i >= 2:
            if prev["x"] - sb[i-2]["x"] < -5:
                events.append({"minute":max(1,int(pf/fps/60)),"type":"Goal",
                                "team":"away","frame":pf,"xG":0.75})
                last_gf = pf
        elif px > tr and pf - last_gf > fps * 5 and i >= 2:
            if prev["x"] - sb[i-2]["x"] > 5:
                events.append({"minute":max(1,int(pf/fps/60)),"type":"Goal",
                                "team":"home","frame":pf,"xG":0.75})
                last_gf = pf

    events.sort(key=lambda e: e["frame"])
    deduped = []
    for e in events:
        if not deduped or e["frame"] - deduped[-1]["frame"] > fps * 5:
            deduped.append(e)
    return deduped


def detect_shots(ball_positions, fw, fps, use_real):
    """
    Phase 3: velocity-based xG.
    Real-world: 0.28 m/frame threshold ~ 8.4 m/s minimum shot speed at 30fps.
    xG penalised by distance to goal — shots from further out score lower.
    """
    shots, sb, last_sf = [], sorted(ball_positions, key=lambda b: b["frame"]), -999
    spd_thr = 0.28 if use_real else 12.0

    for i in range(1, len(sb)):
        prev, curr = sb[i-1], sb[i]
        if curr["frame"] - prev["frame"] > 3:
            continue
        dx    = curr["x"] - prev["x"]
        speed = math.hypot(dx, curr["y"] - prev["y"])
        if speed <= spd_thr or curr["frame"] - last_sf <= fps * 2:
            continue

        if use_real:
            dist = min(curr["x"], PITCH_W_M - curr["x"])
            xG   = round(min(0.90, speed * 1.5 / (1 + dist * 0.12)), 2)
            r = dx > 0 and curr["x"] > PITCH_W_M * 0.60
            l = dx < 0 and curr["x"] < PITCH_W_M * 0.40
        else:
            xG = round(min(0.85, speed / 40), 2)
            r  = dx > 0 and curr["x"] > fw * 0.65
            l  = dx < 0 and curr["x"] < fw * 0.35

        team = "home" if r else ("away" if l else None)
        if team:
            shots.append({"minute":max(1,int(curr["frame"]/fps/60)),"type":"Shot",
                           "team":team,"frame":curr["frame"],"xG":xG})
            last_sf = curr["frame"]
    return shots


def detect_passes(ball_positions, player_paths, team_assignments, fps, proximity):
    sb = sorted(ball_positions, key=lambda b: b["frame"])
    empty = {"completed":0,"attempted":0,"accuracy":0,
             "home":{"completed":0,"attempted":0},"away":{"completed":0,"attempted":0}}
    if len(sb) < 5:
        return empty

    fp: dict = defaultdict(list)
    for pid, positions in player_paths.items():
        team = team_assignments.get(pid, "home")
        for p in positions:
            fp[p["frame"]].append((p["x"], p["y"], pid, team))

    def nearest(bx, by, bf):
        bd, bp2, bt = float("inf"), None, None
        for px, py, pid, team in fp.get(bf, []):
            d = math.hypot(px-bx, py-by)
            if d < bd:
                bd, bp2, bt = d, pid, team
        return bp2, bt, bd

    hc = ha = ac = aa = 0
    last_pid = last_team = None
    for bp in sb:
        pid, team, dist = nearest(bp["x"], bp["y"], bp["frame"])
        if pid is None or dist > proximity:
            continue
        if last_pid is not None and pid != last_pid:
            if last_team == team:
                if team == "home": hc += 1; ha += 1
                else: ac += 1; aa += 1
            else:
                if last_team == "home": ha += 1
                else: aa += 1
        last_pid, last_team = pid, team

    tc, ta = hc+ac, ha+aa
    return {"completed":tc,"attempted":ta,
            "accuracy":round(tc/ta*100) if ta else 0,
            "home":{"completed":hc,"attempted":ha},
            "away":{"completed":ac,"attempted":aa}}


def detect_corners(ball_positions, fw, fh, fps, use_real):
    if use_real:
        cw, ch, W, H = 1.5, 2.5, PITCH_W_M, PITCH_H_M
    else:
        cw, ch, W, H = fw*0.08, fh*0.15, fw, fh
    zones = [(0,0,cw,ch),(W-cw,0,W,ch),(0,H-ch,cw,H),(W-cw,H-ch,W,H)]
    def in_c(x, y): return any(z[0]<=x<=z[2] and z[1]<=y<=z[3] for z in zones)
    sb = sorted(ball_positions, key=lambda b: b["frame"])
    total = consec = 0; last_cf = -999
    for bp in sb:
        if in_c(bp["x"], bp["y"]):
            consec += 1
            if consec >= 2 and bp["frame"] - last_cf > fps * 4:
                total += 1; last_cf = bp["frame"]; consec = 0
        else:
            consec = 0
    return total


# ===========================================================================
# Highlights reel generation
# ===========================================================================

def _draw_score_bug(frame: np.ndarray, home_goals: int, away_goals: int,
                    minute: int, fw: int, fh: int) -> np.ndarray:
    """
    Draws a score bug in the top-left corner:
      HOME  2 - 1  AWAY   12'
    Dark semi-transparent pill, white text.
    """
    f = frame.copy()
    text  = f"HOME  {home_goals} - {away_goals}  AWAY    {minute}'"
    scale = max(0.5, fw / 1920)
    thick = max(1, int(2 * scale))
    font  = cv2.FONT_HERSHEY_DUPLEX

    (tw, th), _ = cv2.getTextSize(text, font, scale, thick)
    pad  = int(12 * scale)
    x0, y0 = 20, 20
    x1, y1 = x0 + tw + pad * 2, y0 + th + pad * 2

    overlay = f.copy()
    cv2.rectangle(overlay, (x0, y0), (x1, y1), (10, 15, 25), -1)
    cv2.addWeighted(overlay, 0.75, f, 0.25, 0, f)
    cv2.putText(f, text, (x0 + pad, y1 - pad), font, scale, (230, 230, 230), thick)
    return f


def _draw_event_label(frame: np.ndarray, label: str, colour: tuple,
                      alpha: float, fw: int, fh: int) -> np.ndarray:
    """
    Draws a large centred event label (GOAL!, CHANCE!, NEAR MISS!) that fades out.
    alpha 1.0 = fully visible, 0.0 = invisible.
    """
    if alpha <= 0:
        return frame
    f = frame.copy()
    font   = cv2.FONT_HERSHEY_DUPLEX
    scale  = max(1.2, fw / 640)
    thick  = max(2, int(4 * fw / 1920))

    (tw, th), _ = cv2.getTextSize(label, font, scale, thick)
    x = (fw - tw) // 2
    y = fh - int(fh * 0.12)

    # Shadow
    cv2.putText(f, label, (x + 3, y + 3), font, scale, (0, 0, 0), thick + 2)
    # Coloured text
    cv2.putText(f, label, (x, y), font, scale, colour, thick)

    return cv2.addWeighted(f, alpha, frame, 1.0 - alpha, 0)


def _write_with_ffmpeg(raw_path: str, out_path: str, fps: float) -> bool:
    """
    Re-encode raw OpenCV output to H.264 via ffmpeg for browser compatibility.
    Returns True on success. Falls back gracefully if ffmpeg is unavailable.
    """
    import subprocess, shutil
    if not shutil.which("ffmpeg"):
        return False
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i",  raw_path,
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "23",
                "-pix_fmt", "yuv420p",   # broadest browser compatibility
                "-movflags", "+faststart",  # enables progressive streaming
                out_path,
            ],
            capture_output=True, timeout=300,
        )
        return result.returncode == 0
    except Exception:
        return False


def generate_highlights(
    video_path: str,
    events: list,
    fps: float,
    vid_stride: int,
    output_path: str,
    target_duration: float = 180.0,   # 3-minute cap
) -> int:
    """
    Generate a 2–3 minute highlights reel with:
      - GOAL! / CHANCE! / NEAR MISS! labels fading out at each event moment
      - Running score bug (top-left corner)
      - 15-frame fade-in / fade-out between clips
      - H.264 encoding via ffmpeg for browser-native playback (falls back to mp4v)

    Clip windows:
      Goal              : 15s before + 8s after
      High-xG shot ≥0.5 : 10s before + 5s after
      Near-miss 0.3–0.5 :  6s before + 3s after

    Returns number of clips assembled, 0 if no key events found.
    """
    # Classify and label events
    LABEL_MAP = {
        "Goal":      ("GOAL!",      (50, 220, 80)),
        "ChanceBig": ("CHANCE!",    (50, 165, 255)),
        "ChanceMed": ("NEAR MISS!", (150, 80, 255)),
    }

    def classify(e):
        if e["type"] == "Goal":
            return "Goal"
        xg = e.get("xG", 0)
        if xg >= 0.5:
            return "ChanceBig"
        if xg >= 0.3:
            return "ChanceMed"
        return None

    key_events = [e for e in events if classify(e)]
    if not key_events:
        return 0

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return 0

    total_src = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    fh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    # Try H.264 first (browser-native), fall back to mp4v
    raw_path = output_path.replace(".mp4", "_raw.mp4")
    fourcc   = cv2.VideoWriter_fourcc(*'avc1')
    out      = cv2.VideoWriter(raw_path, fourcc, fps, (fw, fh))
    if not out.isOpened():
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        out    = cv2.VideoWriter(raw_path, fourcc, fps, (fw, fh))

    FADE_FRAMES = min(15, int(fps * 0.5))

    # Running goal tally (for score bug) — build lookup by source frame
    home_score = away_score = 0
    goal_frames = sorted(
        [(e["frame"] * vid_stride, e["team"]) for e in events if e["type"] == "Goal"],
        key=lambda x: x[0]
    )

    def score_at(src_frame: int):
        h = a = 0
        for gf, gt in goal_frames:
            if gf <= src_frame:
                if gt == "home": h += 1
                else: a += 1
        return h, a

    # Build clip intervals
    intervals: list = []  # (start_src, end_src, event_src, event_kind)
    for e in sorted(key_events, key=lambda x: x["frame"]):
        src  = e["frame"] * vid_stride
        kind = classify(e)

        if kind == "Goal":
            before, after = CLIP_GOAL_BEFORE, CLIP_GOAL_AFTER
        elif kind == "ChanceBig":
            before, after = CLIP_CHANCE_BEFORE, CLIP_CHANCE_AFTER
        else:
            before, after = CLIP_NEAR_BEFORE, CLIP_NEAR_AFTER

        s  = max(0, int(src - before * fps))
        e2 = min(total_src, int(src + after * fps))

        if intervals and s < intervals[-1][1] + int(fps * 3):
            # Merge overlapping/close clips; keep latest event metadata
            intervals[-1] = (intervals[-1][0], max(intervals[-1][1], e2),
                             src, kind)
        else:
            intervals.append((s, e2, src, kind))

    # Enforce target duration — trim lowest-priority clips if over budget
    total_dur = sum((e2 - s) / fps for s, e2, _, __ in intervals)
    if total_dur > target_duration:
        # Drop near-miss clips first to fit within 3 minutes
        intervals = [(s, e2, es, ek) for s, e2, es, ek in intervals
                     if ek != "ChanceMed"]

    clip_count = 0
    for clip_idx, (start, end, event_src, event_kind) in enumerate(intervals):
        cap.set(cv2.CAP_PROP_POS_FRAMES, start)
        total_clip_frames = end - start
        label, colour = LABEL_MAP[event_kind]

        # Frames where the label should appear (centred around the event moment)
        label_start = max(start, event_src - int(fps * 1.0))
        label_end   = min(end,   event_src + int(fps * 3.0))
        LABEL_FADE_FRAMES = int(fps * 0.5)

        for fi in range(total_clip_frames):
            ret, frame = cap.read()
            if not ret:
                break

            cur_src  = start + fi
            minute   = max(1, int(cur_src / fps / 60))
            h, a     = score_at(cur_src)

            # Score bug
            frame = _draw_score_bug(frame, h, a, minute, fw, fh)

            # Event label with fade-in / fade-out
            if label_start <= cur_src <= label_end:
                dist_start = cur_src - label_start
                dist_end   = label_end - cur_src
                fade_in    = min(1.0, dist_start / LABEL_FADE_FRAMES)
                fade_out   = min(1.0, dist_end   / LABEL_FADE_FRAMES)
                label_alpha = min(fade_in, fade_out)
                frame = _draw_event_label(frame, label, colour, label_alpha, fw, fh)

            # Clip fade-in (first FADE_FRAMES frames)
            if fi < FADE_FRAMES:
                frame = (frame * (fi / FADE_FRAMES)).astype(np.uint8)
            # Clip fade-out (last FADE_FRAMES frames)
            elif fi > total_clip_frames - FADE_FRAMES:
                t = (total_clip_frames - fi) / FADE_FRAMES
                frame = (frame * t).astype(np.uint8)

            out.write(frame)

        clip_count += 1

    cap.release()
    out.release()

    if clip_count == 0:
        return 0

    # Re-encode to H.264 for browser playback via ffmpeg if available
    if _write_with_ffmpeg(raw_path, output_path, fps):
        os.remove(raw_path)
    else:
        # ffmpeg unavailable — rename raw as final output
        os.replace(raw_path, output_path)

    return clip_count


# ===========================================================================
# n8n webhook notification (non-blocking, non-fatal)
# ===========================================================================

def _notify_n8n(match_id: str, stats: dict, highlights_ready: bool) -> None:
    """
    POST a lightweight match summary to n8n when analysis completes.

    Set N8N_WEBHOOK_URL env var to your n8n webhook URL to enable.
    Leave unset to skip silently.

    Example n8n workflows this enables:
      - Send WhatsApp/email to coach when match is ready
      - Sync stats to Google Sheets for season-level aggregation
      - Post highlights link to Slack/Discord team channel
      - Trigger automated PDF report email delivery
    """
    import json
    import urllib.request

    webhook_url = os.environ.get("N8N_WEBHOOK_URL", "").strip()
    if not webhook_url:
        return

    payload = json.dumps({
        "matchId":          match_id,
        "score":            stats["score"],
        "possession":       stats["possession"],
        "xG":               stats["shots"]["xG"],
        "goals":            stats["score"]["home"] + stats["score"]["away"],
        "shots":            stats["shots"]["total"],
        "durationMinutes":  stats["meta"]["durationMinutes"],
        "highlightsReady":  highlights_ready,
        "dashboardUrl":     f"http://localhost:3000/dashboard/{match_id}",
        "reportUrl":        f"http://localhost:3000/report/{match_id}",
        "highlightsUrl":    f"http://localhost:8000/match/{match_id}/highlights" if highlights_ready else None,
    }).encode()

    try:
        req = urllib.request.Request(
            webhook_url, data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10):
            print(f"[{match_id[:8]}] n8n webhook notified.")
    except Exception as e:
        print(f"[{match_id[:8]}] n8n webhook failed (non-fatal): {e}")


# ===========================================================================
# Main CV pipeline (synchronous — called from thread pool)
# ===========================================================================

def _run_pipeline(match_id: str, video_path: str) -> None:

    def upd(progress: int, msg: str, status: str = "processing"):
        mock_firestore[match_id] = {"status": status, "progress": progress, "message": msg}
        print(f"[{match_id[:8]}] {progress}% — {msg}")

    upd(2, "Loading CV models...")

    # Models are loaded here (lazily), NOT at module import time
    try:
        track_model, detect_model = _get_models()
    except Exception as e:
        upd(0, f"Model load failed: {e}", "error")
        return

    tracker_yaml = _get_tracker_yaml()

    upd(4, "Opening video...")

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        upd(0, "Failed to open video.", "error"); return

    fps          = cap.get(cv2.CAP_PROP_FPS) or 30.0
    if math.isnan(fps): fps = 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 900
    frame_w      = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))  or 1920
    frame_h      = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 1080

    # Phase 2: compute homography on first frame
    upd(5, "Computing pitch homography (grass segmentation)...")
    ret, first   = cap.read()
    H_matrix     = compute_homography(first) if ret else None
    use_real     = H_matrix is not None
    field_mask   = compute_field_mask(first) if ret else np.ones((frame_h, frame_w), np.uint8)*255
    cap.release()

    coord_mode = "real-world metres" if use_real else "pixel mode"
    upd(7, f"{_MODEL_NAME} + {tracker_yaml} | {coord_mode} | field mask active")

    # --------------------------------------------------------------------------
    # Main tracking loop — BoT-SORT via ultralytics model.track()
    # --------------------------------------------------------------------------
    ball_kf        = BallKalmanFilter()
    player_paths   = defaultdict(list)
    player_colors  = defaultdict(list)
    ball_positions = []
    frame_count    = 0

    try:
        for result in track_model.track(
            source=video_path,
            tracker=tracker_yaml,
            classes=[0],           # persons only via tracker; ball handled below
            conf=0.20,
            imgsz=INFER_SIZE,
            vid_stride=VID_STRIDE, # process every Nth frame — doubles match coverage
            persist=True,
            verbose=False,
            stream=True,
        ):
            frame_count += 1
            frame = result.orig_img

            # Person tracking results
            boxes = result.boxes
            if boxes is not None and boxes.id is not None:
                for i in range(len(boxes)):
                    x1, y1, x2, y2 = [float(v) for v in boxes.xyxy[i]]
                    cx = (x1 + x2) / 2
                    cy = y1 + (y2 - y1) * 0.8   # foot position

                    if not in_field(cx, cy, field_mask):
                        continue

                    tid = int(boxes.id[i])
                    if use_real:
                        rx, ry = clamp_pitch(*to_pitch(cx, cy, H_matrix))
                    else:
                        rx, ry = cx, cy

                    player_paths[tid].append({"x": rx, "y": ry, "frame": frame_count})

                    if frame_count % 5 == 0:
                        col = extract_dominant_color(frame, int(x1), int(y1), int(x2), int(y2))
                        if col:
                            player_colors[tid].append(col)

            # Ball detection — high confidence first, then low-conf fallback
            ball_res = detect_model(frame, classes=[32], conf=0.20,
                                    imgsz=INFER_SIZE, verbose=False)[0]
            if len(ball_res.boxes) == 0:
                ball_res = detect_model(frame, classes=[32], conf=0.08,
                                        imgsz=INFER_SIZE, verbose=False)[0]

            if len(ball_res.boxes) > 0:
                best         = int(np.argmax(ball_res.boxes.conf.cpu().numpy()))
                bx1,by1,bx2,by2 = [float(v) for v in ball_res.boxes.xyxy[best]]
                kx, ky       = ball_kf.update((bx1+bx2)/2, (by1+by2)/2)
                if use_real:
                    rx, ry = clamp_pitch(*to_pitch(kx, ky, H_matrix))
                else:
                    rx, ry = kx, ky
                ball_positions.append({"x": rx, "y": ry, "frame": frame_count})
            else:
                pred = ball_kf.predict()
                if pred:
                    kx, ky = pred
                    if use_real:
                        rx, ry = clamp_pitch(*to_pitch(kx, ky, H_matrix))
                    else:
                        rx, ry = kx, ky
                    ball_positions.append({"x": rx, "y": ry, "frame": frame_count})

            if frame_count % 30 == 0:
                effective_total = max(1, total_frames // VID_STRIDE)
                prog = min(65, 7 + int(58 * frame_count / effective_total))
                elapsed_min = round(frame_count * VID_STRIDE / fps / 60, 1)
                total_min   = round(total_frames / fps / 60, 1)
                upd(prog, f"Tracking {elapsed_min}/{total_min} min of match...")

    except Exception as e:
        upd(0, f"Tracking error: {e}", "error")
        return

    # Ball interpolation
    upd(68, f"Interpolating ball ({len(ball_positions)} detections)...")
    ball_positions = interpolate_ball(ball_positions, max_gap=10)

    # Team classification
    upd(72, "Classifying teams by jersey colour (k-means HSV)...")
    team_assignments = classify_teams(player_colors)

    # Possession
    upd(75, "Computing possession...")
    proximity = 3.0 if use_real else 150.0
    possession = compute_possession(ball_positions, player_paths, team_assignments, proximity)

    # Events
    upd(80, "Detecting goals, shots, passes, corners...")
    fw = PITCH_W_M if use_real else frame_w
    fh = PITCH_H_M if use_real else frame_h

    goal_events  = detect_goals(ball_positions, fw, fh, fps, use_real)
    shot_events  = detect_shots(ball_positions, fw, fps, use_real)
    pass_stats   = detect_passes(ball_positions, player_paths, team_assignments, fps, proximity)
    corner_count = detect_corners(ball_positions, fw, fh, fps, use_real)

    # Phase 3: zone pressure
    upd(84, "Computing zone pressure...")
    zone_pressure = compute_zone_pressure(ball_positions, fw, fps)

    # Phase 3: player distances
    upd(86, "Computing player distances...")
    player_distances = compute_player_distances(player_paths, use_real, frame_w, frame_h)

    # Heatmaps
    upd(90, "Generating heatmaps...")
    home_pids = sorted([p for p,t in team_assignments.items() if t=="home"],
                       key=lambda k: len(player_paths[k]), reverse=True)[:5]
    away_pids = sorted([p for p,t in team_assignments.items() if t=="away"],
                       key=lambda k: len(player_paths[k]), reverse=True)[:5]

    def build_heatmap(pids, prefix):
        out = []
        for pid in pids:
            positions = []
            for pos in player_paths[pid]:
                if use_real:
                    rx = round(min(PITCH_W_M, max(0.0, pos["x"])), 2)
                    ry = round(min(PITCH_H_M, max(0.0, pos["y"])), 2)
                else:
                    rx = round(min(PITCH_W_M, max(0.0, pos["x"]/frame_w*PITCH_W_M)), 2)
                    ry = round(min(PITCH_H_M, max(0.0, pos["y"]/frame_h*PITCH_H_M)), 2)
                positions.append({"x": rx, "y": ry, "intensity": 0.8})
            out.append({
                "playerId": f"{prefix}_{pid}",
                "positions": positions,
                "distanceCovered": player_distances.get(pid, 0.0),
            })
        return out

    home_heatmap = build_heatmap(home_pids, "Home")
    away_heatmap = build_heatmap(away_pids, "Away")

    # Compile
    upd(95, "Compiling final statistics...")
    all_events       = sorted(goal_events + shot_events, key=lambda e: e["frame"])
    home_goals       = sum(1 for e in goal_events if e["team"] == "home")
    away_goals       = sum(1 for e in goal_events if e["team"] == "away")
    shots_home       = sum(1 for e in shot_events if e["team"] == "home")
    shots_away       = sum(1 for e in shot_events if e["team"] == "away")
    on_target        = len(goal_events) + max(0, (shots_home + shots_away) // 3)
    xg_home          = round(sum(e["xG"] for e in all_events if e.get("team")=="home"), 2)
    xg_away          = round(sum(e["xG"] for e in all_events if e.get("team")=="away"), 2)
    match_dur        = round(frame_count / fps / 60, 1) if fps else 0
    home_dist_total  = round(sum(player_distances.get(p, 0) for p in home_pids), 1)
    away_dist_total  = round(sum(player_distances.get(p, 0) for p in away_pids), 1)

    stats = {
        "score":      {"home": home_goals, "away": away_goals},
        "possession": possession,
        "passes": {
            "completed": pass_stats["completed"],
            "attempted":  pass_stats["attempted"],
            "accuracy":   pass_stats["accuracy"],
            "home": pass_stats["home"],
            "away": pass_stats["away"],
        },
        "shots": {
            "total":    shots_home + shots_away,
            "onTarget": on_target,
            "xG":       round(xg_home + xg_away, 2),
            "home":     {"total": shots_home, "xG": xg_home},
            "away":     {"total": shots_away, "xG": xg_away},
        },
        "fouls":   "—",
        "corners": corner_count,
        "events":  all_events,
        "heatmap": home_heatmap + away_heatmap,
        "teamHeatmap": {"home": home_heatmap, "away": away_heatmap},
        "zonePressure": zone_pressure,
        "playerDistances": {
            "home": {f"Home_{p}": player_distances.get(p,0) for p in home_pids},
            "away": {f"Away_{p}": player_distances.get(p,0) for p in away_pids},
            "homeTotal": home_dist_total,
            "awayTotal": away_dist_total,
        },
        "voronoi": [],
        "meta": {
            "framesProcessed":    frame_count,
            "fps":                round(fps, 1),
            "durationMinutes":    match_dur,
            "playersDetected":    len(player_paths),
            "homePlayersTracked": len(home_pids),
            "awayPlayersTracked": len(away_pids),
            "ballDetectedFrames": len(ball_positions),
            "ballDetectionRate":  f"{round(len(ball_positions)/max(1,frame_count)*100)}%",
            "resolution":         f"{frame_w}x{frame_h}",
            "model":              f"{_MODEL_NAME} + {tracker_yaml} + KalmanBall",
            "homographyActive":   use_real,
            "coordinateSpace":    "real_world_metres" if use_real else "pixel",
        },
    }

    # Highlights reel — extract goal + chance clips from source video
    upd(97, "Generating highlights reel...")
    highlights_path  = video_path.replace(".mp4", "_highlights.mp4")
    highlights_clips = 0
    try:
        highlights_clips = generate_highlights(
            video_path=video_path,
            events=all_events,
            fps=fps,
            vid_stride=VID_STRIDE,
            output_path=highlights_path,
        )
        print(f"[{match_id[:8]}] Highlights: {highlights_clips} clips → {highlights_path}")
    except Exception as e:
        print(f"[{match_id[:8]}] Highlights generation failed (non-fatal): {e}")

    highlights_ready = highlights_clips > 0 and os.path.exists(highlights_path)

    mock_firestore[match_id] = {
        "status":          "completed",
        "progress":        100,
        "message":         "Analysis complete.",
        "stats":           stats,
        "highlightsReady": highlights_ready,
        "highlightsClips": highlights_clips,
        "videoPath":       video_path,
        "highlightsPath":  highlights_path if highlights_ready else None,
    }
    print(f"[{match_id[:8]}] Pipeline complete. Highlights: {'yes' if highlights_ready else 'no'}")

    # n8n workflow trigger — notifies coach, syncs to sheets, etc.
    _notify_n8n(match_id, stats, highlights_ready)


# ===========================================================================
# FastAPI background task entry point
# ===========================================================================

async def process_video(match_id: str, video_path: str) -> None:
    """Runs the synchronous CV pipeline in a thread pool — keeps FastAPI responsive."""
    mock_firestore[match_id] = {
        "status": "processing", "progress": 1, "message": "Starting CV pipeline..."
    }
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, _run_pipeline, match_id, video_path)
    except Exception as exc:
        mock_firestore[match_id] = {
            "status": "error", "progress": 0,
            "message": f"Pipeline error: {exc}",
        }
