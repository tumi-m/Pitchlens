from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable, Optional

import cv2
import numpy as np

from app.engine.analytics import event_counts, occupancy_heatmap, possession_share, track_summaries
from app.engine.config import EngineConfig
from app.engine.detect import Detector
from app.engine.events import CandidateEvent, EventDetector
from app.engine.geometry import Homography, clip_pitch, estimate_homography
from app.engine.teams import assign_teams, swap_if_needed
from app.engine.track import FrameTracks, PlayerTracker

logger = logging.getLogger(__name__)
Progress = Callable[[int, str], None]


class LocalCVPipeline:
    def __init__(self, config: Optional[EngineConfig] = None, progress: Optional[Progress] = None):
        self.config = config or EngineConfig()
        self.progress = progress or (lambda _p, _m: None)

    def run(self, video_path: Path, landmarks=None, home_defends_left: bool = True, write_json=None) -> dict:
        video_path = Path(video_path)
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video: {video_path}")
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        nframes = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        duration = nframes / fps if fps else 0.0
        if duration > self.config.max_seconds:
            raise RuntimeError(f"Video is {duration:.0f}s; cap is {self.config.max_seconds:.0f}s for local runs.")
        self.progress(5, "Loading detector")
        detector = Detector(self.config)
        tracker = PlayerTracker(frame_rate=self.config.processed_fps(fps))
        H = self._homography(landmarks)
        space = "pitch" if H and H.valid else "image"
        frames: list[FrameTracks] = []
        crops: dict[int, list] = {}
        self.progress(10, f"Detecting with {detector.backend}")
        idx = processed = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            idx += 1
            if idx % max(1, self.config.frame_stride) != 0:
                continue
            t = idx / fps
            tracked = tracker.update(detector.infer(frame), timestamp=t, frame_index=idx)
            frames.append(tracked)
            for p in tracked.players:
                x1, y1, x2, y2 = map(int, p.xyxy)
                crop = frame[max(0, y1):max(0, y2), max(0, x1):max(0, x2)]
                bag = crops.setdefault(p.track_id, [])
                if crop.size and len(bag) < 12:
                    bag.append(crop.copy())
            processed += 1
            if processed % 40 == 0:
                self.progress(10 + min(60, int(60 * idx / max(1, nframes))), f"Tracked {processed} frames ({t:.1f}s)")
        cap.release()
        if not frames:
            raise RuntimeError("No frames were processed")
        self.progress(75, "Clustering kits")
        team_of = assign_teams(crops)
        mean_x = {}
        for f in frames:
            for p in f.players:
                mean_x.setdefault(p.track_id, []).append(p.foot[0])
        team_of = swap_if_needed(team_of, {k: float(np.mean(v)) for k, v in mean_x.items()}, home_defends_left)
        self.progress(82, "Deriving events")
        eventer = EventDetector(self.config, calibrated=space == "pitch")
        events: list[CandidateEvent] = []
        owners = []
        pitch_seq = []
        compact_frames = []
        for f in frames:
            positions = {}
            for p in f.players:
                if space == "pitch" and H:
                    positions[p.track_id] = clip_pitch(*H.image_to_pitch(*p.foot), self.config.pitch.length_m, self.config.pitch.width_m)
                else:
                    positions[p.track_id] = p.foot
            pitch_seq.append(positions)
            ball_pos = None
            if f.ball:
                ball_pos = H.image_to_pitch(*f.ball.foot) if space == "pitch" and H else f.ball.foot
            events.extend(eventer.observe(f, team_of, positions, ball_pos, space))
            owner = eventer.prev_owner
            owners.append("no_ball" if not f.ball else (owner[1] if owner else "unknown"))
            compact_frames.append({
                "t": round(f.timestamp, 3), "frame": f.frame_index,
                "players": [{
                    "id": p.track_id, "team": team_of.get(p.track_id, "unknown"),
                    "x": round(positions[p.track_id][0], 2), "y": round(positions[p.track_id][1], 2),
                    "bx": round(p.xyxy[0], 1), "by": round(p.xyxy[1], 1),
                    "bw": round(p.xyxy[2] - p.xyxy[0], 1), "bh": round(p.xyxy[3] - p.xyxy[1], 1),
                    "conf": round(p.confidence, 3),
                } for p in f.players],
                "ball": None if not f.ball else {
                    "x": round(ball_pos[0], 2) if ball_pos else None,
                    "y": round(ball_pos[1], 2) if ball_pos else None,
                    "bx": round(f.ball.xyxy[0], 1), "by": round(f.ball.xyxy[1], 1),
                    "bw": round(f.ball.xyxy[2] - f.ball.xyxy[0], 1), "bh": round(f.ball.xyxy[3] - f.ball.xyxy[1], 1),
                    "conf": round(f.ball.confidence, 3),
                },
            })
        length = self.config.pitch.length_m if space == "pitch" else float(width)
        width_p = self.config.pitch.width_m if space == "pitch" else float(height)
        payload = {
            "schemaVersion": 2, "provenance": "local-cv-candidates",
            "detector": detector.backend,
            "weights": self.config.yolo_weights if detector.backend == "ultralytics" else "roboflow",
            "space": space, "calibrated": space == "pitch",
            "homographyResidualPx": None if not H else H.residual_px,
            "video": {"path": str(video_path.name), "fps": fps, "duration": duration, "width": width, "height": height, "framesProcessed": len(frames), "stride": self.config.frame_stride},
            "pitch": {"kind": self.config.pitch.kind, "length": length, "width": width_p, "unit": "m" if space == "pitch" else "px"},
            "teams": team_of,
            "tracks": track_summaries(frames, team_of, pitch_seq, space),
            "possession": possession_share(frames, team_of, owners),
            "events": [e.to_dict() for e in events],
            "eventCounts": event_counts(events),
            "heatmaps": occupancy_heatmap(frames, team_of, pitch_seq, length, width_p) if space == "pitch" else {"home": [], "away": [], "note": "Heatmaps require pitch calibration."},
            "timeline": compact_frames,
            "limitations": [
                "Events are candidates. Confirm goals and shots on the video.",
                "Team labels come from jersey colour clustering and can flip.",
                "Uncalibrated coordinates are camera pixels, not metres.",
                "COCO weights miss many balls and cannot tell kits apart as well as a football model.",
                "Camera cuts, zooms and sideline footage break homography and IDs.",
            ],
        }
        if write_json:
            write_json = Path(write_json)
            write_json.parent.mkdir(parents=True, exist_ok=True)
            write_json.write_text(json.dumps(payload))
            logger.info("Wrote %s", write_json)
        self.progress(100, "Analysis complete")
        return payload

    def _homography(self, landmarks) -> Optional[Homography]:
        if not landmarks:
            return None
        image = [(float(i["ix"]), float(i["iy"])) for i in landmarks]
        pitch = [(float(i["px"]), float(i["py"])) for i in landmarks]
        return estimate_homography(image, pitch, self.config.pitch.length_m, self.config.pitch.width_m)
