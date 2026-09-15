from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

from app.engine.config import EngineConfig
from app.engine.geometry import distance, toward_goal
from app.engine.track import FrameTracks


@dataclass
class CandidateEvent:
    timestamp: float
    type: str
    team: str
    track_id: Optional[int]
    x: Optional[float]
    y: Optional[float]
    confidence: float
    space: str
    needs_review: bool
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


class EventDetector:
    def __init__(self, config: EngineConfig, calibrated: bool):
        self.config = config
        self.calibrated = calibrated
        self.prev_owner: Optional[tuple[int, str]] = None
        self.prev_ball: Optional[tuple[float, float, float]] = None
        self.last_shot_t = -1e9
        self.last_pass_t = -1e9

    def observe(self, frame, team_of, pitch_pos, ball_pos, space) -> list[CandidateEvent]:
        events: list[CandidateEvent] = []
        owner = self._owner(frame, team_of, pitch_pos, ball_pos, space)
        if owner and self.prev_owner and owner[0] != self.prev_owner[0]:
            same_team = owner[1] == self.prev_owner[1] and owner[1] in {"home", "away"}
            travel = distance(self.prev_ball[:2], ball_pos) if self.prev_ball and ball_pos else 0.0
            min_travel = self.config.pass_min_travel_m if space == "pitch" else self.config.pass_min_travel_px
            if same_team and travel >= min_travel and frame.timestamp - self.last_pass_t > 0.6:
                events.append(CandidateEvent(frame.timestamp, "pass", owner[1], owner[0], ball_pos[0] if ball_pos else None, ball_pos[1] if ball_pos else None, min(0.85, 0.4 + travel / (min_travel * 4)), space, True, f"ball moved {travel:.1f} from track {self.prev_owner[0]} to {owner[0]}"))
                self.last_pass_t = frame.timestamp
            elif not same_team and owner[1] in {"home", "away"}:
                events.append(CandidateEvent(frame.timestamp, "turnover", owner[1], owner[0], ball_pos[0] if ball_pos else None, ball_pos[1] if ball_pos else None, 0.45, space, True, "nearest player team changed while ball was visible"))
        if owner and ball_pos and self.prev_ball:
            dt = max(1e-3, frame.timestamp - self.prev_ball[2])
            vel = distance(self.prev_ball[:2], ball_pos) / dt
            goal = self._goal_for(owner[1], space)
            if goal and toward_goal(self.prev_ball[:2], ball_pos, goal):
                dist_goal = distance(ball_pos, goal)
                shot_speed = 8.0 if space == "pitch" else 250.0
                shot_range = 16.0 if space == "pitch" else 0.45 * max(goal)
                if vel > shot_speed and dist_goal < shot_range and frame.timestamp - self.last_shot_t > self.config.shot_cooldown_s:
                    events.append(CandidateEvent(frame.timestamp, "shot", owner[1], owner[0], ball_pos[0], ball_pos[1], min(0.8, 0.35 + vel / (shot_speed * 3)), space, True, f"ball accelerated toward goal (v={vel:.1f}, d={dist_goal:.1f})"))
                    self.last_shot_t = frame.timestamp
                    if dist_goal < (1.5 if space == "pitch" else 40.0):
                        events.append(CandidateEvent(frame.timestamp, "goal", owner[1], owner[0], ball_pos[0], ball_pos[1], 0.25, space, True, "ball entered goal-adjacent zone after a shot candidate — confirm on video"))
        self.prev_owner = owner
        self.prev_ball = (ball_pos[0], ball_pos[1], frame.timestamp) if ball_pos else None
        return events

    def _owner(self, frame, team_of, pitch_pos, ball_pos, space):
        if not ball_pos or not frame.players:
            return None
        radius = self.config.possession_radius_m if space == "pitch" else self.config.possession_radius_px
        best, best_d = None, radius
        for p in frame.players:
            d = distance(pitch_pos.get(p.track_id, p.foot), ball_pos)
            if d < best_d:
                best_d = d
                best = (p.track_id, team_of.get(p.track_id, "unknown"))
        return best

    def _goal_for(self, team, space):
        if space != "pitch":
            return None
        mid = self.config.pitch.width_m / 2
        if team == "home":
            return (self.config.pitch.length_m, mid)
        if team == "away":
            return (0.0, mid)
        return None
