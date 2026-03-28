import { Timestamp } from 'firebase/firestore';

// ── Auth / User ────────────────────────────────────────────────────────────
export interface UserProfile {
  id: string;
  email: string;
  displayName: string;
  photoURL: string;
  teams: string[];
  preferences: { theme: 'dark' | 'light' };
  role: 'player' | 'coach' | 'club-owner';
  createdAt: Timestamp;
}

// ── Team ───────────────────────────────────────────────────────────────────
export interface Player {
  id: string;
  name: string;
  jerseyColor: string;
  jerseyNumber?: number;
  position?: string;
}

export interface Team {
  id: string;
  userId: string;
  name: string;
  players: Player[];
  createdAt: Timestamp;
}

// ── Match Analytics ────────────────────────────────────────────────────────
export type MatchStatus = 'uploading' | 'processing' | 'completed' | 'error';

export interface MatchEvent {
  timestamp: number; // seconds from match start
  type:
    | 'goal'
    | 'shot'
    | 'shot_on_target'
    | 'pass'
    | 'foul'
    | 'corner'
    | 'possession_change'
    | 'pressure';
  teamSide: 'home' | 'away';
  playerId?: string;
  xG?: number;
  x?: number; // pitch coords (normalized 0–1)
  y?: number;
  description?: string;
}

export interface HeatmapPoint {
  x: number;
  y: number;
  intensity: number;
}

export interface PlayerHeatmap {
  playerId: string;
  teamSide: 'home' | 'away';
  positions: HeatmapPoint[];
}

export interface VoronoiFrame {
  frame: number;
  timestampSeconds: number;
  zones: Array<{ playerId: string; teamSide: 'home' | 'away'; area: number }>;
}

export interface PassNetwork {
  nodes: Array<{ playerId: string; name: string; teamSide: 'home' | 'away'; involvement: number; x: number; y: number }>;
  edges: Array<{ from: string; to: string; count: number; accuracy: number }>;
}

export interface MatchStats {
  score: { home: number; away: number };
  possession: { home: number; away: number };
  passes: { home: { completed: number; total: number; accuracy: number }; away: { completed: number; total: number; accuracy: number } };
  shots: { home: { total: number; onTarget: number; xG: number }; away: { total: number; onTarget: number; xG: number } };
  fouls: { home: number; away: number };
  corners: { home: number; away: number };
  pressureIndex: { home: number; away: number };
  momentumTimeline: Array<{ minute: number; home: number; away: number }>; // rolling possession
  events: MatchEvent[];
  heatmaps: PlayerHeatmap[];
  voronoi: VoronoiFrame[];
  passNetwork: PassNetwork;
  narrative: string; // AI-generated match summary
}

// ── Match Document ─────────────────────────────────────────────────────────
export interface Match {
  id: string;
  userId: string;
  title: string;
  homeTeamId?: string;
  awayTeamId?: string;
  homeTeamName: string;
  awayTeamName: string;
  homeTeamColor?: string;
  awayTeamColor?: string;
  videoUrls: string[];
  status: MatchStatus;
  errorMessage?: string;
  processingProgress?: number;
  stats?: MatchStats;
  duration?: number; // seconds
  createdAt: Timestamp;
  updatedAt: Timestamp;
}

// ── API ────────────────────────────────────────────────────────────────────
export interface ProcessMatchRequest {
  matchId: string;
  videoUrl: string;
  teamColors?: { home: string; away: string };
}

export interface ProcessMatchResponse {
  status: 'queued' | 'error';
  message?: string;
}
