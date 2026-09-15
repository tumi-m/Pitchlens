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
