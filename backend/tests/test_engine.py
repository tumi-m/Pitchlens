import math

from app.engine.events import EventDetector
from app.engine.config import EngineConfig
from app.engine.geometry import distance, estimate_homography, foot_point, toward_goal
from app.engine.teams import majority_team
from app.engine.track import FrameTracks, PlayerTracker, TrackedObject, _iou


def test_iou_identical():
    assert _iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1


def test_iou_disjoint():
    assert _iou((0, 0, 1, 1), (5, 5, 6, 6)) == 0


def test_foot_point():
    assert foot_point((10, 20, 30, 80)) == (20, 80)


def test_homography_square():
    H = estimate_homography([(0, 0), (100, 0), (100, 50), (0, 50)], [(0, 0), (42, 0), (42, 25), (0, 25)], 42, 25)
    assert H.valid
    x, y = H.image_to_pitch(50, 25)
    assert abs(x - 21) < 0.5 and abs(y - 12.5) < 0.5


def test_toward_goal():
    assert toward_goal((20, 12), (30, 12), (42, 12.5))
    assert not toward_goal((30, 12), (20, 12), (42, 12.5))


def test_majority_team_uncertain():
    assert majority_team(["home", "away", "home", "away"]) == "unknown"
    assert majority_team(["home", "home", "home", "away"]) == "home"


def test_event_pass_and_cooldown():
    det = EventDetector(EngineConfig(), calibrated=False)
    team = {1: "home", 2: "home"}
    a = FrameTracks(1, 1.0, [TrackedObject(1, (0, 0, 10, 10), 0.9, "player", (5, 10))], TrackedObject(0, (4, 8, 6, 10), 0.8, "ball", (5, 9)))
    b = FrameTracks(2, 1.8, [TrackedObject(2, (80, 0, 90, 10), 0.9, "player", (85, 10))], TrackedObject(0, (84, 8, 86, 10), 0.8, "ball", (85, 9)))
    det.observe(a, team, {1: (5, 10)}, (5, 9), "image")
    assert "pass" in [e.type for e in det.observe(b, team, {2: (85, 10)}, (85, 9), "image")]
    c = FrameTracks(3, 2.0, [TrackedObject(1, (0, 0, 10, 10), 0.9, "player", (5, 10))], TrackedObject(0, (4, 8, 6, 10), 0.8, "ball", (5, 9)))
    assert not any(e.type == "pass" for e in det.observe(c, team, {1: (5, 10)}, (5, 9), "image"))


def test_tracker_assigns_ids():
    from app.engine.detect import RawDetection
    tr = PlayerTracker(10)
    f1 = tr.update([RawDetection((10, 10, 30, 60), 0.9, "player", 0)], 0.1, 1)
    f2 = tr.update([RawDetection((12, 12, 32, 62), 0.9, "player", 0)], 0.2, 2)
    assert f2.players[0].track_id == f1.players[0].track_id


def test_distance():
    assert math.isclose(distance((0, 0), (3, 4)), 5)
