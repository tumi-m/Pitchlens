"""Football-specific ONNX inference with standard letterbox/NMS decoding."""

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

    def detect(self, frame, threshold=0.2):
        h, w = frame.shape[:2]
        found = []
        # Four overlapping half-frame crops preserve the training scale at any resolution.
        for y in sorted({0, max(0, h // 2 - 50)}):
            for x in sorted({0, max(0, w // 2 - 50)}):
                tile = frame[y : min(h, y + h // 2 + 50), x : min(w, x + w // 2 + 50)]
                result = self.model.predict(
                    tile,
                    imgsz=640,
                    conf=threshold,
                    classes=self.classes,
                    device=self.device,
                    quantize=16 if str(self.device).startswith("cuda") else None,
                    verbose=False,
                )[0]
                for box, confidence in zip(result.boxes.xyxy.tolist(), result.boxes.conf.tolist()):
                    box = [box[0] + x, box[1] + y, box[2] + x, box[3] + y]
                    found.append(
                        {
                            "x": (box[0] + box[2]) / 2,
                            "y": (box[1] + box[3]) / 2,
                            "box": box,
                            "confidence": confidence,
                        }
                    )
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
