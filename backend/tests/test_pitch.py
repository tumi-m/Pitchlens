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
    # _look_at's world is right-handed with z up: seen from the near side its x
    # grows to the left. Re-express it in pitch axes (x grows to the right).
    G = G @ np.array([[-1, 0, TEMPLATE["length"]], [0, 1, 0], [0, 0, 1.0]])
    assert pitch.camera_pose(G, size, [[20.0, 10.0]])[2] is False
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


# ── Line masks, re-acquisition, mirrored names, resolution, fit checks ─────
def test_bright_adverts_do_not_hide_faint_painted_lines():
    h, w = 360, 640
    frame = np.full((h, w, 3), (40, 140, 40), np.uint8)
    lines = np.zeros((h, w), np.uint8)
    for y in (120, 250, 340):
        cv2.line(lines, (20, y), (620, y), 255, 2)
    for x in (40, 320, 600):
        cv2.line(lines, (x, 110), (x, 345), 255, 2)
    frame[lines > 0] = (120, 170, 120)  # faint, low-saturation paint (top-hat about 36)
    # Advert boards above the pitch: bright lettering on a dark panel, 4% of the frame.
    frame[10:60, :] = (30, 30, 30)
    for x in range(0, w, 8):
        frame[15:55, x:x + 3] = (250, 250, 250)
    mask = pitch.line_mask(frame)
    assert (mask[lines > 0] > 0).mean() >= 0.6
    # Without the adverts the mask is the same as before.
    clean = frame.copy()
    clean[10:60, :] = (40, 140, 40)
    assert (pitch.line_mask(clean)[lines > 0] > 0).mean() >= 0.6


def test_re_acquisition_needs_a_score_close_to_the_clicked_views_own():
    from app.vision import calibrate

    assert calibrate.reacquire_threshold(0.70) <= 0.69  # real lines: a correct 0.69 lock is kept
    assert calibrate.reacquire_threshold(1.0) > 0.65  # a crisp court: a 0.65 false lock is not
    assert calibrate.reacquire_threshold(None) == 0.6 and calibrate.reacquire_threshold(0.5) == 0.6


def test_a_zoomed_re_aim_is_found_again_and_never_locked_onto_the_wrong_lines():
    from app.vision import calibrate

    H0 = camera_homography()
    cal = {"H": np.linalg.inv(H0).tolist(), "k1": 0.0, "size": list(SIZE)}
    own = pitch.line_mask(render(H0), restrict_to_grass=False)
    _, self_score = pitch.align_to_lines(np.dstack([own] * 3), cal, np.linalg.inv(H0), TEMPLATE, precomputed=own)
    threshold = calibrate.reacquire_threshold(self_score)
    probe = np.array([[x, y] for x in np.linspace(2, 38, 10) for y in np.linspace(2, 18, 5)])
    c = np.array([SIZE[0] / 2, SIZE[1] / 2])
    # The shift-only search locked onto the wrong lines (2-7 m off) for the first seven at score 0.61-0.65.
    re_aims = [(1.2, 80, -20), (1.15, 40, -20), (1.15, -40, -20), (1.15, 40, 0), (1.15, 80, 0), (1.15, -80, 0), (0.85, 80, 20),
               (1.0, 0, 0), (0.9, -40, 20), (1.1, 80, -20), (0.85, -80, -20)]
    for zoom, dx, dy in re_aims:
        Z = np.array([[zoom, 0, c[0] * (1 - zoom)], [0, zoom, c[1] * (1 - zoom)], [0, 0, 1]])
        moved = np.array([[1, 0, dx], [0, 1, dy], [0, 0, 1]]) @ Z @ H0
        mask = pitch.line_mask(render(moved), restrict_to_grass=False)
        refined, score = pitch.align_to_lines(np.dstack([mask] * 3), cal, np.linalg.inv(H0), TEMPLATE, precomputed=mask, search=int(min(SIZE) * 0.3))
        image = pitch.apply(moved, probe)
        inside = (image[:, 0] > 0) & (image[:, 0] < SIZE[0]) & (image[:, 1] > 0) & (image[:, 1] < SIZE[1])
        assert refined is not None and score >= threshold, (zoom, dx, dy, score)
        assert np.linalg.norm(pitch.apply(refined, image[inside]) - probe[inside], axis=1).max() < 1.0, (zoom, dx, dy)


def _pitch_camera(template, back=8.0, height=6.0, fov=80.0, size=SIZE):
    """A camera behind the near touchline, in pitch axes (x grows to the right)."""
    L, W = template["length"], template["width"]
    G = _look_at(np.array([L / 2, W + back, height]), [L / 2, W / 2 - 2, 0.0], fov, size)
    return G @ np.array([[-1, 0, L], [0, 1, 0], [0, 0, 1.0]])


def _visible_names(G, marks, size=SIZE):
    out = []
    for name, xy in marks.items():
        x, y = pitch.apply(G, [xy])[0]
        if np.isfinite(x) and 2 < x < size[0] - 2 and 2 < y < size[1] - 2:
            out.append(name)
    return out


def test_left_and_right_swapped_is_rated_poor_with_a_warning():
    five = pitch.normalise_template("five-a-side")
    G = _pitch_camera(five)
    marks = pitch.landmarks(five)
    names = _visible_names(G, marks)
    world = np.array([marks[n] for n in names])
    clicks = pitch.apply(G, world) + np.random.default_rng(0).normal(0, 0.7, world.shape)
    right = pitch.fit(clicks, world, SIZE, "auto")
    assert right["quality"] == "good" and not any("swapped" in w for w in right["warnings"])
    swap = {"left": "right", "right": "left"}
    mirrored = np.array([marks["-".join(swap.get(part, part) for part in n.split("-"))] for n in names])
    wrong = pitch.fit(clicks, mirrored, SIZE, "auto")
    assert wrong["rmsPixels"] == pytest.approx(right["rmsPixels"], abs=0.05)  # the same pixels fit just as well
    assert wrong["quality"] == "poor" and any(w.startswith("Left and right, or near and far, are swapped") for w in wrong["warnings"])
    assert wrong["cameraHeight"] > 0  # still printed as a height


def test_near_and_far_swapped_behind_a_goal_is_rated_poor_with_a_warning():
    five = pitch.normalise_template("five-a-side")
    L, W = five["length"], five["width"]
    # Behind the left goal, facing along the pitch: the near touchline is on the right.
    G = _look_at(np.array([-6.0, W / 2, 5.0]), [L * 0.45, W / 2, 0.0], 85.0, SIZE) @ np.array([[1, 0, 0], [0, -1, W], [0, 0, 1.0]])
    marks = pitch.landmarks(five)
    names = _visible_names(G, marks)
    world = np.array([marks[n] for n in names])
    clicks = pitch.apply(G, world) + np.random.default_rng(1).normal(0, 0.7, world.shape)
    right = pitch.fit(clicks, world, SIZE, "auto")
    assert right["quality"] != "poor" and not any("swapped" in w for w in right["warnings"])
    swap = {"near": "far", "far": "near"}
    flipped = np.array([marks["-".join(swap.get(part, part) for part in n.split("-"))] for n in names])
    wrong = pitch.fit(clicks, flipped, SIZE, "auto")
    assert wrong["quality"] == "poor" and any("swapped" in w for w in wrong["warnings"])


def test_top_down_clicks_are_never_called_mirrored():
    marks = pitch.landmarks(TEMPLATE)
    names = ["corner-far-left", "corner-far-right", "corner-near-right", "corner-near-left", "centre-spot", "halfway-far"]
    world = np.array([marks[n] for n in names])
    clicks = world * 12 + [80, 60]  # an affine (top-down) view: no camera height to read
    cal = pitch.fit(clicks, world, SIZE, "none")
    assert pitch.camera_pose(np.linalg.inv(np.asarray(cal["H"])), SIZE, world)[2] is None
    assert cal["quality"] == "good" and not any("swapped" in w for w in cal["warnings"])


def _fixed_camera_motion(width, frames=167, seed=4):
    """Motion noise of a fixed 1080p stadium camera (p95 about 0.6 px), at another width."""
    rng = np.random.default_rng(seed)
    scale = width / 1920
    return [
        {"t": i / 2.78, "scene": 0, "camera": None if i == 0 else [1, 0, float(rng.normal(0, 0.23) * scale), 0, 1, float(rng.normal(0, 0.15) * scale)]}
        for i in range(frames)
    ]


def test_a_fixed_4k_camera_is_static_and_a_panning_one_is_not():
    frames = _fixed_camera_motion(3840)
    assert not pitch.static_camera(frames)  # the 640-px limits read 4K noise as movement
    assert pitch.static_camera(frames, width=3840)
    H = np.linalg.inv(camera_homography())
    homographies, _ = pitch.propagate(frames, {80: H}, width=3840)
    assert all(h is not None and np.allclose(h, H) for h in homographies)
    panning = [{"t": i / 5, "scene": 0, "camera": None if i == 0 else [1, 0, -3.0, 0, 1, 0]} for i in range(100)]
    assert not pitch.static_camera(panning)
    assert not pitch.static_camera([{**f, "camera": None if f["camera"] is None else [1, 0, -18.0, 0, 1, 0]} for f in panning], width=3840)


def _loop_residuals(G, k1, size, image_points, pitch_points, line_clicks):
    """The previous per-click loop, kept as the reference for the vectorised one."""
    out = []
    if len(image_points):
        out.append((pitch.distort(pitch.apply(G, pitch_points), k1, size) - image_points).ravel())
    if line_clicks:
        clicks = pitch.undistort(np.array([c[0] for c in line_clicks]), k1, size)
        for (a, b), click in zip([c[1] for c in line_clicks], clicks):
            pa, pb = pitch.apply(G, [a, b])
            direction = pb - pa
            norm = np.linalg.norm(direction)
            if not np.isfinite(norm) or norm < 1e-9:
                out.append(np.array([1e3]))
                continue
            normal = np.array([-direction[1], direction[0]]) / norm
            out.append(np.array([float(np.dot(click - pa, normal))]))
    return np.concatenate(out) if out else np.zeros(0)


def test_vectorised_residuals_equal_the_per_click_loop():
    rng = np.random.default_rng(1)
    lines = list(pitch.straight_lines(TEMPLATE).values())
    for trial in range(200):
        G = np.append(rng.normal(0, 1, 8), 1).reshape(3, 3)
        if trial % 7 == 0:
            G[2, :2] = 0  # affine
        if trial % 11 == 0:
            G[:, :2] = 0  # every line projects to a point
        k1 = float(rng.choice([0, -0.1, -0.5]))
        image, world = rng.uniform(0, 640, (6, 2)), rng.uniform(0, 40, (6, 2))
        clicks = [((float(rng.uniform(0, 640)), float(rng.uniform(0, 360))), lines[rng.integers(len(lines))]) for _ in range(rng.integers(0, 12))]
        a = _loop_residuals(G, k1, SIZE, image, world, clicks)
        b = pitch._residuals(G, k1, SIZE, image, world, clicks)
        assert np.array_equal(np.isfinite(a), np.isfinite(b))
        finite = np.isfinite(a)
        assert np.allclose(a[finite], b[finite], rtol=1e-12, atol=1e-9)


def test_correct_clicks_are_not_called_wrong_and_a_wrong_click_is_named():
    rng = np.random.default_rng(3)
    for name in ("futsal", "five-a-side", "seven-a-side"):
        template = pitch.normalise_template(name)
        G = _pitch_camera(template, back=6.0, height=7.0, fov=90.0)
        marks = pitch.landmarks(template)
        names = _visible_names(G, marks)
        world = np.array([marks[n] for n in names])
        assert len(world) >= 6, name
        for _ in range(5):
            clicks = pitch.apply(G, world) + rng.normal(0, 0.7, world.shape)
            cal = pitch.fit(clicks, world, SIZE, "none")
            assert not any("disagrees" in w for w in cal["warnings"]), (name, cal["warnings"])
            assert cal["leaveOneOut"] == [None] * len(world) and len(cal["z"]) == len(world)
        wrong = int(rng.integers(len(world)))
        clicks = pitch.apply(G, world) + rng.normal(0, 0.7, world.shape)
        clicks[wrong] += [32, -24]  # 40 px off
        cal = pitch.fit(clicks, world, SIZE, "none")
        assert any(w.startswith(f"Landmark {wrong + 1} disagrees") for w in cal["warnings"]), (name, wrong, cal["warnings"])


def test_a_landmark_nothing_else_checks_is_not_rated_good():
    five = pitch.normalise_template("five-a-side")
    G = _pitch_camera(five)
    marks = pitch.landmarks(five)
    names = ["corner-far-left", "corner-far-right", "halfway-near", "centre-spot"]
    world = np.array([marks[n] for n in names])
    clicks = pitch.apply(G, world)
    clicks[2] += [0, -80]  # halfway-near clicked 80 px too high (positions up to 10 m off)
    lines = pitch.straight_lines(five)
    a, b = np.array(lines["far-touchline"])
    rng = np.random.default_rng(3)
    line_clicks = [(tuple(pitch.apply(G, [a + (b - a) * s])[0] + rng.normal(0, 0.5, 2)), lines["far-touchline"]) for s in np.linspace(0.15, 0.85, 4)]
    cal = pitch.fit(clicks, world, SIZE, "auto", line_clicks)
    assert cal["quality"] != "good"
    assert any(w.startswith("Nothing else checks landmark") for w in cal["warnings"])


def test_a_worst_case_preview_is_several_times_cheaper(monkeypatch):
    import time

    from app.vision import calibrate

    size = (1920, 1080)
    template = pitch.normalise_template("eleven-a-side")
    marks = pitch.landmarks(template)
    G, _ = cv2.findHomography(np.array([[0, 0], [105, 0], [105, 68], [0, 68]], float), np.array([[200, 250], [1720, 250], [1900, 1000], [20, 1000]], float))
    rng = np.random.default_rng(0)
    points = [{"name": n, "x": float(x + rng.normal(0, 1)), "y": float(y + rng.normal(0, 1))} for n, (x, y) in ((n, pitch.apply(G, [marks[n]])[0]) for n in marks)]
    lines = pitch.straight_lines(template)
    clicks = []
    for k in range(80):
        name = list(lines)[k % len(lines)]
        a, b = np.array(lines[name])
        x, y = pitch.apply(G, [a + (b - a) * rng.uniform(0.05, 0.95)])[0]
        clicks.append({"line": name, "x": float(x + rng.normal(0, 1)), "y": float(y + rng.normal(0, 1))})
    result = {"frames": [{"t": 0.0}], "video": {"width": size[0], "height": size[1]}}
    request = {"template": "eleven-a-side", "points": points, "lines": clicks, "distortion": "auto"}
    assert len(points) == 23
    started = time.process_time()
    fast = calibrate.preview(result, request)
    now = time.process_time() - started
    # Before: residuals looped over the line clicks, and every landmark was left out in turn once more.
    monkeypatch.setattr(pitch, "_residuals", _loop_residuals)
    started = time.process_time()
    slow = calibrate.preview(result, request)
    template_, image, world, _, _, _, line_clicks, _ = calibrate.validate_request(request, size)
    pitch._leave_one_out(image, world, line_clicks, size, [slow["fit"]["k1"]], False)
    before = time.process_time() - started
    assert fast["fit"]["quality"] == slow["fit"]["quality"] == "good"
    assert before >= 3 * now, (before, now)


def test_lens_term_does_not_absorb_a_mis_clicked_corner():
    marks = pitch.landmarks(TEMPLATE)
    lines = pitch.straight_lines(TEMPLATE)
    H = camera_homography()
    world = np.array([marks[n] for n in ("corner-far-left", "corner-far-right", "corner-near-right", "corner-near-left")])

    def fit(off, k1=0.0):
        rng = np.random.default_rng(0)
        clicks = pitch.distort(pitch.apply(H, world), k1, SIZE) + rng.normal(0, 0.5, world.shape)
        clicks[2] += [off, -off / 2]
        touchline_clicks = []
        for name in ("far-touchline", "near-touchline"):
            a, b = np.array(lines[name])
            for s in (0.15, 0.3, 0.5, 0.7, 0.85):
                touchline_clicks.append((tuple(pitch.distort(pitch.apply(H, [a + (b - a) * s]), k1, SIZE)[0] + rng.normal(0, 0.5, 2)), lines[name]))
        return pitch.fit(clicks, world, SIZE, "auto", touchline_clicks)

    thirty = fit(30)
    assert thirty["k1"] == 0 and thirty["quality"] == "check"
    assert fit(45)["quality"] == "poor"
    lens = fit(0, k1=-0.15)
    assert abs(lens["k1"] + 0.15) < 0.03


def test_degenerate_clicks_get_a_clear_message():
    from app.vision import calibrate

    frames = [{"t": round(i / 3, 3), "scene": 0, "players": [], "ball": None, "camera": [1, 0, 0, 0, 1, 0] if i else None} for i in range(30)]
    result = {"frames": frames, "sampleFps": 3, "video": {"width": SIZE[0], "height": SIZE[1], "fps": 30}}
    names = ["corner-far-left", "corner-far-right", "corner-near-right", "corner-near-left", "centre-spot"]
    pairs = [{"name": n, "x": [100, 100, 500, 500, 300][k], "y": [100, 100, 300, 300, 200][k]} for k, n in enumerate(names)]
    in_a_line = [{"name": n, "x": 100 + 50 * k, "y": 100 + 20 * k} for k, n in enumerate(names)]
    for points in (pairs, in_a_line):
        with pytest.raises(ValueError, match="same spot or all clicks are in a line"):
            calibrate.preview(result, {"template": "futsal", "points": points})
        with pytest.raises(ValueError, match="same spot or all clicks are in a line"):
            calibrate.build(result, {"template": "futsal", "points": points, "t": 0})


def test_a_telephoto_camera_is_not_called_unreal():
    template = pitch.normalise_template("eleven-a-side")
    size = (1920, 1080)
    G = _look_at(np.array([30.0, 68 + 50.0, 22.0]), [16.0, 30.0, 0.0], 21.0, size) @ np.array([[-1, 0, 105], [0, 1, 0], [0, 0, 1.0]])
    marks = pitch.landmarks(template)
    names = _visible_names(G, marks, size)
    world = np.array([marks[n] for n in names])
    assert len(world) >= 5
    cal = pitch.fit(pitch.apply(G, world) + np.random.default_rng(2).normal(0, 1.0, world.shape), world, size, "none")
    assert cal["fieldOfView"] == pytest.approx(21, abs=3) and cal["cameraHeight"] == pytest.approx(22, abs=4)
    assert not any("real camera" in w for w in cal["warnings"])


def test_a_mirrored_setup_is_refused_with_the_reason():
    from app.vision import calibrate

    five = pitch.normalise_template("five-a-side")
    G = _pitch_camera(five)
    marks = pitch.landmarks(five)
    names = _visible_names(G, marks)
    swap = {"left": "right", "right": "left"}
    points = [{"name": "-".join(swap.get(part, part) for part in n.split("-")), "x": float(x), "y": float(y)}
              for n, (x, y) in ((n, pitch.apply(G, [marks[n]])[0]) for n in names)]
    frames = [{"t": round(i / 3, 3), "scene": 0, "players": [], "ball": None, "camera": [1, 0, 0, 0, 1, 0] if i else None} for i in range(30)]
    result = {"frames": frames, "sampleFps": 3, "video": {"width": SIZE[0], "height": SIZE[1], "fps": 30}}
    with pytest.raises(ValueError, match="are swapped"):
        calibrate.build(result, {"template": "five-a-side", "points": points, "t": 0})
