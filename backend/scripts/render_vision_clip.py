"""Render an evidence clip from saved inference results; never run a second model."""

import argparse
import bisect
import json
import subprocess
import tempfile
from pathlib import Path

import cv2

p = argparse.ArgumentParser()
p.add_argument("video")
p.add_argument("result")
p.add_argument("output")
p.add_argument("--start", type=float, default=120)
p.add_argument("--seconds", type=float, default=30)
a = p.parse_args()
if a.start < 0 or a.seconds <= 0:
    p.error("start must be nonnegative and seconds must be positive")
data = json.loads(Path(a.result).read_text())
timestamps = [f["t"] for f in data["frames"]]
width, height = data["video"]["width"], data["video"]["height"]
fps = data["video"]["fps"]
cap = cv2.VideoCapture(a.video)
cap.set(cv2.CAP_PROP_POS_MSEC, a.start * 1000)
with tempfile.TemporaryDirectory() as temp:
    path = Path(temp) / "raw.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    if not writer.isOpened():
        raise RuntimeError("Video encoder is unavailable")
    for i in range(int(a.seconds * fps)):
        t = a.start + i / fps
        ok, frame = cap.read()
        if not ok:
            break
        found = data["frames"][max(0, bisect.bisect_right(timestamps, t) - 1)]
        for player in found["players"]:
            box = list(map(int, player["box"]))
            hex_colour = (
                data["teams"][player["team"]]["colour"] if player["team"] >= 0 else "#cccccc"
            )
            rgb = tuple(int(hex_colour[n : n + 2], 16) for n in (1, 3, 5))
            colour = rgb[::-1]
            cv2.rectangle(frame, tuple(box[:2]), tuple(box[2:]), colour, 2)
            cv2.putText(
                frame,
                f"#{player['id']}",
                (box[0], max(12, box[1] - 4)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.4,
                colour,
                1,
                cv2.LINE_AA,
            )
        ball = found["ball"]
        if ball:
            cv2.circle(frame, (int(ball["x"]), int(ball["y"])), 8, (0, 255, 255), 2)
        cv2.rectangle(frame, (0, 0), (width, 24), (18, 18, 18), -1)
        cv2.putText(
            frame,
            f"Pitchlens CV | {int(t // 60):02}:{int(t % 60):02} | detections, not verified events",
            (8, 17),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            (235, 235, 235),
            1,
            cv2.LINE_AA,
        )
        writer.write(frame)
    writer.release()
    cap.release()
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(path),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            a.output,
        ],
        check=True,
    )
print(a.output)
