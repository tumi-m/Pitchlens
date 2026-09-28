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
        if not isinstance(p, dict) or not isinstance(p.get("name"), str) or p["name"] not in names:
            raise ValueError("Unknown landmark")
        if p["name"] in used:
            raise ValueError("Each landmark can be used once")
        try:
            x, y = float(p.get("x")), float(p.get("y"))
        except (TypeError, ValueError, OverflowError) as exc:
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
        if not isinstance(c, dict) or not isinstance(c.get("line"), str) or c["line"] not in lines:
            raise ValueError("Unknown line")
        try:
            x, y = float(c.get("x")), float(c.get("y"))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Line points must be numbers") from exc
        if not (math.isfinite(x) and math.isfinite(y) and -w <= x <= 2 * w and -h <= y <= 2 * h):
            raise ValueError("Line point is outside the video")
        line_clicks.append(((x, y), lines[c["line"]]))
    try:
        t = float(request.get("t", 0))
    except (TypeError, ValueError, OverflowError) as exc:
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
    # Points behind the camera come back NaN: send null (JSON has no NaN) so the
    # browser splits the line there.
    lines = [
        [[round(float(x), 1), round(float(y), 1)] if math.isfinite(x) and math.isfinite(y) else None for x, y in pitchlib.pitch_to_image(cal, line)]
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

    The engine records each sample's source frame number; the video is read the
    same way (seek to the analysis start, count decoded frames), so masks come
    from exactly the analysed frames even on variable-frame-rate phone video.
    Older results without frame numbers are matched by time: the frame-count
    clock when t is exactly one, otherwise the decoder's timestamp.
    """
    wanted = sorted(set(indices))
    if not wanted:
        return {}
    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    half = 0.5 / fps
    by_number, by_time = {}, []
    for i in wanted:
        number = frames[i].get("frame")
        if not (isinstance(number, int) and not isinstance(number, bool) and number >= 0):
            t = frames[i]["t"]
            n = int(round(t * fps))
            # The engine stored frame/fps when it distrusted the decoder clock.
            number = n if abs(round(n / fps, 3) - t) < 0.0015 else None
        if number is None:
            by_time.append(i)
        else:
            by_number.setdefault(number, i)
    by_time.sort(key=lambda i: frames[i]["t"])
    out = {}

    def keep(i):
        ok, image = cap.retrieve()
        if ok:
            ok, png = cv2.imencode(".png", pitchlib.line_mask(image, restrict_to_grass=False))
            if ok:
                out[i] = png.tobytes()
        if progress and len(out) % 50 == 0:
            progress(len(out) / len(wanted))

    try:
        n = 0
        start = frames[0].get("frame")
        if not by_time and isinstance(start, int) and not isinstance(start, bool) and 0 < start <= min(by_number):
            # Same seek as the engine, so frame counting starts at the same place.
            cap.set(cv2.CAP_PROP_POS_FRAMES, start)
            n = start
        last = max(by_number) if by_number else -1
        k = 0
        while n <= last or k < len(by_time):
            if not cap.grab():
                break
            if n in by_number:
                keep(by_number[n])
            if k < len(by_time):
                position = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
                # Skip targets we have passed without a close enough frame.
                while k < len(by_time) and frames[by_time[k]]["t"] < position - half:
                    k += 1
                if k < len(by_time) and abs(frames[by_time[k]]["t"] - position) <= half:
                    keep(by_time[k])
                    k += 1
            n += 1
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
    # The clicks must be on an analysed frame itself (to within one video frame):
    # a moving camera is somewhere else a fraction of a second later.
    native = float((result.get("video") or {}).get("fps") or 0)
    tolerance = min(0.25 / fps, 0.5 / native + 0.005) if native > 0 else 0.25 / fps
    if not static and abs(frames[anchor]["t"] - t) > tolerance:
        raise ValueError(
            "The camera moves, and the clicked moment is between analysed frames. Use the frame buttons in the pitch setup to pick an analysed frame."
        )
    anchors = {anchor: np.asarray(cal["H"], float)}
    aligned = 0
    reacquired = 0
    if static and video_path and Path(video_path).is_file():
        # A fixed camera keeps one view, except where it may have been re-aimed
        # (motion lost with the tracks broken) or the recording cut: find the
        # painted lines again there, or leave that stretch unmapped.
        breaks = pitchlib.static_breaks(frames)
        own = max(i for i in breaks if i <= anchor)  # the clicked frame covers its own stretch
        breaks = [i for i in breaks if i != own]
        masks = line_masks(video_path, frames, breaks, progress=(lambda p: progress(p * 0.8)) if progress else None)
        for i in sorted(masks):
            refined, score = _align(masks[i], cal, anchors[anchor], template, int(min(size) * 0.3))
            if refined is not None and score >= 0.6:
                anchors[i] = refined
                reacquired += 1
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


# ------------------------------------------------------------------ venues
# A fixed venue camera (PUSHIT-style) sees the same pitch every match: save its
# calibration once and reuse it, checked against the painted lines each time.


def venue_from_calibration(calibration, name):
    frames = calibration.get("frames") or []
    anchor = calibration.get("anchorFrame")
    entry = frames[anchor] if anchor is not None and anchor < len(frames) else None
    if not entry:
        raise ValueError("This match has no usable pitch setup to save")
    return {
        "name": name,
        "template": calibration["template"],
        "size": calibration["size"],
        "k1": calibration.get("k1", 0.0),
        "H": entry["H"],
        "static": bool(calibration.get("static")),
        "rms": calibration.get("rms"),
    }


def build_from_venue(result, venue, video_path=None, progress=None, samples=12):
    """Apply a saved venue calibration to a new match, verified against its lines.

    Refuses when the video size differs, or when the painted lines in this
    match do not line up with the saved setup (the camera moved or it is a
    different court).
    """
    frames = result["frames"]
    size = (result["video"]["width"], result["video"]["height"])
    if list(size) != list(venue["size"]):
        raise ValueError(
            f"This video is {size[0]}x{size[1]} but the saved venue was set up on {venue['size'][0]}x{venue['size'][1]}. Set up the pitch for this match."
        )
    template = venue["template"]
    H = np.asarray(venue["H"], float).reshape(3, 3)
    cal = {"H": H, "k1": venue.get("k1", 0.0), "size": list(size)}
    static = pitchlib.static_camera(frames)
    # A fixed camera may still have been re-aimed where motion was lost, or the
    # recording cut: each stretch between such breaks is checked on its own.
    breaks = pitchlib.static_breaks(frames) if static else []
    verified = None
    anchors = {}
    has_video = bool(video_path and Path(video_path).is_file())
    if has_video:
        picks = sorted(set(np.linspace(0, len(frames) - 1, min(samples, len(frames))).astype(int).tolist()))
        masks = line_masks(video_path, frames, sorted(set(picks) | set(breaks)), progress=(lambda p: progress(p * 0.8)) if progress else None)
        scores = []
        for i, png in masks.items():
            refined, score = _align(png, cal, H, template, search=int(min(size) * 0.05))
            if i in picks:
                scores.append(score)
            if refined is not None and score >= 0.45:
                anchors[i] = refined
        verified = round(float(np.median(scores)), 2) if scores else None
        if not scores or sum(1 for i in anchors if i in picks) < max(2, len(scores) // 2):
            raise ValueError(
                "The painted lines in this match do not line up with the saved venue (the camera may have moved). Set up the pitch for this match."
            )
    if not anchors and not static:
        raise ValueError(
            "This match's footage is no longer on the server, so the saved venue cannot be checked against the "
            "painted lines, and the camera moves. Set up the pitch for this match instead."
        )
    if static:
        # One fixed view per stretch: the median of its verified corrections, or
        # the saved homography for the first stretch when there is no footage to
        # check. A stretch nothing verifies stays unmapped.
        homographies, distance = [None] * len(frames), [None] * len(frames)
        fixed = {}
        edges = breaks + [len(frames)]
        for first, end in zip(edges, edges[1:]):
            inside = [anchors[i] for i in anchors if first <= i < end]
            if inside:
                fixed[first] = _median_homography(inside, size) if len(inside) > 1 else inside[0]
            elif not has_video and first == 0:
                fixed[first] = H
            if first in fixed:
                for i in range(first, end):
                    homographies[i], distance[i] = fixed[first], i - first
        anchors = fixed
    else:
        homographies, distance = pitchlib.propagate(frames, anchors, static)
    per_frame = [
        {"H": np.asarray(h, float).round(9).reshape(-1).tolist(), "d": d} if h is not None else None
        for h, d in zip(homographies, distance)
    ]
    covered = sum(1 for f in per_frame if f)
    return {
        "schemaVersion": 1,
        "state": "ready",
        "template": template,
        "request": {"venue": venue.get("id"), "template": template},
        "fit": {"quality": "venue", "rms": venue.get("rms"), "rmsPixels": None, "warnings": [], "residuals": []},
        "k1": venue.get("k1", 0.0),
        "size": list(size),
        "rms": venue.get("rms") or 0.3,
        "static": static,
        "anchorFrame": 0,
        "anchors": len(anchors),
        "lineAligned": len(anchors),
        "reacquired": 0,
        "coverage": round(covered / max(1, len(frames)) * 100, 1),
        "venue": {"id": venue.get("id"), "name": venue.get("name"), "lineScore": verified, "verified": verified is not None},
        "frames": per_frame,
    }


def _median_homography(homographies, size):
    """Robust average of nearby homographies: median of where they send a grid."""
    w, h = size
    grid = np.array([[x, y] for x in np.linspace(0, w, 5) for y in np.linspace(h * 0.3, h, 4)])
    mapped = np.median(np.stack([pitchlib.apply(H, grid) for H in homographies]), axis=0)
    H, _ = cv2.findHomography(grid, mapped, 0)
    return H / H[2, 2]
