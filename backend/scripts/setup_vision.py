"""Download official model weights explicitly during setup, never during an upload."""

import hashlib
import urllib.request
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "models" / "yolo11s.pt"
path.parent.mkdir(exist_ok=True)
EXPECTED = "85a76fe86dd8afe384648546b56a7a78580c7cb7b404fc595f97969322d502d5"
if not path.exists():
    temp = path.with_suffix(".download")
    urllib.request.urlretrieve(
        "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11s.pt", temp
    )
    if hashlib.sha256(temp.read_bytes()).hexdigest() != EXPECTED:
        temp.unlink()
        raise RuntimeError("Downloaded model checksum mismatch")
    temp.replace(path)
if hashlib.sha256(path.read_bytes()).hexdigest() != EXPECTED:
    raise RuntimeError("Installed model checksum mismatch")
print(f"Model: {path}\nSHA256: {hashlib.sha256(path.read_bytes()).hexdigest()}")

ball = path.parent / "football-ball.onnx"
BALL_SHA = "9fd2031e5bced9dff47a48bae8c6809dd56493124ef8924ae28a3ce26a17a441"
if not ball.exists():
    temp = ball.with_suffix(".download")
    urllib.request.urlretrieve(
        "https://huggingface.co/acatorcini/yolov9-soccer-ball/resolve/b30df5abc9f3eda4a9d326d953be12b3541a14b4/v9b_best.onnx",
        temp,
    )
    if hashlib.sha256(temp.read_bytes()).hexdigest() != BALL_SHA:
        temp.unlink()
        raise RuntimeError("Downloaded ball model checksum mismatch")
    temp.replace(ball)
if hashlib.sha256(ball.read_bytes()).hexdigest() != BALL_SHA:
    raise RuntimeError("Installed ball model checksum mismatch")
print(f"Ball model: {ball}\nSHA256: {BALL_SHA}")
