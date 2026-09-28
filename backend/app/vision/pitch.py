"""Pitch calibration: image pixels <-> pitch metres.

Coordinates on the pitch are metres with the origin at the far-left corner as
seen from the camera: x runs along the length (0 = left goal line, L = right
goal line), y across the width (0 = far touchline, W = near touchline).

A calibration is a planar homography from undistorted image pixels to pitch
metres, optionally with one radial distortion coefficient (division model,
which suits wide-angle venue cameras). It is fitted from landmarks a person
clicks on one frame, then carried to other frames through the per-frame camera
motion the engine records, and re-aligned to the painted lines where they are
visible. Only positions ON the ground plane are meaningful: feet, and the ball
when it is on the ground. An airborne ball projects to the wrong place.
"""

import math

import cv2
import numpy as np

# Pitch presets. Dimensions are typical, not official for every venue: the
# person calibrating can edit them, and should when the venue publishes them.
PRESETS = {
    "five-a-side": {
        "label": "5-a-side (cage or small pitch)",
        "length": 36.0,
        "width": 24.0,
        "goalWidth": 3.66,
        "centreRadius": None,
        "areaRadius": 6.0,
        "areaDepth": None,
        "areaWidth": None,
        "penaltySpot": None,
    },
    "futsal": {
        "label": "Futsal / indoor court",
        "length": 40.0,
        "width": 20.0,
        "goalWidth": 3.0,
        "centreRadius": 3.0,
        "areaRadius": 6.0,
        "areaDepth": None,
        "areaWidth": None,
        "penaltySpot": 6.0,
    },
    "seven-a-side": {
        "label": "7-a-side",
        "length": 55.0,
        "width": 37.0,
        "goalWidth": 3.66,
        "centreRadius": 5.5,
        "areaRadius": None,
        "areaDepth": 9.0,
        "areaWidth": 18.0,
        "penaltySpot": 8.0,
    },
    "eleven-a-side": {
        "label": "11-a-side",
        "length": 105.0,
        "width": 68.0,
        "goalWidth": 7.32,
        "centreRadius": 9.15,
        "areaRadius": None,
        "areaDepth": 16.5,
        "areaWidth": 40.32,
        "penaltySpot": 11.0,
    },
}


def normalise_template(spec):
    """Validated pitch dimensions from a preset name or a dict (metres)."""
    if isinstance(spec, str):
        spec = PRESETS.get(spec)
        if spec is None:
            raise ValueError("Unknown pitch preset")
    if not isinstance(spec, dict):
        raise ValueError("Pitch dimensions must be an object or a preset name")
    base = dict(PRESETS["five-a-side"])
    base.update({k: v for k, v in spec.items() if k in base})
    try:
        length, width = float(base["length"]), float(base["width"])
    except (TypeError, ValueError) as exc:
        raise ValueError("Pitch length and width must be numbers") from exc
    if not (10 <= length <= 130 and 5 <= width <= 100 and length >= width * 0.8):
        raise ValueError("Pitch dimensions must be 10-130 m long and 5-100 m wide")
    try:
        goal = float(base["goalWidth"] or 3.0)
    except (TypeError, ValueError) as exc:
        raise ValueError("Goal width must be a number") from exc
    if not 1 <= goal <= min(8, width):
        raise ValueError("Goal width must be between 1 m and 8 m")
    out = {"length": length, "width": width, "goalWidth": goal}
    limits = {"centreRadius": width / 2, "areaRadius": width / 2, "areaDepth": length / 2, "areaWidth": width, "penaltySpot": length / 2}
    for key, limit in limits.items():
        value = base.get(key)
        if value in (None, "", 0):
            out[key] = None
            continue
        try:
            value = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} must be a number") from exc
        if not (0 < value <= limit):
            raise ValueError(f"{key} must be between 0 and {limit:.1f} m for this pitch")
        out[key] = value
    if out["areaDepth"] and not out["areaWidth"]:
        out["areaDepth"] = None
    return out


def landmarks(t):
    """Named points a person can click, in pitch metres."""
    L, W, g = t["length"], t["width"], t["goalWidth"]
    points = {
        "corner-far-left": (0.0, 0.0),
        "corner-far-right": (L, 0.0),
        "corner-near-right": (L, W),
        "corner-near-left": (0.0, W),
        "halfway-far": (L / 2, 0.0),
        "halfway-near": (L / 2, W),
        "centre-spot": (L / 2, W / 2),
        "left-goal-far-post": (0.0, W / 2 - g / 2),
        "left-goal-near-post": (0.0, W / 2 + g / 2),
        "right-goal-far-post": (L, W / 2 - g / 2),
        "right-goal-near-post": (L, W / 2 + g / 2),
    }
    if t.get("centreRadius"):
        r = t["centreRadius"]
        points["centre-circle-far"] = (L / 2, W / 2 - r)
        points["centre-circle-near"] = (L / 2, W / 2 + r)
    if t.get("penaltySpot"):
        points["left-penalty-spot"] = (t["penaltySpot"], W / 2)
        points["right-penalty-spot"] = (L - t["penaltySpot"], W / 2)
    if t.get("areaDepth") and t.get("areaWidth"):
        d, b = t["areaDepth"], t["areaWidth"]
        points["left-area-far"] = (d, W / 2 - b / 2)
        points["left-area-near"] = (d, W / 2 + b / 2)
        points["right-area-far"] = (L - d, W / 2 - b / 2)
        points["right-area-near"] = (L - d, W / 2 + b / 2)
        # Where the box's sides meet the goal line.
        points["left-area-far-goalline"] = (0.0, W / 2 - b / 2)
        points["left-area-near-goalline"] = (0.0, W / 2 + b / 2)
        points["right-area-far-goalline"] = (L, W / 2 - b / 2)
        points["right-area-near-goalline"] = (L, W / 2 + b / 2)
    return points


def line_segments(t, step=0.25):
    """Painted lines as sampled polylines (metres), for drawing and alignment."""
    L, W = t["length"], t["width"]

    def segment(a, b):
        n = max(2, int(math.hypot(b[0] - a[0], b[1] - a[1]) / step) + 1)
        return [(a[0] + (b[0] - a[0]) * s, a[1] + (b[1] - a[1]) * s) for s in np.linspace(0, 1, n)]

    def arc(cx, cy, r, start, end):
        n = max(8, int(abs(end - start) * r / step) + 1)
        return [(cx + r * math.cos(a), cy + r * math.sin(a)) for a in np.linspace(start, end, n)]

    lines = [
        segment((0, 0), (L, 0)),
        segment((L, 0), (L, W)),
        segment((L, W), (0, W)),
        segment((0, W), (0, 0)),
        segment((L / 2, 0), (L / 2, W)),
    ]
    if t.get("centreRadius"):
        lines.append(arc(L / 2, W / 2, t["centreRadius"], 0, 2 * math.pi))
    if t.get("areaDepth") and t.get("areaWidth"):
        d, b = t["areaDepth"], t["areaWidth"]
        for x0, x1 in ((0, d), (L, L - d)):
            lines.append(segment((x0, W / 2 - b / 2), (x1, W / 2 - b / 2)))
            lines.append(segment((x1, W / 2 - b / 2), (x1, W / 2 + b / 2)))
            lines.append(segment((x1, W / 2 + b / 2), (x0, W / 2 + b / 2)))
    elif t.get("areaRadius"):
        r = min(t["areaRadius"], W / 2)
        lines.append(arc(0, W / 2, r, -math.pi / 2, math.pi / 2))
        lines.append(arc(L, W / 2, r, math.pi / 2, 3 * math.pi / 2))
    return lines


# ---------------------------------------------------------------- distortion
# Division model: undistorted = distorted / (1 + k1 * r^2), with r measured from
# the image centre in units of half the image diagonal. k1 < 0 for barrel
# (wide-angle) distortion. One parameter is enough for venue cameras and stays
# well-conditioned with a handful of clicks.


def _norm(size):
    w, h = size
    return np.array([w / 2, h / 2]), math.hypot(w, h) / 2


def undistort(points, k1, size):
    points = np.asarray(points, float).reshape(-1, 2)
    if not k1:
        return points
    centre, scale = _norm(size)
    p = (points - centre) / scale
    r2 = (p**2).sum(axis=1, keepdims=True)
    return p / (1 + k1 * r2) * scale + centre


def distort(points, k1, size):
    """Inverse of undistort (closed form for the division model)."""
    points = np.asarray(points, float).reshape(-1, 2)
    if not k1:
        return points
    centre, scale = _norm(size)
    u = (points - centre) / scale
    ru = np.linalg.norm(u, axis=1, keepdims=True)
    # rd / (1 + k1 rd^2) = ru  ->  k1 ru rd^2 - rd + ru = 0
    if k1 > 0:
        # Beyond this radius the division model has no inverse; clamp rather than fold.
        ru = np.minimum(ru, 0.999 / (2 * math.sqrt(k1)))
    with np.errstate(divide="ignore", invalid="ignore"):
        disc = 1 - 4 * k1 * ru**2
        rd = np.where(
            np.abs(k1 * ru) < 1e-12,
            ru,
            (1 - np.sqrt(np.clip(disc, 0, None))) / (2 * k1 * np.where(ru == 0, 1, ru)),
        )
        factor = np.where(ru > 0, rd / np.where(ru == 0, 1, ru), 1.0)
    return u * factor * scale + centre


# ---------------------------------------------------------------- homography


def apply(H, points):
    points = np.asarray(points, float).reshape(-1, 2)
    homog = np.hstack([points, np.ones((len(points), 1))]) @ np.asarray(H, float).T
    with np.errstate(divide="ignore", invalid="ignore"):
        return homog[:, :2] / homog[:, 2:3]


def _dlt(src, dst):
    H, _ = cv2.findHomography(np.asarray(src, np.float64), np.asarray(dst, np.float64), 0)
    if H is None:
        raise ValueError("These points do not define a pitch plane. Pick points that are spread out.")
    return H / H[2, 2]


def _spread(pitch_points):
    """Area of the convex hull of the pitch points (m^2): collinear clicks fail."""
    hull = cv2.convexHull(np.asarray(pitch_points, np.float32))
    return float(cv2.contourArea(hull))


def _general_position(pitch_points, min_area=1.0):
    """True if some four points have no three on a line (a homography needs that)."""
    from itertools import combinations

    pts = np.asarray(pitch_points, float)

    def area(a, b, c):
        return abs((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])) / 2

    for quad in combinations(range(len(pts)), 4):
        if all(area(*pts[list(tri)]) > min_area for tri in combinations(quad, 3)):
            return True
    return False


def straight_lines(t):
    """Straight painted lines a person can click points along (pitch metres)."""
    L, W = t["length"], t["width"]
    lines = {
        "far-touchline": ((0.0, 0.0), (L, 0.0)),
        "near-touchline": ((0.0, W), (L, W)),
        "left-goal-line": ((0.0, 0.0), (0.0, W)),
        "right-goal-line": ((L, 0.0), (L, W)),
        "halfway-line": ((L / 2, 0.0), (L / 2, W)),
    }
    if t.get("areaDepth") and t.get("areaWidth"):
        d, b = t["areaDepth"], t["areaWidth"]
        lines["left-box-front"] = ((d, W / 2 - b / 2), (d, W / 2 + b / 2))
        lines["right-box-front"] = ((L - d, W / 2 - b / 2), (L - d, W / 2 + b / 2))
    return lines


def _residuals(G, k1, size, image_points, pitch_points, line_clicks):
    """Image-pixel residuals: point clicks, and distance of line clicks to their line.

    G maps pitch metres to undistorted image pixels. Point residuals are
    measured in the observed (distorted) image, which is the maximum-likelihood
    criterion for click noise; line residuals are measured in the undistorted
    image, where the painted line is straight.
    """
    out = []
    if len(image_points):
        projected = distort(apply(G, pitch_points), k1, size)
        out.append((projected - image_points).ravel())
    if line_clicks:
        clicks = undistort(np.array([c[0] for c in line_clicks]), k1, size)
        for (a, b), click in zip([c[1] for c in line_clicks], clicks):
            pa, pb = apply(G, [a, b])
            direction = pb - pa
            norm = np.linalg.norm(direction)
            if not np.isfinite(norm) or norm < 1e-9:
                out.append(np.array([1e3]))
                continue
            normal = np.array([-direction[1], direction[0]]) / norm
            out.append(np.array([float(np.dot(click - pa, normal))]))
    return np.concatenate(out) if out else np.zeros(0)


def _solve(image_points, pitch_points, line_clicks, size, k1_grid, refine_k1):
    from scipy.optimize import least_squares

    best = None
    centre, half = _norm(size)
    radius2 = np.max(np.sum(((image_points - centre) / half) ** 2, axis=1)) if len(image_points) else 0
    for k1 in k1_grid:
        # The division model must stay invertible over the clicked area.
        if 1 + k1 * radius2 < 0.25:
            continue
        try:
            G = _dlt(pitch_points, undistort(image_points, k1, size))
        except ValueError:
            continue
        r = _residuals(G, k1, size, image_points, pitch_points, line_clicks)
        if not np.isfinite(r).all():
            continue  # this lens value folds the clicked points; not a real lens
        cost = float(np.sum(np.minimum(r**2, 400)))  # robust: cap each at 20 px
        if best is None or cost < best[0]:
            best = (cost, G, k1)
    if best is None:
        raise ValueError("These points do not define a pitch plane. Pick points that are spread out.")
    _, G, k1 = best

    def unpack(x):
        g = np.append(x[:8], 1).reshape(3, 3)
        return g, (x[8] if refine_k1 else k1)

    x0 = np.append((G / G[2, 2]).ravel()[:8], [k1] if refine_k1 else [])
    if not np.isfinite(_residuals(*unpack(x0), size, image_points, pitch_points, line_clicks)).all():
        raise ValueError("These points do not define a pitch plane. Pick points that are spread out.")
    solution = least_squares(
        lambda x: _residuals(*unpack(x), size, image_points, pitch_points, line_clicks),
        x0,
        loss="soft_l1",
        f_scale=2.0,
        max_nfev=3000,
    )
    G, k1 = unpack(solution.x)
    if refine_k1 and (not -1.5 < k1 < 0.2 or 1 + k1 * radius2 < 0.25):
        G, k1 = unpack(x0)
    return G / G[2, 2], float(k1)


def _leave_one_out(image_points, pitch_points, line_clicks, size, k1_grid, refine_k1):
    """Residual (px) of each clicked landmark when the fit is made without it."""
    n = len(image_points)
    out = []
    for i in range(n):
        keep = [j for j in range(n) if j != i]
        if len(keep) < 4 or not _general_position(pitch_points[keep]):
            out.append(None)
            continue
        try:
            G, k1 = _solve(image_points[keep], pitch_points[keep], line_clicks, size, k1_grid, refine_k1)
        except ValueError:
            out.append(None)
            continue
        predicted = distort(apply(G, pitch_points[i : i + 1]), k1, size)[0]
        out.append(float(np.linalg.norm(predicted - image_points[i])))
    return out


def camera_geometry(G, size):
    """Field of view (deg) and camera height (m) implied by a pitch->image homography.

    Assumes square pixels and the principal point at the image centre. Returns
    (None, None) when the homography is not consistent with a real camera.
    """
    w, h = size
    shift = np.array([[1, 0, -w / 2], [0, 1, -h / 2], [0, 0, 1.0]])
    g = shift @ np.asarray(G, float)
    g1, g2, g3 = g[:, 0], g[:, 1], g[:, 2]
    candidates = []
    denom = g1[2] * g2[2]
    if abs(denom) > 1e-12:
        candidates.append(-(g1[0] * g2[0] + g1[1] * g2[1]) / denom)
    denom = g2[2] ** 2 - g1[2] ** 2
    if abs(denom) > 1e-12:
        candidates.append((g1[0] ** 2 + g1[1] ** 2 - g2[0] ** 2 - g2[1] ** 2) / denom)
    f2 = [c for c in candidates if c > 0]
    if not f2:
        return None, None
    f = math.sqrt(float(np.median(f2)))
    K_inv = np.diag([1 / f, 1 / f, 1.0])
    r1, r2, t = K_inv @ g1, K_inv @ g2, K_inv @ g3
    scale = (np.linalg.norm(r1) + np.linalg.norm(r2)) / 2
    if scale <= 0:
        return None, None
    r1, r2, t = r1 / scale, r2 / scale, t / scale
    r3 = np.cross(r1, r2)
    R = np.column_stack([r1, r2, r3])
    centre = -R.T @ t
    fov = math.degrees(2 * math.atan(w / (2 * f)))
    return round(fov, 1), round(abs(float(centre[2])), 2)


def fit(image_points, pitch_points, size, distortion="auto", line_clicks=None):
    """Fit a calibration from clicked landmarks and (optional) points along lines.

    distortion: "none", "auto" (grid-search a one-parameter division model and
    keep it only if held-out clicks agree better), or "on".
    line_clicks: [((x, y), ((ax, ay), (bx, by)))] image click and its pitch line.
    Residuals are image pixels (the natural unit of click error); each landmark
    is also reported in metres and as a leave-one-out residual.
    """
    image_points = np.asarray(image_points, float).reshape(-1, 2)
    pitch_points = np.asarray(pitch_points, float).reshape(-1, 2)
    line_clicks = list(line_clicks or [])
    n = len(image_points)
    if n < 4 or len(pitch_points) != n:
        raise ValueError("Click at least four pitch landmarks.")
    if _spread(pitch_points) < 20:
        raise ValueError("The landmarks are too close together or in a line. Spread them across the pitch.")
    if not _general_position(pitch_points):
        raise ValueError(
            "At least four of the landmarks must not share a line (for example, not all on the halfway line). "
            "Add a corner, a goal post or a point on another line."
        )
    constraints = 2 * n + len(line_clicks)
    # With fewer constraints than this the lens term is not identifiable: it
    # would absorb click error and invent a fisheye.
    use_distortion = distortion in ("on", "auto") and constraints >= 12
    plain = _solve(image_points, pitch_points, line_clicks, size, [0.0], False)
    G, k1 = plain
    if use_distortion:
        grid = list(np.arange(-1.2, 0.051, 0.05))
        warped = _solve(image_points, pitch_points, line_clicks, size, grid, True)
        if distortion == "on":
            G, k1 = warped
        elif n >= 6:
            # Keep the lens term only if it predicts held-out clicks >= 10% better.
            loo_plain = [r for r in _leave_one_out(image_points, pitch_points, line_clicks, size, [0.0], False) if r is not None]
            loo_warped = [
                r for r in _leave_one_out(image_points, pitch_points, line_clicks, size, [warped[1]], True) if r is not None
            ]
            if loo_plain and loo_warped and np.sqrt(np.mean(np.square(loo_warped))) < 0.9 * np.sqrt(np.mean(np.square(loo_plain))):
                G, k1 = warped
        elif len(line_clicks) >= 6:
            r0 = _residuals(*plain, size, image_points, pitch_points, line_clicks)
            r1 = _residuals(*warped, size, image_points, pitch_points, line_clicks)
            if np.sqrt(np.mean(r1**2)) < 0.7 * np.sqrt(np.mean(r0**2)):
                G, k1 = warped
    H = np.linalg.inv(G)
    H = H / H[2, 2]
    calibration = {"H": H.tolist(), "k1": k1, "size": list(size)}
    loo = _leave_one_out(image_points, pitch_points, line_clicks, size, [k1], False) if n >= 5 else [None] * n
    return describe(calibration, image_points, pitch_points, line_clicks, G, loo)


def describe(calibration, image_points, pitch_points, line_clicks=(), G=None, loo=None):
    H = np.asarray(calibration["H"], float)
    k1 = calibration["k1"]
    size = calibration["size"]
    if G is None:
        G = np.linalg.inv(H)
    scale = size[1] / 360.0  # pixel thresholds are for 360p; scale with height
    pixel = _residuals(G, k1, size, image_points, pitch_points, [])
    pixel = np.linalg.norm(pixel.reshape(-1, 2), axis=1)
    lines = np.abs(_residuals(G, k1, size, np.zeros((0, 2)), np.zeros((0, 2)), list(line_clicks))) if line_clicks else np.zeros(0)
    everything = np.concatenate([pixel, lines])
    rms_px = float(np.sqrt(np.mean(everything**2))) if len(everything) else 0.0
    metres = np.linalg.norm(apply(H, undistort(image_points, k1, size)) - pitch_points, axis=1)
    warnings = []
    n = len(image_points)
    if n == 4 and not len(line_clicks):
        warnings.append(
            "Four points fit exactly, so their error cannot be measured. Add a fifth landmark or click along a line to check the fit."
        )
    loo = loo or [None] * n
    known = [r for r in loo if r is not None]
    if known:
        median = float(np.median(known))
        for i, r in enumerate(loo):
            if r is not None and r > max(3 * scale, 3 * median):
                warnings.append(f"Landmark {i + 1} disagrees with the others ({r:.1f} px when left out); it may be the wrong point.")
    folded = not horizon_ok(calibration, image_points, pitch_points)
    if folded:
        warnings.append("The pitch folds over itself with these clicks; two landmarks are probably swapped.")
    fov, height = camera_geometry(G, size)
    if fov is None or not 30 <= fov <= 175:
        warnings.append("The fit does not look like a real camera; check the landmarks and the pitch dimensions.")
    elif height is not None and not 1.0 <= height <= 30:
        warnings.append(f"The fit puts the camera {height:.0f} m above the pitch; check the pitch dimensions.")
    verifiable = n > 4 or len(line_clicks) > 0
    quality = "good" if rms_px <= 2 * scale else "check" if rms_px <= 4 * scale else "poor"
    if folded:
        quality = "poor"
    elif not verifiable:
        quality = "unverified"
    return {
        **calibration,
        "residuals": [round(float(r), 3) for r in metres],
        "pixelResiduals": [round(float(r), 2) for r in pixel],
        "leaveOneOut": [None if r is None else round(float(r), 2) for r in loo],
        "lineResiduals": [round(float(r), 2) for r in lines],
        "rmsPixels": round(rms_px, 2),
        "rms": round(float(np.sqrt(np.mean(metres**2))), 3),
        "fieldOfView": fov,
        "cameraHeight": height,
        "quality": quality,
        "warnings": warnings,
    }


def horizon_ok(calibration, image_points=None, pitch_points=None):
    """Every clicked point must lie on the same side of the camera horizon.

    A mismatched landmark (say two corners swapped) folds the plane over
    itself, and some points then map through the horizon (the projective
    denominator changes sign). Image pixels away from the clicks are not
    checked: walls and crowd legitimately sit above the pitch horizon.
    """
    H = np.asarray(calibration["H"], float)
    try:
        inverse = np.linalg.inv(H)
    except np.linalg.LinAlgError:
        return False
    if not np.isfinite(inverse).all():
        return False
    signs = []
    if image_points is not None and len(image_points):
        undistorted = undistort(image_points, calibration.get("k1", 0), calibration["size"])
        pts = np.hstack([undistorted, np.ones((len(undistorted), 1))])
        signs.append(np.sign((pts @ H.T)[:, 2]))
    if pitch_points is not None and len(pitch_points):
        pts = np.hstack([np.asarray(pitch_points, float), np.ones((len(pitch_points), 1))])
        signs.append(np.sign((pts @ inverse.T)[:, 2]))
    if not signs:
        return True
    return all(np.all(s == s[0]) and s[0] != 0 for s in signs)


def image_to_pitch(calibration, points, H=None):
    H = np.asarray(H if H is not None else calibration["H"], float)
    return apply(H, undistort(points, calibration["k1"], calibration["size"]))


def pitch_to_image(calibration, points, H=None):
    H = np.asarray(H if H is not None else calibration["H"], float)
    return distort(apply(np.linalg.inv(H), points), calibration["k1"], calibration["size"])


# ---------------------------------------------------------------- camera motion

IDENTITY = np.eye(3)


def _affine(frame):
    m = frame.get("camera")
    if m is None:
        return None
    a = np.asarray(m, float).reshape(2, 3)
    # Deadband: a fixed camera's motion estimate is noise around identity.
    # Composing thousands of noisy near-identities drifts; treat them as exact.
    if (
        abs(a[0, 2]) < 0.25
        and abs(a[1, 2]) < 0.25
        and abs(a[0, 0] - 1) < 0.002
        and abs(a[1, 1] - 1) < 0.002
        and abs(a[0, 1]) < 0.002
        and abs(a[1, 0]) < 0.002
    ):
        return IDENTITY
    return np.vstack([a, [0, 0, 1]])


def static_camera(frames):
    """True when the recorded camera motion is negligible (a fixed venue camera)."""
    moves = [
        np.hypot(*np.asarray(f["camera"], float).reshape(2, 3)[:, 2])
        for f in frames
        if f.get("camera") is not None
    ]
    if len(moves) < max(5, len(frames) * 0.5):
        return False
    # A fixed camera that was bumped or re-aimed moves for a moment: not static.
    return float(np.percentile(moves, 95)) < 0.6 and float(np.max(moves)) < 3.0


def _elapsed(frames, earlier, later):
    a, b = frames[earlier].get("t"), frames[later].get("t")
    return 0.0 if a is None or b is None else b - a


def propagate(frames, anchors, static=None, max_seconds=12.0):
    """Per-frame image->pitch homographies (in undistorted-pixel terms) from anchors.

    anchors: {frame index: 3x3 image->pitch homography} for frames that were
    calibrated (clicked) or re-aligned to the lines. Each anchor is carried
    forwards and backwards through the camera motion inside its scene until the
    next anchor or a cut. Returns (list of H or None, list of steps from anchor).
    Camera motion is estimated on raw frames, so with lens distortion this is an
    approximation that the line re-alignment corrects.
    """
    n = len(frames)
    if static is None:
        static = static_camera(frames)
    homographies = [None] * n
    distance = [None] * n
    for index, H in sorted(anchors.items()):
        H = np.asarray(H, float)
        scene = frames[index]["scene"]
        homographies[index], distance[index] = H, 0
        # forwards: image(i) -> image(anchor) is inv(A_i ... A_{anchor+1})
        to_anchor = IDENTITY.copy()
        for i in range(index + 1, n):
            if frames[i]["scene"] != scene or i in anchors:
                break
            # A moving camera's motion chain drifts: without a re-alignment to the
            # painted lines, stop trusting it after max_seconds.
            if not static and _elapsed(frames, index, i) > max_seconds:
                break
            a = IDENTITY if static else _affine(frames[i])
            if a is None:
                break
            to_anchor = to_anchor @ np.linalg.inv(a)
            if distance[i] is None or distance[i] > i - index:
                homographies[i], distance[i] = H @ to_anchor, i - index
        # backwards: image(i) -> image(anchor) is A_anchor ... A_{i+1}
        to_anchor = IDENTITY.copy()
        for i in range(index - 1, -1, -1):
            if frames[i]["scene"] != scene or i in anchors:
                break
            if not static and _elapsed(frames, i, index) > max_seconds:
                break
            a = IDENTITY if static else _affine(frames[i + 1])
            if a is None:
                break
            to_anchor = to_anchor @ a
            if distance[i] is None or distance[i] > index - i:
                homographies[i], distance[i] = H @ to_anchor, index - i
    return homographies, distance


# ---------------------------------------------------------------- line alignment


def line_mask(frame, pitch_colour=None, restrict_to_grass=True):
    """Thin bright markings (the painted lines).

    By default only on grass-coloured ground. With restrict_to_grass=False the
    caller restricts the mask to the calibrated pitch area instead, which also
    works on blue or red indoor courts.
    """
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    lightness = lab[:, :, 0]
    h = frame.shape[0]
    k = max(5, int(h * 0.025) | 1)
    tophat = cv2.morphologyEx(lightness, cv2.MORPH_TOPHAT, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    bright = (tophat >= max(18, np.percentile(tophat, 97))) & (hsv[:, :, 1] < 90)
    mask = bright.astype(np.uint8) * 255
    if restrict_to_grass:
        # Only markings on the playing surface: grass-coloured surroundings.
        grass = cv2.inRange(hsv, np.array([25, 30, 25]), np.array([100, 255, 255]))
        grass = cv2.dilate(grass, np.ones((k, k), np.uint8))
        mask &= grass
    return mask


def pitch_region(calibration, H, template, shape, margin_m=1.5):
    """Mask of the image area covered by the pitch (plus a margin) under H."""
    L, W = template["length"], template["width"]
    outline = [(x, -margin_m) for x in np.linspace(-margin_m, L + margin_m, 24)]
    outline += [(L + margin_m, y) for y in np.linspace(-margin_m, W + margin_m, 12)]
    outline += [(x, W + margin_m) for x in np.linspace(L + margin_m, -margin_m, 24)]
    outline += [(-margin_m, y) for y in np.linspace(W + margin_m, -margin_m, 12)]
    pts = pitch_to_image(calibration, outline, H)
    pts = pts[np.isfinite(pts).all(axis=1)]
    region = np.zeros(shape[:2], np.uint8)
    if len(pts) >= 3:
        pts = np.clip(pts, -4 * shape[1], 4 * shape[1]).astype(np.int32)
        cv2.fillPoly(region, [cv2.convexHull(pts)], 255)
    return region


def _chamfer_field(mask, cap):
    inverted = np.where(mask > 0, 0, 255).astype(np.uint8)
    field = cv2.distanceTransform(inverted, cv2.DIST_L2, 3)
    return np.minimum(field, cap)


def align_to_lines(frame, calibration, H, template, max_nfev=200, precomputed=None, search=0, max_shift=None):
    """Refine an image->pitch homography so projected lines sit on painted lines.

    Staged to avoid false locks on a partial, symmetric view of the lines:
    an optional coarse search over image shifts (`search` pixels, for
    re-acquiring after the camera moved unobserved), then a pan/tilt/zoom
    (similarity) fit, then a lightly regularised full homography correction.
    The result is refused when it moves the lines implausibly far
    (`max_shift`), changes scale by more than ~20%, or does not improve the fit.

    Returns (H or None, score): score is the fraction of visible template
    samples lying within 2 px of a detected marking.
    """
    from scipy.ndimage import map_coordinates
    from scipy.optimize import least_squares

    h, w = frame.shape[:2]
    cap = max(6.0, h * 0.03)
    mask = precomputed if precomputed is not None else line_mask(frame)
    # Keep markings near where the pitch should be (walls, boards and adverts
    # outside it also have bright lines).
    region = pitch_region(calibration, H, template, (h, w), margin_m=2.0 + (search or 0) / max(1.0, h) * 10)
    region = cv2.dilate(region, np.ones((max(3, int(h * 0.08)),) * 2, np.uint8))
    mask = mask & region
    if mask.sum() / 255 < h * w * 0.002:
        return None, 0.0
    field = _chamfer_field(mask, cap)
    samples = np.array([p for line in line_segments(template, step=0.2) for p in line])
    size = calibration["size"]
    k1 = calibration["k1"]
    to_image = np.linalg.inv(H)
    centre = np.array([w / 2, h / 2])
    shift_c = np.array([[1, 0, centre[0]], [0, 1, centre[1]], [0, 0, 1.0]])
    unshift_c = np.array([[1, 0, -centre[0]], [0, 1, -centre[1]], [0, 0, 1.0]])

    def project(M):
        return distort(apply(M @ to_image, samples), k1, size)

    def visible(points):
        return (
            np.isfinite(points).all(axis=1)
            & (points[:, 0] >= 1)
            & (points[:, 0] < w - 1)
            & (points[:, 1] >= 1)
            & (points[:, 1] < h - 1)
        )

    def values(M, subset):
        points = project(M)[subset]
        inside = visible(points)
        out = np.full(len(points), cap)
        out[inside] = map_coordinates(field, [points[inside, 1], points[inside, 0]], order=1, mode="nearest")
        return out

    def score_of(M):
        points = project(M)
        inside = visible(points)
        if inside.sum() < 60:
            return 0.0, int(inside.sum())
        vals = map_coordinates(field, [points[inside, 1], points[inside, 0]], order=1, mode="nearest")
        return float((vals <= 2).mean()), int(inside.sum())

    identity = np.eye(3)
    base = project(identity)
    keep = visible(base)
    if search:
        # Samples visible anywhere in a padded frame, so a shifted view still counts.
        pad = search
        keep = (
            np.isfinite(base).all(axis=1)
            & (base[:, 0] >= -pad)
            & (base[:, 0] < w + pad)
            & (base[:, 1] >= -pad)
            & (base[:, 1] < h + pad)
        )
    if keep.sum() < 60:
        return None, 0.0
    score_before, _ = score_of(identity)

    # Stage 0: coarse search over image shifts.
    M0 = identity
    if search:
        step = max(2, int(search / 12))
        best = None
        for dx in range(-search, search + 1, step):
            for dy in range(-search // 2, search // 2 + 1, step):
                T = np.array([[1, 0, dx], [0, 1, dy], [0, 0, 1.0]])
                cost = float(values(T, keep).mean())
                if best is None or cost < best[0]:
                    best = (cost, T)
        M0 = best[1]

    # Stage 1: similarity (zoom, rotation, shift) around the image centre.
    def similarity(q):
        a = 1 + q[0] * 1e-2
        r = q[1] * 1e-2
        S = np.array([[a * math.cos(r), -a * math.sin(r), q[2]], [a * math.sin(r), a * math.cos(r), q[3]], [0, 0, 1.0]])
        return shift_c @ S @ unshift_c @ M0

    sol1 = least_squares(lambda q: values(similarity(q), keep), np.zeros(4), loss="soft_l1", f_scale=2.0, max_nfev=max_nfev, diff_step=1e-3)
    M1 = similarity(sol1.x)

    # Stage 2: full correction, regularised towards the similarity solution.
    scale = np.array([1e-2, 1e-2, 5.0, 1e-2, 1e-2, 5.0, 5e-5, 5e-5])

    def full(p):
        p = p * scale
        D = np.array([[1 + p[0], p[1], p[2]], [p[3], 1 + p[4], p[5]], [p[6], p[7], 1.0]])
        return shift_c @ D @ unshift_c @ M1

    prior = 0.5 * math.sqrt(keep.sum())
    sol2 = least_squares(
        lambda p: np.concatenate([values(full(p), keep), prior * p / 10]),
        np.zeros(8),
        loss="soft_l1",
        f_scale=2.0,
        max_nfev=max_nfev,
        diff_step=1e-3,
    )
    M = full(sol2.x)
    score, count = score_of(M)
    if score < max(0.35, score_before) or count < 60:
        return None, score_before
    # Plausibility: the correction must look like camera motion, not a warp.
    linear = M[:2, :2] / M[2, 2]
    zoom = math.sqrt(abs(np.linalg.det(linear)))
    if not 0.8 < zoom < 1.25:
        return None, score_before
    moved = project(M)[keep] - base[keep]
    finite = np.isfinite(moved).all(axis=1)
    limit = max_shift if max_shift is not None else (search + 40 if search else 60)
    if not finite.any() or float(np.median(np.linalg.norm(moved[finite], axis=1))) > limit:
        return None, score_before
    refined = np.linalg.inv(M @ to_image)
    return refined / refined[2, 2], score
