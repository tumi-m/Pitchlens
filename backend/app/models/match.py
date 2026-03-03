from pydantic import BaseModel, HttpUrl, field_validator
from typing import Optional, List
from enum import Enum


class TeamColors(BaseModel):
    home: str = "#FF0000"
    away: str = "#0000FF"


class ProcessMatchRequest(BaseModel):
    matchId: str
    videoUrl: str
    userId: Optional[str] = None
    teamColors: Optional[TeamColors] = None

    @field_validator("matchId")
    @classmethod
    def match_id_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("matchId cannot be empty")
        return v


class ProcessMatchResponse(BaseModel):
    status: str  # 'queued' | 'error'
    message: Optional[str] = None


class MatchStatus(str, Enum):
    UPLOADING = "uploading"
    PROCESSING = "processing"
    COMPLETED = "completed"
    ERROR = "error"


# ── Analytics output models ───────────────────────────────────────────────
class ScoreModel(BaseModel):
    home: int = 0
    away: int = 0


class PossessionModel(BaseModel):
    home: float = 50.0
    away: float = 50.0


class PassStatsModel(BaseModel):
    completed: int = 0
    total: int = 0
    accuracy: float = 0.0


class TeamPassStats(BaseModel):
    home: PassStatsModel
    away: PassStatsModel


class ShotStatsModel(BaseModel):
    total: int = 0
    onTarget: int = 0
    xG: float = 0.0


class TeamShotStats(BaseModel):
    home: ShotStatsModel
    away: ShotStatsModel


class MatchEvent(BaseModel):
    timestamp: float  # seconds from match start
    type: str  # goal, shot, shot_on_target, pass, foul, corner, possession_change, pressure
    teamSide: str  # home | away
    playerId: Optional[str] = None
    xG: Optional[float] = None
    x: Optional[float] = None
    y: Optional[float] = None
    description: Optional[str] = None


class HeatmapPoint(BaseModel):
    x: float
    y: float
    intensity: float


class PlayerHeatmap(BaseModel):
    playerId: str
    teamSide: str
    positions: List[HeatmapPoint]


class VoronoiZone(BaseModel):
    playerId: str
    teamSide: str
    area: float


class VoronoiFrame(BaseModel):
    frame: int
    timestampSeconds: float
    zones: List[VoronoiZone]


class PassNetworkNode(BaseModel):
    playerId: str
    name: str
    teamSide: str
    involvement: int
    x: float
    y: float


class PassNetworkEdge(BaseModel):
    fromId: str
    toId: str
    count: int
    accuracy: float


class PassNetwork(BaseModel):
    nodes: List[PassNetworkNode]
    edges: List[PassNetworkEdge]


class MomentumPoint(BaseModel):
    minute: int
    home: float
    away: float


class PressureIndex(BaseModel):
    home: float
    away: float


class MatchAnalytics(BaseModel):
    score: ScoreModel
    possession: PossessionModel
    passes: TeamPassStats
    shots: TeamShotStats
    fouls: ScoreModel
    corners: ScoreModel
    pressureIndex: PressureIndex
    momentumTimeline: List[MomentumPoint]
    events: List[MatchEvent]
    heatmaps: List[PlayerHeatmap]
    voronoi: List[VoronoiFrame]
    passNetwork: PassNetwork
    narrative: str
