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
