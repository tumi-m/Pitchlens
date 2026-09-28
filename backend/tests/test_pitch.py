import math

import cv2
import numpy as np
import pytest

from app.vision import pitch

SIZE = (640, 360)
TEMPLATE = pitch.normalise_template("futsal")


def camera_homography():
    """A plausible elevated side-on view of a 40x20 court (pitch -> image)."""
    image = np.array([[120, 90], [520, 90], [630, 330], [10, 330]], np.float64)
    L, W = TEMPLATE["length"], TEMPLATE["width"]
    world = np.array([[0, 0], [L, 0], [L, W], [0, W]], np.float64)
    H, _ = cv2.findHomography(world, image)
    return H


def render(H_pitch_to_image, k1=0.0):
    frame = np.zeros((SIZE[1], SIZE[0], 3), np.uint8)
    frame[:] = (40, 140, 40)
    cal = {"H": np.linalg.inv(H_pitch_to_image).tolist(), "k1": k1, "size": list(SIZE)}
    for line in pitch.line_segments(TEMPLATE, step=0.05):
        pts = pitch.pitch_to_image(cal, line)
        for a, b in zip(pts[:-1], pts[1:]):
            if np.isfinite(a).all() and np.isfinite(b).all():
                cv2.line(frame, tuple(np.round(a).astype(int)), tuple(np.round(b).astype(int)), (235, 235, 235), 2)
    return frame


def test_fit_recovers_pitch_positions_from_clicks():
    H = camera_homography()
    names = ["corner-far-left", "corner-far-right", "halfway-near", "centre-spot", "left-goal-near-post", "halfway-far"]
    world = np.array([pitch.landmarks(TEMPLATE)[n] for n in names])
    clicks = pitch.apply(H, world) + np.random.default_rng(0).normal(0, 0.4, (len(names), 2))
    cal = pitch.fit(clicks, world, SIZE, distortion="none")
    assert cal["quality"] == "good" and cal["rms"] < 0.5 and cal["rmsPixels"] < 1.5
    feet = pitch.apply(H, [[10.0, 5.0], [30.0, 15.0]])
    assert np.allclose(pitch.image_to_pitch(cal, feet), [[10, 5], [30, 15]], atol=0.5)


def test_four_points_are_reported_unverified_not_perfect():
    H = camera_homography()
    names = ["corner-far-left", "corner-far-right", "corner-near-right", "corner-near-left"]
    world = np.array([pitch.landmarks(TEMPLATE)[n] for n in names])
    cal = pitch.fit(pitch.apply(H, world), world, SIZE)
    assert cal["quality"] == "unverified"
    assert any("cannot be measured" in w for w in cal["warnings"])


def test_collinear_clicks_are_rejected():
    world = np.array([[0, 0], [10, 0], [20, 0], [30, 0.1]])
    with pytest.raises(ValueError, match="(?i)spread"):
        pitch.fit(np.array([[1, 1], [2, 2], [3, 3], [4, 4.1]]), world, SIZE)


def test_wide_angle_distortion_is_estimated_when_it_helps():
    H = camera_homography()
    rng = np.random.default_rng(5)
    for k1 in (-0.18, -0.5):  # mild barrel and a strong fisheye the old fit rejected
        names = list(pitch.landmarks(TEMPLATE))
        world = np.array([pitch.landmarks(TEMPLATE)[n] for n in names])
        clicks = pitch.distort(pitch.apply(H, world), k1, SIZE) + rng.normal(0, 0.5, (len(names), 2))
        plain = pitch.fit(clicks, world, SIZE, distortion="none")
        fitted = pitch.fit(clicks, world, SIZE, distortion="auto")
        assert fitted["rmsPixels"] < plain["rmsPixels"] * 0.5
        assert abs(fitted["k1"] - k1) < 0.08
        assert fitted["quality"] == "good"
        probe = np.array([[8.0, 4.0], [32.0, 16.0]])
        seen = pitch.distort(pitch.apply(H, probe), k1, SIZE)
        assert np.linalg.norm(pitch.image_to_pitch(fitted, seen) - probe, axis=1).max() < 0.3


def test_points_along_lines_make_distortion_observable_with_few_landmarks():
    H = camera_homography()
    k1 = -0.35
    marks = pitch.landmarks(TEMPLATE)
    names = ["corner-far-left", "corner-far-right", "halfway-near", "centre-spot"]
    world = np.array([marks[n] for n in names])
    clicks = pitch.distort(pitch.apply(H, world), k1, SIZE)
    lines = pitch.straight_lines(TEMPLATE)
    line_clicks = []
    for name in ("far-touchline", "near-touchline", "halfway-line", "left-goal-line"):
        a, b = np.array(lines[name])
        for s in np.linspace(0.1, 0.9, 5):
            p = a + (b - a) * s
            line_clicks.append((tuple(pitch.distort(pitch.apply(H, [p]), k1, SIZE)[0]), lines[name]))
    four = pitch.fit(clicks, world, SIZE, distortion="auto")
    assert four["quality"] == "unverified"
    with_lines = pitch.fit(clicks, world, SIZE, distortion="auto", line_clicks=line_clicks)
    assert with_lines["quality"] == "good" and abs(with_lines["k1"] - k1) < 0.08


def test_camera_geometry_is_plausible_for_a_synthetic_camera():
    fov, height = pitch.camera_geometry(camera_homography(), SIZE)
    assert fov is not None and 30 < fov < 175 and height is not None and height > 0


def test_a_mis_clicked_landmark_is_flagged():
    H = camera_homography()
    marks = pitch.landmarks(TEMPLATE)
    names = list(marks)[:9]
    world = np.array([marks[n] for n in names])
    clicks = pitch.apply(H, world)
    clicks[3] += [25, -15]
    cal = pitch.fit(clicks, world, SIZE, distortion="none")
    assert any("Landmark 4" in w for w in cal["warnings"])


def test_distort_inverts_undistort():
    points = np.array([[10.0, 10.0], [320, 180], [630, 350], [100, 300]])
    for k1 in (-0.3, -0.1, 0.1):
        assert np.allclose(pitch.undistort(pitch.distort(points, k1, SIZE), k1, SIZE), points, atol=1e-6)


def test_calibration_follows_a_panning_camera_within_a_scene():
    H = camera_homography()
    cal = {"H": np.linalg.inv(H), "k1": 0.0, "size": list(SIZE)}
    frames = []
    offset = 0.0
    for i in range(10):
        step = 0 if i == 0 else 12.0  # camera pans right: the picture moves left 12 px per frame
        offset += step
        frames.append({"scene": 0, "camera": [1, 0, -step, 0, 1, 0] if i else None})
    frames.append({"scene": 1, "camera": None})
    homographies, distance = pitch.propagate(frames, {0: cal["H"]}, static=False)
    assert homographies[-1] is None and distance[9] == 9
    # A player standing at pitch (20, 10): in frame 9 the image point moved 108 px left.
    point0 = pitch.apply(H, [[20.0, 10.0]])
    point9 = point0 - [108.0, 0]
    assert np.allclose(pitch.apply(homographies[9], point9), [[20, 10]], atol=1e-6)


def test_static_camera_ignores_motion_noise():
    rng = np.random.default_rng(1)
    frames = [{"scene": 0, "camera": [1, 0, float(rng.normal(0, 0.1)), 0, 1, float(rng.normal(0, 0.1))]} for _ in range(200)]
    assert pitch.static_camera(frames)
    H = np.linalg.inv(camera_homography())
    homographies, _ = pitch.propagate(frames, {100: H})
    assert all(np.allclose(h, H) for h in homographies)


def test_line_alignment_corrects_a_drifted_calibration():
    H = camera_homography()
    frame = render(H)
    truth = np.linalg.inv(H)
    cal = {"H": truth.tolist(), "k1": 0.0, "size": list(SIZE)}
    # Drift: the estimate is off by an 8 px pan and a slight zoom.
    drift = np.array([[1.02, 0, -8.0], [0, 1.02, 3.0], [0, 0, 1]])
    drifted = truth @ np.linalg.inv(drift)
    refined, score = pitch.align_to_lines(frame, cal, drifted, TEMPLATE)
    assert refined is not None and score > 0.6
    probe = pitch.apply(H, [[5.0, 5.0], [35.0, 15.0], [20.0, 10.0]])
    err_before = np.linalg.norm(pitch.apply(drifted, probe) - [[5, 5], [35, 15], [20, 10]], axis=1).max()
    err_after = np.linalg.norm(pitch.apply(refined, probe) - [[5, 5], [35, 15], [20, 10]], axis=1).max()
    assert err_after < 0.35 < err_before


def test_line_alignment_declines_a_frame_without_lines():
    frame = np.zeros((SIZE[1], SIZE[0], 3), np.uint8)
    frame[:] = (40, 140, 40)
    H = np.linalg.inv(camera_homography())
    refined, _ = pitch.align_to_lines(frame, {"H": H, "k1": 0.0, "size": list(SIZE)}, H, TEMPLATE)
    assert refined is None


def test_templates_validate_dimensions():
    assert pitch.normalise_template({"length": 38, "width": 19})["length"] == 38
    with pytest.raises(ValueError):
        pitch.normalise_template({"length": 5, "width": 3})
    points = pitch.landmarks(pitch.normalise_template("eleven-a-side"))
    assert points["right-penalty-spot"] == (94.0, 34.0)
    assert math.isclose(points["left-goal-near-post"][1], 34 + 3.66)


def test_three_landmarks_on_one_line_are_explained():
    t = TEMPLATE
    marks = pitch.landmarks(t)
    names = ["halfway-far", "centre-spot", "halfway-near", "corner-far-right"]
    world = np.array([marks[n] for n in names])
    with pytest.raises(ValueError, match="share a line"):
        pitch.fit(pitch.apply(camera_homography(), world), world, SIZE)


def test_build_follows_a_panning_video_and_recovers_after_motion_failure(tmp_path):
    from app.vision import calibrate

    rng = np.random.default_rng(3)
    fps = 5.0
    H0 = camera_homography()
    # Zoom in so only part of the court is visible, then pan across it.
    zoom = np.array([[1.6, 0, -250], [0, 1.6, -120], [0, 0, 1]])
    truths, frames = [], []
    writer = cv2.VideoWriter(str(tmp_path / "pan.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), fps, SIZE)
    pan = 0.0
    for i in range(60):
        step = 0.0 if i == 0 else 9.0
        pan += step
        T = np.array([[1, 0, -pan], [0, 1, 0], [0, 0, 1]]) @ zoom
        P2I = T @ H0
        truths.append(P2I)
        image = render(P2I)
        image = np.clip(image.astype(int) + rng.normal(0, 6, image.shape), 0, 255).astype(np.uint8)
        writer.write(image)
        noisy = [1, 0, -step + rng.normal(0, 0.8), 0, 1, rng.normal(0, 0.8)]
        # Motion estimation fails for two frames mid-way (fast pan, blur).
        camera = None if i in (30, 31) else (noisy if i else None)
        frames.append({"t": round(i / fps, 3), "scene": 0, "players": [], "ball": None, "camera": camera})
    writer.release()
    marks = pitch.landmarks(TEMPLATE)
    names = [n for n in marks if np.all(np.isfinite(pitch.apply(truths[0], [marks[n]])))]
    visible = [n for n in names if 5 < pitch.apply(truths[0], [marks[n]])[0][0] < 635 and 5 < pitch.apply(truths[0], [marks[n]])[0][1] < 355]
    assert len(visible) >= 4
    points = [{"name": n, "x": float(pitch.apply(truths[0], [marks[n]])[0][0]), "y": float(pitch.apply(truths[0], [marks[n]])[0][1])} for n in visible]
    result = {"frames": frames, "sampleFps": fps, "video": {"width": SIZE[0], "height": SIZE[1]}}
    request = {"template": "futsal", "points": points, "t": 0.0, "distortion": "none"}
    out = calibrate.build(result, request, tmp_path / "pan.mp4", stride_seconds=1.0)
    assert out["coverage"] == 100.0
    probe = np.array([[10.0, 5.0], [20.0, 10.0], [30.0, 15.0]])
    errors = []
    for i, f in enumerate(out["frames"]):
        H = np.asarray(f["H"]).reshape(3, 3)
        image_points = pitch.apply(truths[i], probe)
        inside = (image_points[:, 0] > 0) & (image_points[:, 0] < 640) & (image_points[:, 1] > 0) & (image_points[:, 1] < 360)
        if inside.any():
            errors.append(np.linalg.norm(pitch.apply(H, image_points[inside]) - probe[inside], axis=1).max())
    # Without line re-alignment the noisy motion chain drifts by metres; with it
    # every frame stays within half a metre.
    assert max(errors) < 0.5, [(i, round(e, 2)) for i, e in enumerate(errors)]
    assert out["lineAligned"] >= 5


def test_line_alignment_works_on_a_blue_indoor_court():
    H = camera_homography()
    frame = render(H)
    court = np.all(frame == (40, 140, 40), axis=2)
    frame[court] = (150, 80, 30)  # blue-ish court, BGR
    truth = np.linalg.inv(H)
    cal = {"H": truth.tolist(), "k1": 0.0, "size": list(SIZE)}
    drifted = truth @ np.linalg.inv(np.array([[1.0, 0, -6.0], [0, 1.0, 2.0], [0, 0, 1]]))
    assert pitch.line_mask(frame).sum() == 0  # the grass rule finds nothing here
    mask = pitch.line_mask(frame, restrict_to_grass=False)
    refined, score = pitch.align_to_lines(frame, cal, drifted, TEMPLATE, precomputed=mask)
    assert refined is not None and score > 0.6
    probe = pitch.apply(H, [[10.0, 5.0], [30.0, 15.0]])
    assert np.linalg.norm(pitch.apply(refined, probe) - [[10, 5], [30, 15]], axis=1).max() < 0.35


def test_template_dimensions_are_bounded():
    with pytest.raises(ValueError):
        pitch.normalise_template({"length": 40, "width": 20, "centreRadius": 1e9})
    with pytest.raises(ValueError):
        pitch.normalise_template({"length": "forty", "width": 20})
    with pytest.raises(ValueError):
        pitch.normalise_template(["not", "a", "dict"])


def test_forcing_distortion_with_four_clicks_does_not_invent_a_lens():
    H = camera_homography()
    names = ["corner-far-left", "corner-far-right", "corner-near-right", "corner-near-left"]
    world = np.array([pitch.landmarks(TEMPLATE)[n] for n in names])
    cal = pitch.fit(pitch.apply(H, world) + [[1.5, -1], [0, 1], [-1, 0], [1, 1]], world, SIZE, distortion="on")
    assert cal["k1"] == 0.0


def test_points_behind_the_camera_are_not_drawn():
    # Camera 3 m up at the near touchline, looking along the pitch to the left goal.
    K = np.array([[300.0, 0, 320], [0, 300.0, 180], [0, 0, 1]])
    yaw, pitch_down = np.radians(170), np.radians(8)
    Rz = np.array([[np.cos(yaw), -np.sin(yaw), 0], [np.sin(yaw), np.cos(yaw), 0], [0, 0, 1]])
    base = np.array([[0, 1, 0], [0, 0, -1], [1, 0, 0]])  # x_cam = world y, y_cam = -z, z_cam = world x
    Rx = np.array([[1, 0, 0], [0, np.cos(pitch_down), -np.sin(pitch_down)], [0, np.sin(pitch_down), np.cos(pitch_down)]])
    R = Rx @ base @ Rz.T
    C = np.array([20.0, 21.0, 3.0])
    t = -R @ C
    P2I = K @ np.column_stack([R[:, 0], R[:, 1], t])  # ground plane z = 0
    cal = {"H": np.linalg.inv(P2I), "k1": 0.0, "size": list(SIZE)}
    front = pitch.pitch_to_image(cal, [[5.0, 10.0]])  # towards the left goal: visible
    behind = pitch.pitch_to_image(cal, [[38.0, 10.0]])  # the right goal: behind the camera
    assert np.isfinite(front).all() and np.isnan(behind).all()


def _crowd(ids, dx=0.0):
    spots = [(100, 200), (200, 220), (300, 180), (400, 240), (500, 210)]
    return [{"id": k, "box": [x - 5 + dx, y - 30, x + 5 + dx, y]} for k, (x, y) in zip(ids, spots)]


def test_a_slowly_re_aimed_fixed_camera_is_not_static():
    rng = np.random.default_rng(2)
    frames = [{"t": i / 5, "scene": 0, "camera": [1, 0, float(rng.normal(0, 0.2)), 0, 1, float(rng.normal(0, 0.2))]} for i in range(3000)]
    assert pitch.static_camera(frames)
    for f in frames[1500:1596]:  # 2.5 px per frame for 19 s: noise in any one frame, 240 px in all
        f["camera"][2] += 2.5
    assert not pitch.static_camera(frames)


def test_a_fixed_camera_is_only_carried_across_a_motion_gap_the_tracks_survive():
    H = np.linalg.inv(camera_homography())

    def frames_with(gap_players):
        frames = []
        for i in range(100):
            players = _crowd(range(1, 6)) if i < 50 else gap_players
            frames.append({"t": i / 5, "scene": 0, "players": players, "camera": None if i in (0, 50, 51) else [1, 0, 0, 0, 1, 0]})
        return frames

    held = frames_with(_crowd(range(1, 6), dx=3))  # the same players, where they were
    assert pitch.static_camera(held) and pitch.static_breaks(held) == [0]
    homographies, _ = pitch.propagate(held, {10: H}, static=True)
    assert all(h is not None for h in homographies)
    for moved in (frames_with(_crowd(range(11, 16))), frames_with(_crowd(range(1, 6), dx=240))):
        # New tracks (or everyone shifted together): the camera may have been re-aimed.
        assert pitch.static_breaks(moved) == [0, 50]
        homographies, _ = pitch.propagate(moved, {10: H}, static=True)
        assert all(h is not None for h in homographies[:50]) and all(h is None for h in homographies[50:])


def test_a_re_aimed_fixed_camera_is_found_again_from_the_lines(tmp_path):
    from app.vision import calibrate

    fps = 5.0
    H0 = camera_homography()
    shifted = np.array([[1, 0, -60], [0, 1, 12], [0, 0, 1]]) @ H0  # re-aimed while motion was lost
    writer = cv2.VideoWriter(str(tmp_path / "fixed.avi"), cv2.VideoWriter_fourcc(*"MJPG"), fps, SIZE)
    frames = []
    for i in range(40):
        writer.write(render(H0 if i < 20 else shifted))
        players = _crowd(range(1, 6)) if i < 20 else _crowd(range(11, 16))
        frames.append({"t": round(i / fps, 3), "frame": i, "scene": 0, "players": players, "ball": None,
                       "camera": None if i in (0, 20, 21) else [1, 0, 0, 0, 1, 0]})
    writer.release()
    marks = pitch.landmarks(TEMPLATE)
    names = ["corner-far-left", "corner-far-right", "halfway-near", "centre-spot", "left-goal-near-post", "halfway-far"]
    points = [{"name": n, "x": float(pitch.apply(H0, [marks[n]])[0][0]), "y": float(pitch.apply(H0, [marks[n]])[0][1])} for n in names]
    result = {"frames": frames, "sampleFps": fps, "video": {"width": SIZE[0], "height": SIZE[1], "fps": fps}}
    request = {"template": "futsal", "points": points, "t": 1.0, "distortion": "none"}
    # Without the footage nothing can vouch for the second view: it stays unmapped.
    out = calibrate.build(result, request)
    assert out["static"] and out["coverage"] == 50.0
    out = calibrate.build(result, request, tmp_path / "fixed.avi")
    assert out["coverage"] == 100.0 and out["reacquired"] >= 1
    probe = np.array([[10.0, 5.0], [20.0, 10.0], [30.0, 15.0]])
    H = np.asarray(out["frames"][30]["H"]).reshape(3, 3)
    assert np.linalg.norm(pitch.apply(H, pitch.apply(shifted, probe)) - probe, axis=1).max() < 0.5


def test_line_masks_read_the_analysed_frame_by_its_number(tmp_path, monkeypatch):
    from app.vision import calibrate

    path = tmp_path / "counter.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 30, (64, 48))
    for n in range(90):
        writer.write(np.full((48, 64, 3), n * 2, np.uint8))
    writer.release()
    monkeypatch.setattr(calibrate.pitchlib, "line_mask", lambda image, **kw: image[:, :, 0])
    numbers = [12, 15, 30, 61, 88]
    # Stored times that disagree with the decoder clock, as on variable-frame-rate video.
    frames = [{"t": round(n / 30 + 0.4, 3), "frame": n} for n in numbers]
    masks = calibrate.line_masks(path, frames, range(len(frames)))
    seen = {i: int(np.median(cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE))) for i, png in masks.items()}
    assert sorted(seen) == list(range(len(numbers)))
    assert all(abs(seen[i] - 2 * n) <= 3 for i, n in enumerate(numbers)), seen


def test_clicks_between_analysed_frames_of_a_moving_camera_are_refused():
    from app.vision import calibrate

    H = camera_homography()
    marks = pitch.landmarks(TEMPLATE)
    names = ["corner-far-left", "corner-far-right", "halfway-near", "centre-spot", "left-goal-near-post", "halfway-far"]
    points = [{"name": n, "x": float(pitch.apply(H, [marks[n]])[0][0]), "y": float(pitch.apply(H, [marks[n]])[0][1])} for n in names]
    frames = [{"t": round(i / 3, 3), "scene": 0, "players": [], "ball": None, "camera": [1, 0, -20, 0, 1, 0] if i else None} for i in range(30)]
    result = {"frames": frames, "sampleFps": 3, "video": {"width": SIZE[0], "height": SIZE[1], "fps": 30}}
    with pytest.raises(ValueError, match="between analysed frames"):
        calibrate.build(result, {"template": "futsal", "points": points, "t": 3.16, "distortion": "none"})
    assert calibrate.build(result, {"template": "futsal", "points": points, "t": 3.333, "distortion": "none"})["anchorFrame"] == 10


def _look_at(position, target, fov, size):
    w, h = size
    f = w / 2 / math.tan(math.radians(fov) / 2)
    K = np.array([[f, 0, w / 2], [0, f, h / 2], [0, 0, 1]])
    forward = np.asarray(target, float) - position
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, [0, 0, 1.0])
    right /= np.linalg.norm(right)
    down = np.cross(forward, right)
    R = np.vstack([right, down, forward])
    P = K @ np.hstack([R, (-R @ np.asarray(position, float)).reshape(3, 1)])
    return P[:, [0, 1, 3]]


def test_camera_geometry_is_stable_for_a_camera_facing_straight_across():
    size = (1280, 720)
    G = _look_at(np.array([20.0, 40.0, 8.0]), [20.0, 10.0, 0.0], 70.0, size)  # side-on, level with halfway
    world = np.array([p for p in pitch.landmarks(TEMPLATE).values()], float)
    image = pitch.apply(G, world)
    inside = (image[:, 0] > 0) & (image[:, 0] < size[0]) & (image[:, 1] > 0) & (image[:, 1] < size[1])
    world, image = world[inside], image[inside]
    assert len(world) >= 8
    wrong = 0
    for seed in range(100):
        noisy = image + np.random.default_rng(seed).normal(0, 1.0, image.shape)
        Gn, _ = cv2.findHomography(world, noisy, 0)
        fov, height = pitch.camera_geometry(Gn, size)
        wrong += fov is None or abs(fov - 70.0) > 10 or abs(height - 8.0) > 2
    assert wrong <= 3, wrong


def test_malformed_calibration_values_are_input_errors():
    from app.vision import calibrate

    good = [{"name": n, "x": 100.0 + 50 * k, "y": 100.0 + 20 * k} for k, n in enumerate(["corner-far-left", "corner-far-right", "halfway-near", "centre-spot"])]
    for request in (
        {"template": "futsal", "points": [{"name": ["a"], "x": 1, "y": 1}] + good},
        {"template": "futsal", "points": good, "lines": [{"line": {"x": 1}, "x": 1, "y": 1}]},
        {"template": "futsal", "points": [{**good[0], "x": 10**400}] + good[1:]},
        {"template": "futsal", "points": good, "t": 10**400},
        {"template": {"length": 10**400, "width": 20}, "points": good},
    ):
        with pytest.raises(ValueError):
            calibrate.validate_request(request, SIZE)
