"""Football-specific ONNX inference with standard letterbox/NMS decoding."""

import math
import os
from pathlib import Path

import cv2
import numpy as np


class BallDetector:
    def __init__(self, path):
        import onnxruntime as ort

        ort.disable_telemetry_events()
        options = ort.SessionOptions()
        from app.vision.engine import worker_threads

        options.intra_op_num_threads = worker_threads()
        options.inter_op_num_threads = 1
        # On a GPU worker (VISION_DEVICE=cuda) use CUDA; ONNX Runtime falls back
        # to the CPU provider if CUDA libraries are unavailable.
        providers = ["CPUExecutionProvider"]
        if os.getenv("VISION_DEVICE", "cpu").startswith("cuda"):
            providers.insert(0, "CUDAExecutionProvider")
        self.session = ort.InferenceSession(
            str(Path(path)), sess_options=options, providers=providers
        )
        self.input = self.session.get_inputs()[0]
        self.height, self.width = self.input.shape[2:]
        if not isinstance(self.height, int) or not isinstance(self.width, int):
            raise ValueError("Ball model must declare fixed input dimensions")

    def detect_batch(self, frames, threshold=0.2):
        return [self.detect(frame, threshold) for frame in frames]

    def detect(self, frame, threshold=0.2):
        height, width = frame.shape[:2]
        ratio = min(self.width / width, self.height / height)
        resized = cv2.resize(frame, (round(width * ratio), round(height * ratio)))
        left = (self.width - resized.shape[1]) // 2
        top = (self.height - resized.shape[0]) // 2
        canvas = np.full((self.height, self.width, 3), 114, np.uint8)
        canvas[top : top + resized.shape[0], left : left + resized.shape[1]] = resized
        data = (
            np.ascontiguousarray(canvas[:, :, ::-1].transpose(2, 0, 1)[None], dtype=np.float32)
            / 255
        )
        output = self.session.run(None, {self.input.name: data})[0][0].T
        if output.shape[1] != 5:
            raise ValueError("Ball model must output one-class YOLO detections")
        output = output[output[:, 4] >= threshold]
        if not len(output):
            return []
        boxes = np.column_stack(
            (
                output[:, 0] - output[:, 2] / 2,
                output[:, 1] - output[:, 3] / 2,
                output[:, 2],
                output[:, 3],
            )
        )
        indices = cv2.dnn.NMSBoxes(boxes.tolist(), output[:, 4].tolist(), threshold, 0.5)
        found = []
        for i in np.array(indices).reshape(-1):
            cx, cy, w, h, confidence = output[i]
            x = (cx - left) / ratio
            y = (cy - top) / ratio
            w /= ratio
            h /= ratio
            if not (0 <= x < width and 0 <= y < height):
                continue
            found.append(
                {
                    "x": round(float(x), 1),
                    "y": round(float(y), 1),
                    "box": [
                        round(float(x - w / 2), 1),
                        round(float(y - h / 2), 1),
                        round(float(x + w / 2), 1),
                        round(float(y + h / 2), 1),
                    ],
                    "confidence": round(float(confidence), 3),
                }
            )
        return found


class TiledBallDetector:
    """Local YOLO weights trained for tiles, including Roboflow's football example.

    Tiles are sized so a ball is always presented near the model's training
    scale: a 360p frame is split into two tiles that get upscaled, a 1080p
    frame into eight. All tiles of a batch of frames go through the model in
    one call, which is where a GPU earns its keep.
    """

    TILE = 480  # native pixels per tile side before resizing to imgsz 640
    OVERLAP = 48

    def __init__(self, path, device="cpu"):
        from ultralytics import YOLO

        self.model = YOLO(str(path))
        self.classes = [i for i, name in self.model.names.items() if "ball" in name.lower()]
        if not self.classes:
            raise ValueError("Ball model must contain a named ball class")
        self.device = device

    def tiles(self, frame):
        h, w = frame.shape[:2]
        cols = max(1, math.ceil(w / self.TILE))
        rows = max(1, math.ceil(h / self.TILE))
        tw, th = math.ceil(w / cols), math.ceil(h / rows)
        out = []
        for r in range(rows):
            for c in range(cols):
                x0 = max(0, c * tw - self.OVERLAP)
                y0 = max(0, r * th - self.OVERLAP)
                x1 = min(w, (c + 1) * tw + self.OVERLAP)
                y1 = min(h, (r + 1) * th + self.OVERLAP)
                out.append((x0, y0, frame[y0:y1, x0:x1]))
        return out

    def _predict(self, crops, threshold):
        self.inference_calls = getattr(self, "inference_calls", 0) + 1
        return self.model.predict(
            crops,
            imgsz=640,
            conf=threshold,
            classes=self.classes,
            device=self.device,
            quantize=16 if str(self.device).startswith("cuda") else None,
            verbose=False,
        )

    @staticmethod
    def _collect(result, x0, y0):
        out = []
        for box, confidence in zip(result.boxes.xyxy.tolist(), result.boxes.conf.tolist()):
            box = [box[0] + x0, box[1] + y0, box[2] + x0, box[3] + y0]
            out.append(
                {
                    "x": round((box[0] + box[2]) / 2, 1),
                    "y": round((box[1] + box[3]) / 2, 1),
                    "box": [round(v, 1) for v in box],
                    "confidence": round(confidence, 3),
                }
            )
        return out

    def detect_batch(self, frames, threshold=0.2):
        """Detect in several frames with one model call. Returns a list per frame."""
        crops, owners = [], []
        for index, frame in enumerate(frames):
            for x0, y0, tile in self.tiles(frame):
                crops.append(tile)
                owners.append((index, x0, y0))
        if not crops:
            return [[] for _ in frames]
        results = self._predict(crops, threshold)
        per_frame = [[] for _ in frames]
        for result, (index, x0, y0) in zip(results, owners):
            per_frame[index].extend(self._collect(result, x0, y0))
        return [self._suppress(found, threshold) for found in per_frame]

    def detect(self, frame, threshold=0.2, focus=None):
        """Single frame. With `focus` (last known ball position) the tile around
        it is searched first; a clear hit there saves the remaining tiles."""
        if focus is None:
            return self.detect_batch([frame], threshold)[0]
        h, w = frame.shape[:2]
        tiles = self.tiles(frame)

        def margin(tile):
            x0, y0, crop = tile
            th, tw = crop.shape[:2]
            return min(focus[0] - x0, x0 + tw - focus[0], focus[1] - y0, y0 + th - focus[1])

        selected = max(tiles, key=margin)
        found = []
        if margin(selected) >= min(h, w) * 0.08:
            x0, y0, crop = selected
            found = self._collect(self._predict([crop], threshold)[0], x0, y0)
            tiles.remove(selected)
            # One clear hit next to the last position settles it; weak candidates
            # (kept for track-before-detect) do not make the tile "ambiguous".
            strong = [f for f in found if f["confidence"] >= 0.35]
            if (
                len(strong) == 1
                and np.hypot(strong[0]["x"] - focus[0], strong[0]["y"] - focus[1]) < min(h, w) * 0.15
            ):
                return self._suppress(found, threshold)
        if tiles:
            results = self._predict([t[2] for t in tiles], threshold)
            for result, (x0, y0, _) in zip(results, tiles):
                found.extend(self._collect(result, x0, y0))
        return self._suppress(found, threshold)

    @staticmethod
    def _suppress(found, threshold):
        if not found:
            return []
        boxes = [
            [b["box"][0], b["box"][1], b["box"][2] - b["box"][0], b["box"][3] - b["box"][1]]
            for b in found
        ]
        keep = cv2.dnn.NMSBoxes(boxes, [b["confidence"] for b in found], threshold, 0.1)
        return [found[int(i)] for i in np.asarray(keep).reshape(-1)]


def create_ball_detector(path, device="cpu"):
    return BallDetector(path) if Path(path).suffix == ".onnx" else TiledBallDetector(path, device)
