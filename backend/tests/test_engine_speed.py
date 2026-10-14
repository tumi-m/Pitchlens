"""Engine speed-ups must not change a single output value (round 6, engine speed)."""

from types import SimpleNamespace

import cv2
import numpy as np
import pytest


def _reference_pitch_colour(frame, mask):
    if mask is None or not mask.any():
        return None
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    return np.median(lab[mask > 0][::17], axis=0).astype(float)


def test_pitch_colour_converts_only_sampled_pixels_with_the_same_median():
    from app.vision.engine import field_mask, pitch_colour

    rng = np.random.default_rng(3)
    for _ in range(25):
        frame = rng.integers(0, 256, (90, 160, 3), dtype=np.uint8)
        frame[20:80, 10:150] = (40 + rng.integers(0, 30), 150, 40)  # grass with noise below
        frame[20:80, 10:150] += rng.integers(0, 12, (60, 140, 3), dtype=np.uint8)
        for mask in (field_mask(frame), (rng.random((90, 160)) > 0.5).astype(np.uint8) * 255, np.zeros((90, 160), np.uint8)):
            expected = _reference_pitch_colour(frame, mask)
            got = pitch_colour(frame, mask)
            assert (expected is None and got is None) or np.array_equal(expected, got)


def test_pitch_colour_edge_masks_match_the_full_frame_conversion():
    from app.vision.engine import pitch_colour

    rng = np.random.default_rng(5)
    frame = rng.integers(0, 256, (40, 60, 3), dtype=np.uint8)
    few = np.zeros((40, 60), np.uint8)
    few[3, [2, 9, 30]] = 255  # fewer set pixels than the stride of 17
    few[25, 59] = 255
    ones = np.ones((40, 60), np.uint8)  # a 0/1 mask, not 0/255
    for mask in (few, ones):
        assert np.array_equal(pitch_colour(frame, mask), _reference_pitch_colour(frame, mask))
    for mask in (np.zeros((40, 60), np.uint8), None):
        assert pitch_colour(frame, mask) is None and _reference_pitch_colour(frame, mask) is None


def test_histogram_percentile_matches_numpy_bit_for_bit():
    from app.vision.faint import percentile_u8

    rng = np.random.default_rng(0)
    arrays = [np.zeros((36, 64), np.uint8), np.full((36, 64), 255, np.uint8), np.array([[7]], np.uint8)]
    for n in (2, 3, 199, 200, 201, 1001, 230400):
        arrays.append(rng.integers(0, 256, n).astype(np.uint8))
        arrays.append(np.minimum(255, rng.exponential(8, n)).astype(np.uint8))
        arrays.append(rng.choice(np.array([0, 1, 24, 25, 255], np.uint8), n))
    for a in arrays:
        for q in (0, 12.5, 50, 99.5, 99.9, 100):
            assert percentile_u8(a, q) == np.percentile(a, q)
            assert int(percentile_u8(a, q)) == int(np.percentile(a, q))


def _textured(seed, shift=0):
    rng = np.random.default_rng(seed)
    base = cv2.GaussianBlur(rng.integers(0, 256, (240, 400, 3), dtype=np.uint8), (5, 5), 0)
    return np.roll(base, shift, axis=1)[:, 40:360].copy()


def test_camera_motion_reuses_the_previous_small_grey_picture():
    from app.vision.tracking import camera_motion, small_grey

    previous, current = _textured(1), _textured(1, shift=3)
    boxes = [[20, 30, 40, 90]]
    expected = camera_motion(previous, current, boxes)
    got = camera_motion(previous, current, boxes, small=(small_grey(previous), small_grey(current)))
    assert expected[1] == got[1] and np.array_equal(expected[0], got[0])


def test_difference_candidates_with_shared_region_and_grey_are_unchanged():
    from app.vision.engine import field_mask
    from app.vision.faint import difference_candidates

    previous = np.full((120, 200, 3), (40, 150, 40), np.uint8)
    current = previous.copy()
    cv2.circle(previous, (60, 60), 3, (255, 255, 255), -1)
    cv2.circle(current, (70, 62), 3, (255, 255, 255), -1)
    current[10:20, 150:190] = 250  # a moving board outside the dilated pitch is still masked the same way
    mask = field_mask(current)
    matrix = np.array([[1, 0, 0], [0, 1, 0]], np.float32)
    players = [{"box": [100, 30, 120, 90]}]
    k = max(3, int(current.shape[0] * 0.08))
    region = cv2.dilate(mask, np.ones((k, k), np.uint8))
    expected = difference_candidates(previous, current, matrix, players, 6, mask=mask)
    got = difference_candidates(
        previous, current, matrix, players, 6, mask=mask, region=region,
        grey=cv2.cvtColor(current, cv2.COLOR_BGR2GRAY),
    )
    assert expected == got and expected


def _moving_clip(tmp_path, frames=60, fps=10):
    video = tmp_path / "moving.avi"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), fps, (160, 96))
    for i in range(frames):
        frame = np.full((96, 160, 3), (40, 150, 40), np.uint8)
        cv2.rectangle(frame, (10 + i, 30), (22 + i, 70), (0, 0, 220), -1)
        cv2.rectangle(frame, (120 - i, 26), (132 - i, 68), (220, 0, 0), -1)
        cv2.circle(frame, (40 + 2 * i % 120, 80), 2, (255, 255, 255), -1)
        writer.write(frame)
    writer.release()
    return video


def _content_model(monkeypatch):
    """A detector that finds the red and blue rectangles in whatever frame it is given."""
    import torch

    class Model:
        names = {0: "person", 32: "sports ball"}

        def predict(self, frames, **options):
            out = []
            for frame in frames if isinstance(frames, list) else [frames]:
                boxes, scores, classes = [], [], []
                for channel in (2, 0):
                    ys, xs = np.nonzero(frame[:, :, channel] > 180)
                    ys, xs = ys[frame[ys, xs, 1] < 100], xs[frame[ys, xs, 1] < 100]
                    if len(xs):
                        boxes.append([xs.min(), ys.min(), xs.max() + 1, ys.max() + 1])
                        scores.append(0.9)
                        classes.append(0)
                out.append(SimpleNamespace(boxes=SimpleNamespace(
                    xyxy=torch.tensor(boxes, dtype=torch.float32).reshape(-1, 4),
                    conf=torch.tensor(scores, dtype=torch.float32),
                    cls=torch.tensor(classes, dtype=torch.float32),
                )))
            return out

    import ultralytics

    monkeypatch.setattr(ultralytics, "YOLO", lambda path: Model())
    return Model


@pytest.mark.parametrize("ball_search", ["exhaustive", "adaptive"])
def test_pipelined_inference_gives_the_same_result(tmp_path, monkeypatch, ball_search):
    pytest.importorskip("torch")
    pytest.importorskip("ultralytics")
    from app.vision import engine
    from app.vision.ball import TiledBallDetector

    _content_model(monkeypatch)
    weights = tmp_path / "model.pt"
    weights.write_bytes(b"test")
    monkeypatch.setattr(engine, "model_paths", lambda profile: (weights, weights))

    class Tiles(TiledBallDetector):
        def __init__(self):
            self.inference_calls = 0

        def _find(self, frame):
            ys, xs = np.nonzero((frame[:, :, 0] > 200) & (frame[:, :, 1] > 200) & (frame[:, :, 2] > 200))
            if not len(xs):
                return []
            x, y = float(xs.mean()), float(ys.mean())
            return [{"x": round(x, 1), "y": round(y, 1), "box": [x - 2, y - 2, x + 2, y + 2], "confidence": 0.6}]

        def detect(self, frame, threshold=0.05, focus=None):
            return self._find(frame)

        def detect_batch(self, frames, threshold=0.05):
            return [self._find(f) for f in frames]

    monkeypatch.setattr(engine, "create_ball_detector", lambda *args: Tiles())
    monkeypatch.setenv("VISION_BATCH", "4")
    clip = _moving_clip(tmp_path)
    results = []
    for pipe in ("0", "1"):
        monkeypatch.setenv("VISION_PIPELINE", pipe)
        result = engine.run_video(clip, tmp_path / f"r{pipe}.json", sample_fps=10, ball_search=ball_search)
        result.pop("performance")
        results.append(result)
    assert results[0] == results[1]
    assert any(f["ball"] for f in results[0]["frames"]) and any(f["players"] for f in results[0]["frames"])


def test_inference_error_on_the_helper_thread_reaches_the_caller(tmp_path, monkeypatch):
    pytest.importorskip("torch")
    pytest.importorskip("ultralytics")
    from app.vision import engine

    model = _content_model(monkeypatch)
    calls = []
    original = model.predict

    def failing(self, frames, **options):
        calls.append(1)
        if len(calls) > 3:  # after kit calibration, inside the main pass
            raise RuntimeError("CUDA out of memory")
        return original(self, frames, **options)

    monkeypatch.setattr(model, "predict", failing)
    weights = tmp_path / "model.pt"
    weights.write_bytes(b"test")
    monkeypatch.setattr(engine, "model_paths", lambda profile: (weights, weights))
    monkeypatch.setenv("VISION_BATCH", "4")
    monkeypatch.setenv("VISION_PIPELINE", "1")  # inference on the helper thread, even on the CPU
    with pytest.raises(RuntimeError, match="out of memory"):
        engine.run_video(_moving_clip(tmp_path), tmp_path / "r.json", sample_fps=10, ball_search="none")


@pytest.mark.parametrize("setting, pipelined", [(None, False), ("0", False), ("1", True)])
def test_pipeline_is_off_on_the_cpu_unless_forced(tmp_path, monkeypatch, setting, pipelined):
    # CPU and MPS workers keep the serial loop (neutral on the CPU, untested on MPS);
    # only CUDA overlaps by default, and VISION_PIPELINE=1 forces it on.
    pytest.importorskip("torch")
    pytest.importorskip("ultralytics")
    from app.vision import engine

    _content_model(monkeypatch)
    weights = tmp_path / "model.pt"
    weights.write_bytes(b"test")
    monkeypatch.setattr(engine, "model_paths", lambda profile: (weights, weights))
    monkeypatch.setenv("VISION_DEVICE", "cpu")
    monkeypatch.setenv("VISION_BATCH", "4")
    if setting is None:
        monkeypatch.delenv("VISION_PIPELINE", raising=False)
    else:
        monkeypatch.setenv("VISION_PIPELINE", setting)
    made = []
    real = engine.ThreadPoolExecutor

    def executor(*args, **kwargs):
        if not pipelined:
            raise AssertionError("the serial loop must not start a helper thread")
        made.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(engine, "ThreadPoolExecutor", executor)
    result = engine.run_video(_moving_clip(tmp_path), tmp_path / "r.json", sample_fps=10, ball_search="none")
    assert result["frames"] and made == ([1] if pipelined else [])


def test_both_worker_images_cap_openblas_at_one_thread():
    # Also covers what run_video's limit misses (pitch setup's small inversions).
    import re
    from pathlib import Path

    backend = Path(__file__).resolve().parents[1]
    dockerfile = (backend / "vision.Dockerfile").read_text()
    modal_image = (backend / "app" / "vision" / "modal_app.py").read_text()
    assert re.search(r"^\s*OPENBLAS_NUM_THREADS=1\s*$", dockerfile, re.M)
    assert '"OPENBLAS_NUM_THREADS": "1"' in modal_image
