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
