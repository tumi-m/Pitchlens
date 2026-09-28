"""Football-specific ONNX inference with standard letterbox/NMS decoding."""

from pathlib import Path

import cv2
import numpy as np


class BallDetector:
    def __init__(self, path):
        import onnxruntime as ort

        ort.disable_telemetry_events()
        options = ort.SessionOptions()
        options.intra_op_num_threads = 4
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(Path(path)), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self.input = self.session.get_inputs()[0]
        self.height, self.width = self.input.shape[2:]
        if not isinstance(self.height, int) or not isinstance(self.width, int):
            raise ValueError("Ball model must declare fixed input dimensions")

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
    """Local YOLO weights trained for tiles, including Roboflow's football example."""

    def __init__(self, path, device="cpu"):
        from ultralytics import YOLO

        self.model = YOLO(str(path))
        self.classes = [i for i, name in self.model.names.items() if "ball" in name.lower()]
        if not self.classes:
            raise ValueError("Ball model must contain a named ball class")
        self.device = device

    def detect(self, frame, threshold=0.2, focus=None):
        h, w = frame.shape[:2]
        tiles = [
            (x, y, min(w, x + w // 2 + 50), min(h, y + h // 2 + 50))
            for y in sorted({0, max(0, h // 2 - 50)})
            for x in sorted({0, max(0, w // 2 - 50)})
        ]
        found = []

        def infer(tile):
            x, y, right, bottom = tile
            result = self.model.predict(
                frame[y:bottom, x:right],
                imgsz=640,
                conf=threshold,
                classes=self.classes,
                device=self.device,
                verbose=False,
            )[0]
            self.inference_calls = getattr(self, "inference_calls", 0) + 1
            detections = []
            for box, confidence in zip(result.boxes.xyxy.tolist(), result.boxes.conf.tolist()):
                box = [box[0] + x, box[1] + y, box[2] + x, box[3] + y]
                detections.append(
                    {
                        "x": (box[0] + box[2]) / 2,
                        "y": (box[1] + box[3]) / 2,
                        "box": box,
                        "confidence": confidence,
                    }
                )
            return detections

        if focus is not None:
            # Keep the training crop scale: select an existing tile instead of zooming
            # arbitrarily. A miss/ambiguous observation searches the remaining tiles.
            def margin(tile):
                x, y, right, bottom = tile
                return min(focus[0] - x, right - focus[0], focus[1] - y, bottom - focus[1])

            selected = max(tiles, key=margin)
            if margin(selected) >= min(h, w) * 0.08:
                found = infer(selected)
                tiles.remove(selected)
                if (
                    len(found) == 1
                    and found[0]["confidence"] >= 0.35
                    and np.hypot(found[0]["x"] - focus[0], found[0]["y"] - focus[1])
                    < min(h, w) * 0.15
                ):
                    return found
        for tile in tiles:
            found.extend(infer(tile))
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
