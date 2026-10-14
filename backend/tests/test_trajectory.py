import copy

import numpy as np

from app.vision.trajectory import recover_near_anchors


def observation(x, confidence=.8, **kw):
    return {'x': x, 'y': 50., 'confidence': confidence, 'trackId': 7, 'observed': True, **kw}


def sequence():
    # Two uncertain candidates followed by two clear observations of a pass.
    frames = [
        {'t': 0., 'scene': 0, 'ball': observation(10, .4),
         'ballCandidates': [observation(40, .075), observation(10, .4)]},
        {'t': .2, 'scene': 0, 'ball': observation(10, .3),
         'ballCandidates': [observation(60, .12, source='motion'), observation(10, .3)]},
        {'t': .4, 'scene': 0, 'ball': observation(80), 'ballCandidates': []},
        {'t': .6, 'scene': 0, 'ball': observation(100), 'ballCandidates': []},
    ]
    return frames, [np.eye(2, 3)] * 4


def test_future_neural_anchors_recover_visible_pass_over_weak_distractor():
    frames, matrices = sequence()
    assert recover_near_anchors(frames, matrices, 734, 5) == 2
    assert [f['ball']['x'] for f in frames] == [40, 60, 80, 100]
    assert frames[0]['ball']['confidence'] == .075  # evidence is not inflated
    assert frames[1]['ball']['source'] == 'motion'
    assert all(f['ball'].get('observed') for f in frames)
    assert frames[0]['ball']['anchorTimes'] == [.4, .6]


def test_anchors_do_not_fill_empty_gaps_or_authenticate_unbracketed_motion():
    frames, matrices = sequence()
    frames[0]['ballCandidates'] = []
    assert recover_near_anchors(frames, matrices, 734, 5) == 0
    assert frames[1]['ball']['x'] == 10
    frames[0]['ball'] = None
    frames[1]['ball'] = None
    assert recover_near_anchors(frames, matrices, 734, 5) == 0
    assert frames[0]['ball'] is None and frames[1]['ball'] is None


def test_recovery_preserves_strong_detections_and_stops_at_cuts_or_unknown_motion():
    original, matrices = sequence()
    for variant in ('cut', 'missing-camera', 'strong-existing', 'still-seeds', 'different-tracks'):
        frames, camera = copy.deepcopy(original), copy.deepcopy(matrices)
        if variant == 'cut':
            frames[2]['scene'] = frames[3]['scene'] = 1
        elif variant == 'missing-camera':
            camera[2] = None
        elif variant == 'strong-existing':
            frames[0]['ball']['confidence'] = frames[1]['ball']['confidence'] = .9
        elif variant == 'still-seeds':
            frames[3]['ball']['x'] = 80
        else:
            frames[3]['ball']['trackId'] = 8
        assert recover_near_anchors(frames, camera, 734, 5) == 0, variant


def test_candidate_positions_follow_camera_pan_without_becoming_free_extrapolation():
    frames, matrices = sequence()
    pan = np.array([[1., 0., 10.], [0., 1., 0.]])
    for i, f in enumerate(frames):
        f['ball']['x'] += i * 10
        for c in f['ballCandidates']:
            c['x'] += i * 10
    assert recover_near_anchors(frames, [pan] * 4, 734, 5) == 2
    assert [f['ball']['x'] for f in frames] == [40, 70, 100, 130]


def test_a_panning_camera_does_not_protect_a_fixed_graphic():
    from app.vision.faint import drop_static_balls

    def ball(x, confidence=0.2, recovered=True):
        return {"x": x, "y": 40.0, "confidence": confidence, "trackId": 2, "recovered": recovered, "box": [x - 2, 38, x + 2, 42]}

    pan = np.array([[1.0, 0, 5.0], [0, 1.0, 0]])
    graphic = [{"scene": 0, "players": [], "ball": ball(50.0)} for _ in range(20)]
    assert drop_static_balls(graphic, [pan] * 20, 734, sample_fps=5) == 20
    # Motion was not estimated. The mark is still glued to the screen.
    unknown = [{"scene": 0, "players": [], "ball": ball(50.0)} for _ in range(20)]
    assert drop_static_balls(unknown, [None] * 20, 734, sample_fps=5) == 20
    # A confident ball that sits still on the pitch while the camera pans is kept
    # (four seconds is not a logo).
    carried = [{"scene": 0, "players": [], "ball": ball(50.0 + 5 * i, confidence=0.8, recovered=False)} for i in range(20)]
    assert drop_static_balls(carried, [pan] * 20, 734, sample_fps=5) == 0


def test_player_model_mark_must_move_on_the_screen_and_the_pitch():
    from app.vision.ball_tracking import BallTracker

    identity = np.eye(2, 3)
    pan = np.array([[1.0, 0, 30.0], [0, 1.0, 0]])
    shape = (360, 640)

    def logo(x):
        return {"x": x, "y": 52.0, "confidence": 0.8, "source": "player-model"}

    screen = BallTracker()
    assert screen.update([logo(77)], 0.0, identity, shape) is None
    assert screen.update([logo(77)], 0.2, pan, shape) is None
    pitch = BallTracker()
    assert pitch.update([logo(77)], 0.0, identity, shape) is None
    assert pitch.update([logo(107)], 0.2, pan, shape) is None
    moving = BallTracker()
    assert moving.update([logo(200)], 0.0, identity, shape) is None
    gone = moving.update([logo(270)], 0.2, pan, shape)
    assert gone is not None and gone["x"] == 270


def test_ball_search_does_not_follow_an_uncorroborated_player_model_mark():
    from app.vision.ball import search_focus

    matrix = np.array([[1.0, 0, 10.0], [0, 1.0, 0]])
    graphic = {"x": 77.0, "y": 52.0, "source": "player-model"}
    real = {"x": 200.0, "y": 150.0, "source": "detector"}
    assert search_focus(graphic, cut=False, motion_ok=True, since_sweep=0.1, matrix=matrix) is None
    focus = search_focus(real, cut=False, motion_ok=True, since_sweep=0.1, matrix=matrix)
    assert focus is not None and abs(focus[0] - 210) < 1e-6
    assert search_focus(real, cut=False, motion_ok=True, since_sweep=0.5, matrix=matrix) is None
    assert search_focus(real, cut=True, motion_ok=True, since_sweep=0.1, matrix=matrix) is None


def _ball_stages(candidate, players, every, seconds=10, fps=6.0):
    """Replay engine.run_video's ball stages in order on synthetic frames (still
    camera): online tracker with the aboveHeads flag, reject_static_candidates,
    reassociation, recover_near_anchors, recover_ball (static drop, bridge).
    Returns the published balls."""
    import math

    from app.vision.ball_tracking import BallTracker
    from app.vision.faint import recover_ball, reject_static_candidates, strong_candidates

    shape = (360, 640)
    diagonal = math.hypot(640, 360)
    tracker = BallTracker()
    frames, matrices = [], []
    for i in range(int(seconds * fps)):
        t = i / fps
        candidates = [dict(candidate)] if i % every == 0 else []
        strong = strong_candidates(candidates)
        ceiling = min(p['box'][1] for p in players) - 4
        for c in strong:
            if c['y'] < ceiling:
                c['aboveHeads'] = True
        ball = tracker.update(strong, t, np.eye(2, 3), shape, cut=False)
        frames.append({'t': t, 'frame': i, 'scene': 0, 'players': copy.deepcopy(players), 'ball': ball, 'ballCandidates': candidates})
        matrices.append(None if i == 0 else np.eye(2, 3))
    if reject_static_candidates(frames, matrices, diagonal, fps):
        tracker = BallTracker()
        for i, f in enumerate(frames):
            f['ball'] = tracker.update(strong_candidates(f['ballCandidates']), f['t'], np.eye(2, 3), shape, cut=False)
    recover_near_anchors(frames, matrices, diagonal, fps)
    recover_ball(frames, matrices, diagonal, fps)
    return [f['ball'] for f in frames]


HEADS = [{'id': k + 1, 'team': k % 2, 'role': 'player', 'box': [200 + 60 * k, 137, 216 + 60 * k, 177]} for k in range(6)]
FAR_PLAYERS = [{'id': k + 1, 'team': k % 2, 'role': 'player', 'box': [300 + 40 * k, 100, 316 + 40 * k, 140]} for k in range(6)]


def test_a_scoreboard_the_detector_sees_on_alternate_frames_is_never_the_ball():
    graphic = {'x': 77.2, 'y': 51.6, 'box': [75, 49, 79, 54], 'outsidePitch': False}
    for every, confidence in ((2, 0.5), (2, 0.82), (3, 0.5)):
        balls = _ball_stages({**graphic, 'confidence': confidence}, HEADS, every)
        at_graphic = [b for b in balls if b is not None and abs(b['x'] - 77.2) < 3 and abs(b['y'] - 51.6) < 3]
        assert at_graphic == [], (every, confidence)


def test_a_weak_unattended_spot_seen_on_alternate_frames_is_dropped():
    spot = {'x': 120.0, 'y': 300.0, 'box': [118, 298, 122, 302], 'confidence': 0.2, 'outsidePitch': False}
    assert all(b is None for b in _ball_stages(spot, FAR_PLAYERS, every=2))


def test_a_confident_dead_ball_on_the_pitch_is_kept_even_when_seen_on_alternate_frames():
    ball = {'x': 120.0, 'y': 300.0, 'box': [118, 298, 122, 302], 'confidence': 0.5, 'outsidePitch': False}
    assert sum(b is not None for b in _ball_stages(ball, FAR_PLAYERS, every=1)) == 60
    assert sum(b is not None for b in _ball_stages(ball, FAR_PLAYERS, every=2)) >= 58
