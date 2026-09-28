"""Build a per-frame pitch calibration for an analysed match.

Input: the analysis result (frames with recorded camera motion), the landmarks
a person clicked on one frame, and (optionally) the video for re-aligning to
the painted lines. Output: calibration.json with one image->pitch homography
per sampled frame, or null where the pitch position is unknown.
"""

import math
from pathlib import Path

import cv2
import numpy as np

from app.vision import pitch as pitchlib


def validate_request(request, size):
    if not isinstance(request, dict):
        raise ValueError("Calibration must be a JSON object")
    template = pitchlib.normalise_template(request.get("template") or "five-a-side")
    names = pitchlib.landmarks(template)
    points = request.get("points")
    if not isinstance(points, list) or not 4 <= len(points) <= 30:
        raise ValueError("Click between 4 and 30 landmarks")
    image, world, used = [], [], []
    w, h = size
    for p in points:
        if not isinstance(p, dict) or p.get("name") not in names:
            raise ValueError("Unknown landmark")
        if p["name"] in used:
            raise ValueError("Each landmark can be used once")
        try:
            x, y = float(p.get("x")), float(p.get("y"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Landmark positions must be numbers") from exc
        if not (math.isfinite(x) and math.isfinite(y) and -w <= x <= 2 * w and -h <= y <= 2 * h):
            raise ValueError("Landmark position is outside the video")
        used.append(p["name"])
        image.append((x, y))
        world.append(names[p["name"]])
    lines = pitchlib.straight_lines(template)
    line_clicks = []
    raw_lines = request.get("lines") or []
    if not isinstance(raw_lines, list) or len(raw_lines) > 80:
        raise ValueError("Click at most 80 points along lines")
    for c in raw_lines:
        if not isinstance(c, dict) or c.get("line") not in lines:
            raise ValueError("Unknown line")
        try:
            x, y = float(c.get("x")), float(c.get("y"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Line points must be numbers") from exc
        if not (math.isfinite(x) and math.isfinite(y) and -w <= x <= 2 * w and -h <= y <= 2 * h):
            raise ValueError("Line point is outside the video")
        line_clicks.append(((x, y), lines[c["line"]]))
    try:
        t = float(request.get("t", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid frame time") from exc
    if not math.isfinite(t) or t < 0:
        raise ValueError("Invalid frame time")
    distortion = request.get("distortion", "auto")
    if distortion not in ("auto", "none", "on"):
        raise ValueError("Unknown distortion mode")
    walls = bool(request.get("walls", False))
    template["walls"] = walls
    clean = {
        "template": template,
        "points": [{"name": n, "x": float(x), "y": float(y)} for n, (x, y) in zip(used, image)],
        "lines": [{"line": c.get("line"), "x": float(c.get("x")), "y": float(c.get("y"))} for c in raw_lines],
        "t": t,
        "distortion": distortion,
        "walls": walls,
    }
    return template, np.array(image), np.array(world), t, distortion, used, line_clicks, clean


def preview(result, request):
    size = (result["video"]["width"], result["video"]["height"])
    template, image, world, t, distortion, used, line_clicks, _ = validate_request(request, size)
    cal = pitchlib.fit(image, world, size, distortion, line_clicks)
    lines = [
        [[round(float(x), 1), round(float(y), 1)] for x, y in pitchlib.pitch_to_image(cal, line)]
        for line in pitchlib.line_segments(template, step=0.5)
    ]
    return {"fit": _public_fit(cal, used), "template": template, "lines": lines}


def _public_fit(cal, used):
    return {
        "H": np.asarray(cal["H"], float).round(9).tolist(),
        "k1": round(float(cal["k1"]), 5),
        "size": cal["size"],
        "rms": cal["rms"],
        "rmsPixels": cal["rmsPixels"],
        "quality": cal["quality"],
        "warnings": cal["warnings"],
        "fieldOfView": cal["fieldOfView"],
        "cameraHeight": cal["cameraHeight"],
        "lineResiduals": cal["lineResiduals"],
        "residuals": [
            {"name": n, "metres": m, "pixels": p, "leftOut": o}
            for n, m, p, o in zip(used, cal["residuals"], cal["pixelResiduals"], cal["leaveOneOut"])
        ],
    }


def nearest_frame(frames, t):
    return min(range(len(frames)), key=lambda i: abs(frames[i]["t"] - t))


def line_masks(video_path, frames, indices, progress=None):
    """PNG-compressed line masks for the chosen sampled frames (memory-light).

    Frames are matched by the decoder's timestamp (the same clock the engine
    used for t), so variable-frame-rate phone video lines up correctly.
    """
    wanted = sorted(set(indices), key=lambda i: frames[i]["t"])
    if not wanted:
        return {}
    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    half = 0.5 / fps
    out = {}
    k = 0
    try:
        while k < len(wanted):
            if not cap.grab():
                break
            position = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
            # Skip targets we have passed without a close enough frame.
            while k < len(wanted) and frames[wanted[k]]["t"] < position - half:
                k += 1
            if k < len(wanted) and abs(frames[wanted[k]]["t"] - position) <= half:
                ok, image = cap.retrieve()
                if ok:
                    ok, png = cv2.imencode(".png", pitchlib.line_mask(image, restrict_to_grass=False))
                    if ok:
                        out[wanted[k]] = png.tobytes()
                k += 1
                if progress and len(out) % 50 == 0:
                    progress(len(out) / len(wanted))
    finally:
        cap.release()
    return out


def _align(mask_png, calibration, H, template, search=0):
    mask = cv2.imdecode(np.frombuffer(mask_png, np.uint8), cv2.IMREAD_GRAYSCALE)
    if mask is None:
        return None, 0.0
    # align_to_lines computes its own mask from a frame; give it the stored one.
    frame = np.dstack([mask, mask, mask])
    return pitchlib.align_to_lines(frame, calibration, H, template, precomputed=mask, search=search)


def build(result, request, video_path=None, progress=None, stride_seconds=1.0):
    """Fit, optionally re-align through the video, and return calibration.json."""
    frames = result["frames"]
    size = (result["video"]["width"], result["video"]["height"])
    template, image, world, t, distortion, used, line_clicks, clean = validate_request(request, size)
    cal = pitchlib.fit(image, world, size, distortion, line_clicks)
    if cal["quality"] == "poor":
        raise ValueError(
            "These clicks do not fit a flat pitch (error "
            f"{cal['rmsPixels']:.1f} px). Check each landmark is the right one and the pitch size."
        )
    anchor = nearest_frame(frames, t)
    static = pitchlib.static_camera(frames)
    fps = result.get("sampleFps") or 5
    if not static and abs(frames[anchor]["t"] - t) > 0.75 / fps:
        raise ValueError(
            "The camera moves, and the clicked moment is between analysed frames. Use the frame buttons in the pitch setup to pick an analysed frame."
        )
    anchors = {anchor: np.asarray(cal["H"], float)}
    aligned = 0
    reacquired = 0
    if not static and video_path and Path(video_path).is_file():
        fps = result.get("sampleFps") or 5
        stride = max(1, int(round(stride_seconds * fps)))
        scene_starts = [i for i in range(len(frames)) if i == 0 or frames[i]["scene"] != frames[i - 1]["scene"]]
        # Frames where motion estimation failed break the chain: align there too.
        motion_gaps = [i for i in range(1, len(frames)) if frames[i].get("camera") is None]
        indices = list(range(0, len(frames), stride)) + scene_starts + motion_gaps
        # Re-acquisition (a wide search) is expensive: only at shot starts,
        # motion failures and every ~5 s of uncovered footage.
        reacquire_at = set(scene_starts) | set(motion_gaps) | set(range(0, len(frames), stride * 5))
        masks = line_masks(video_path, frames, indices, progress=(lambda p: progress(p * 0.6)) if progress else None)
        for round_ in range(4):
            new = 0
            homographies, _ = pitchlib.propagate(frames, anchors, static)
            for i in sorted(masks):
                if i in anchors:
                    continue
                start = homographies[i]
                strict = False
                if start is None and i not in reacquire_at:
                    continue
                if start is None:
                    # Motion estimation failed (fast pan, blur) or a new shot began
                    # (cut, replay). The same camera usually shows a similar view:
                    # start from the nearest known calibration, preferring this
                    # shot, and accept only a clearly good line fit.
                    same = [k for k in anchors if frames[k]["scene"] == frames[i]["scene"]]
                    known = same or list(anchors)
                    if not known:
                        continue
                    start = anchors[min(known, key=lambda k: abs(k - i))]
                    strict = True
                # Re-acquiring: the camera moved while unobserved, search wider.
                search = int(min(size) * 0.3) if strict else 0
                refined, score = _align(masks[i], cal, start, template, search)
                if refined is not None and score >= (0.6 if strict else 0.45):
                    anchors[i] = refined
                    new += 1
                    aligned += 0 if strict else 1
                    reacquired += 1 if strict else 0
            if progress:
                progress(0.6 + 0.1 * (round_ + 1))
            if not new:
                break
    homographies, distance = pitchlib.propagate(frames, anchors, static)
    per_frame = [
        {"H": np.asarray(H, float).round(9).reshape(-1).tolist(), "d": d} if H is not None else None
        for H, d in zip(homographies, distance)
    ]
    covered = sum(1 for f in per_frame if f)
    return {
        "schemaVersion": 1,
        "state": "ready",
        "template": template,
        "request": clean,
        "fit": _public_fit(cal, used),
        "k1": cal["k1"],
        "size": list(size),
        "rms": cal["rms"],
        "static": static,
        "anchorFrame": anchor,
        "anchors": len(anchors),
        "lineAligned": aligned,
        "reacquired": reacquired,
        "coverage": round(covered / max(1, len(frames)) * 100, 1),
        "frames": per_frame,
    }
