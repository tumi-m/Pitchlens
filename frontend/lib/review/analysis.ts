import { visionJson } from "./vision";

/** Pitch presets; mirrors backend/app/vision/pitch.py PRESETS (the worker validates). */
export type PitchTemplate = {
  length: number;
  width: number;
  goalWidth: number;
  centreRadius: number | null;
  areaRadius: number | null;
  areaDepth: number | null;
  areaWidth: number | null;
  penaltySpot: number | null;
  walls?: boolean;
};

export const PITCH_PRESETS: Record<string, { label: string; template: PitchTemplate }> = {
  "five-a-side": {
    label: "5-a-side (cage or small pitch)",
    template: { length: 36, width: 24, goalWidth: 3.66, centreRadius: null, areaRadius: 6, areaDepth: null, areaWidth: null, penaltySpot: null },
  },
  futsal: {
    label: "Futsal / indoor court",
    template: { length: 40, width: 20, goalWidth: 3, centreRadius: 3, areaRadius: 6, areaDepth: null, areaWidth: null, penaltySpot: 6 },
  },
  "seven-a-side": {
    label: "7-a-side",
    template: { length: 55, width: 37, goalWidth: 3.66, centreRadius: 5.5, areaRadius: null, areaDepth: 9, areaWidth: 18, penaltySpot: 8 },
  },
  "eleven-a-side": {
    label: "11-a-side",
    template: { length: 105, width: 68, goalWidth: 7.32, centreRadius: 9.15, areaRadius: null, areaDepth: 16.5, areaWidth: 40.32, penaltySpot: 11 },
  },
};

export type Landmark = { name: string; label: string; x: number; y: number };

/** Clickable landmarks in pitch metres; mirrors pitch.landmarks(). */
export function landmarks(t: PitchTemplate): Landmark[] {
  const { length: L, width: W, goalWidth: g } = t;
  const out: Landmark[] = [
    { name: "corner-far-left", label: "Far-left corner", x: 0, y: 0 },
    { name: "corner-far-right", label: "Far-right corner", x: L, y: 0 },
    { name: "corner-near-right", label: "Near-right corner", x: L, y: W },
    { name: "corner-near-left", label: "Near-left corner", x: 0, y: W },
    { name: "halfway-far", label: "Halfway line, far touchline", x: L / 2, y: 0 },
    { name: "halfway-near", label: "Halfway line, near touchline", x: L / 2, y: W },
    { name: "centre-spot", label: "Centre spot", x: L / 2, y: W / 2 },
    { name: "left-goal-far-post", label: "Left goal, far post (base)", x: 0, y: W / 2 - g / 2 },
    { name: "left-goal-near-post", label: "Left goal, near post (base)", x: 0, y: W / 2 + g / 2 },
    { name: "right-goal-far-post", label: "Right goal, far post (base)", x: L, y: W / 2 - g / 2 },
    { name: "right-goal-near-post", label: "Right goal, near post (base)", x: L, y: W / 2 + g / 2 },
  ];
  if (t.centreRadius) {
    out.push({ name: "centre-circle-far", label: "Centre circle, far edge", x: L / 2, y: W / 2 - t.centreRadius });
    out.push({ name: "centre-circle-near", label: "Centre circle, near edge", x: L / 2, y: W / 2 + t.centreRadius });
  }
  if (t.penaltySpot) {
    out.push({ name: "left-penalty-spot", label: "Left penalty spot", x: t.penaltySpot, y: W / 2 });
    out.push({ name: "right-penalty-spot", label: "Right penalty spot", x: L - t.penaltySpot, y: W / 2 });
  }
  if (t.areaDepth && t.areaWidth) {
    const d = t.areaDepth;
    const b = t.areaWidth;
    out.push({ name: "left-area-far", label: "Left box, far corner", x: d, y: W / 2 - b / 2 });
    out.push({ name: "left-area-near", label: "Left box, near corner", x: d, y: W / 2 + b / 2 });
    out.push({ name: "right-area-far", label: "Right box, far corner", x: L - d, y: W / 2 - b / 2 });
    out.push({ name: "right-area-near", label: "Right box, near corner", x: L - d, y: W / 2 + b / 2 });
    out.push({ name: "left-area-far-goalline", label: "Left box meets goal line (far)", x: 0, y: W / 2 - b / 2 });
    out.push({ name: "left-area-near-goalline", label: "Left box meets goal line (near)", x: 0, y: W / 2 + b / 2 });
    out.push({ name: "right-area-far-goalline", label: "Right box meets goal line (far)", x: L, y: W / 2 - b / 2 });
    out.push({ name: "right-area-near-goalline", label: "Right box meets goal line (near)", x: L, y: W / 2 + b / 2 });
  }
  return out;
}

/** Painted lines as polylines in metres (for the mini pitch and the video overlay). */
export function pitchLines(t: PitchTemplate, step = 0.5): [number, number][][] {
  const { length: L, width: W } = t;
  const seg = (a: [number, number], b: [number, number]) => {
    const n = Math.max(2, Math.ceil(Math.hypot(b[0] - a[0], b[1] - a[1]) / step) + 1);
    return Array.from({ length: n }, (_, i) => [a[0] + ((b[0] - a[0]) * i) / (n - 1), a[1] + ((b[1] - a[1]) * i) / (n - 1)] as [number, number]);
  };
  const arc = (cx: number, cy: number, r: number, a0: number, a1: number) => {
    const n = Math.max(8, Math.ceil((Math.abs(a1 - a0) * r) / step) + 1);
    return Array.from({ length: n }, (_, i) => {
      const a = a0 + ((a1 - a0) * i) / (n - 1);
      return [cx + r * Math.cos(a), cy + r * Math.sin(a)] as [number, number];
    });
  };
  const lines = [seg([0, 0], [L, 0]), seg([L, 0], [L, W]), seg([L, W], [0, W]), seg([0, W], [0, 0]), seg([L / 2, 0], [L / 2, W])];
  if (t.centreRadius) lines.push(arc(L / 2, W / 2, t.centreRadius, 0, 2 * Math.PI));
  if (t.areaDepth && t.areaWidth) {
    const d = t.areaDepth;
    const b = t.areaWidth;
    for (const [x0, x1] of [[0, d], [L, L - d]]) {
      lines.push(seg([x0, W / 2 - b / 2], [x1, W / 2 - b / 2]), seg([x1, W / 2 - b / 2], [x1, W / 2 + b / 2]), seg([x1, W / 2 + b / 2], [x0, W / 2 + b / 2]));
    }
  } else if (t.areaRadius) {
    const r = Math.min(t.areaRadius, W / 2);
    lines.push(arc(0, W / 2, r, -Math.PI / 2, Math.PI / 2), arc(L, W / 2, r, Math.PI / 2, (3 * Math.PI) / 2));
  }
  return lines;
}

// ---------------------------------------------------------------- projection

export type Mat3 = number[]; // row-major 3x3

export function invert3(m: Mat3): Mat3 | null {
  const [a, b, c, d, e, f, g, h, i] = m;
  const A = e * i - f * h;
  const B = -(d * i - f * g);
  const C = d * h - e * g;
  const det = a * A + b * B + c * C;
  if (!Number.isFinite(det) || Math.abs(det) < 1e-15) return null;
  return [A, -(b * i - c * h), b * f - c * e, B, a * i - c * g, -(a * f - c * d), C, -(a * h - b * g), a * e - b * d].map((v) => v / det);
}

export function applyH(m: Mat3, x: number, y: number): [number, number] | null {
  const w = m[6] * x + m[7] * y + m[8];
  if (!Number.isFinite(w) || Math.abs(w) < 1e-12) return null;
  return [(m[0] * x + m[1] * y + m[2]) / w, (m[3] * x + m[4] * y + m[5]) / w];
}

/** Division-model lens distortion (closed form), matching pitch.distort(). */
export function distort(x: number, y: number, k1: number, size: [number, number]): [number, number] {
  if (!k1) return [x, y];
  const cx = size[0] / 2;
  const cy = size[1] / 2;
  const s = Math.hypot(size[0], size[1]) / 2;
  const ux = (x - cx) / s;
  const uy = (y - cy) / s;
  const ru = Math.hypot(ux, uy);
  if (ru === 0) return [x, y];
  const disc = 1 - 4 * k1 * ru * ru;
  const rd = Math.abs(k1 * ru) < 1e-12 ? ru : (1 - Math.sqrt(Math.max(0, disc))) / (2 * k1 * ru);
  const f = rd / ru;
  return [ux * f * s + cx, uy * f * s + cy];
}

/** Pitch metres -> image pixels for a frame, given its image->pitch homography. */
export function pitchToImage(H: Mat3, k1: number, size: [number, number], points: [number, number][]) {
  const inv = invert3(H);
  if (!inv) return [];
  const out: ([number, number] | null)[] = [];
  for (const [x, y] of points) {
    const p = applyH(inv, x, y);
    out.push(p ? distort(p[0], p[1], k1, size) : null);
  }
  return out;
}

// ---------------------------------------------------------------- types

export type CountStat = { value: number; confirmed: number; pending: number };

export type TeamStats = {
  controlSeconds: number;
  /** Team possession: won ball to lost ball, including passes in flight. */
  possessionSeconds: number;
  possession: number | null;
  possessions: number;
  averagePossession: number | null;
  passes: CountStat;
  passesComplete: CountStat;
  passAccuracy: number | null;
  /** Share of all detected passes: the pass-based possession definition, as a cross-check. */
  passShare?: number | null;
  shots: CountStat | null;
  shotsOnTarget: CountStat | null;
  goals: { value: number; candidates: number } | null;
  interceptions: CountStat;
  tackles: CountStat;
  /** Share of both teams' attacking-third control that was this team's. */
  fieldTilt: number | null;
};

export type AnalysisEvent = {
  id: string;
  type: string;
  t: number;
  tEnd?: number;
  team: number | null;
  confidence: number;
  status: "proposed" | "confirmed" | "rejected";
  outcome?: string;
  onTarget?: boolean;
  x?: number | null;
  y?: number | null;
  endX?: number | null;
  endY?: number | null;
  from?: number;
  to?: number;
  length?: number | null;
  speed?: number;
  distance?: number;
  ballSeen?: number;
  needsReview?: boolean;
  source?: string;
  note?: string;
  evidence?: string[];
  restartAt?: number;
};

export type Analysis = {
  schemaVersion: 1;
  calibrated: boolean;
  template: PitchTemplate | null;
  directions: { segments: { start: number; end: number | null; team0Attacks: "left" | "right" }[]; confidence: number; source: string } | null;
  events: AnalysisEvent[];
  stats: {
    teams: [TeamStats, TeamStats];
    coverage: {
      /** Share of in-play time assigned to a team's possession. */
      possessionPercent: number;
      possessionShown: boolean;
      /** 95% interval for the first team's possession share. */
      possessionInterval: [number, number] | null;
      controlPercent: number;
      ballStatePercent: number;
      calibratedPercent: number;
      inPlaySeconds: number;
      deadBallSeconds: number;
      contestedSeconds: number;
      looseSeconds: number;
      unknownSeconds: number;
    };
    momentum: (number | null)[];
    heatmaps: Record<string, number[][] | null> | null;
    averagePositions: { player: number; team: number; x: number; y: number; seconds: number }[] | null;
    shotMap: { id: string; team: number; x: number; y: number; outcome?: string; status: string; t: number }[] | null;
    tracks: { fragments: number; players: number };
    possessionSequences?: { team: number; start: number; end: number; seconds: number }[];
  };
  kickoffs?: { t: number; team: number }[];
  /** Final score typed by the reviewer: the authority for the scoreline. */
  enteredScore?: [number, number] | null;
  review: { decisions: number; confirmed: number; rejected: number; pending: number };
  /** [t, [[player, team, x, y]...] | null, [bx, by, inferred] | null] per sampled frame. */
  positions: [number, [number, number, number, number][] | null, [number, number, number] | null][] | null;
};

export type CalibrationFit = {
  H: number[][];
  k1: number;
  size: [number, number];
  /** Landmark error in metres (RMS). */
  rms: number;
  /** Click error in image pixels (RMS), the basis of the quality grade. */
  rmsPixels: number;
  quality: "good" | "check" | "poor" | "unverified";
  warnings: string[];
  fieldOfView: number | null;
  cameraHeight: number | null;
  lineResiduals: number[];
  residuals: { name: string; metres: number; pixels: number; leftOut: number | null }[];
};

export type Calibration = {
  state: "none" | "ready";
  template?: PitchTemplate;
  fit?: CalibrationFit;
  k1?: number;
  size?: [number, number];
  static?: boolean;
  coverage?: number;
  lineAligned?: number;
  reacquired?: number;
  anchorFrame?: number;
  frames?: ({ H: number[]; d: number } | null)[];
  request?: CalibrationRequest;
  job?: { state: "processing" | "done" | "failed"; progress?: number; error?: string };
};

export type CalibrationRequest = {
  template: PitchTemplate;
  points: { name: string; x: number; y: number }[];
  /** Clicks anywhere along a straight painted line. */
  lines?: { line: string; x: number; y: number }[];
  t: number;
  distortion?: "auto" | "none" | "on";
  walls?: boolean;
};

export type ReviewDecision =
  | { action: "accept" | "reject" | "reset"; eventId: string }
  | { action: "team"; eventId: string; value: 0 | 1 }
  | { action: "type" | "outcome"; eventId: string; value: string }
  | { action: "add"; type: string; t: number; team?: 0 | 1; outcome?: string; x?: number; y?: number }
  | { action: "direction"; value: "left" | "right" }
  | { action: "score"; value: [number, number] };

export const fetchAnalysis = (jobId: string, signal?: AbortSignal) =>
  visionJson<Analysis>(`jobs/${jobId}/analysis`, { signal });

export const fetchCalibration = (jobId: string, signal?: AbortSignal) =>
  visionJson<Calibration>(`jobs/${jobId}/calibration`, { signal });

const post = <T,>(path: string, body: unknown) =>
  visionJson<T>(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

export const previewCalibration = (jobId: string, request: CalibrationRequest) =>
  post<{ fit: CalibrationFit; template: PitchTemplate; lines: [number, number][][] }>(`jobs/${jobId}/calibration/preview`, request);

export const saveCalibration = (jobId: string, request: CalibrationRequest) =>
  post<{ state: string; fit: CalibrationFit }>(`jobs/${jobId}/calibration`, request);

export const sendReview = (jobId: string, decisions: ReviewDecision[]) =>
  post<{ decisions: number; analysis: Analysis }>(`jobs/${jobId}/review`, { decisions });

export const EVENT_LABELS: Record<string, string> = {
  pass: "Pass",
  shot: "Shot",
  goal: "Goal",
  "goal-candidate": "Possible goal",
  interception: "Interception",
  tackle: "Tackle / ball won",
  out: "Ball out",
  save: "Save",
  foul: "Foul",
  note: "Note",
};

export function describeEvent(e: AnalysisEvent): string {
  const base = EVENT_LABELS[e.type] || e.type;
  if (e.type === "pass") return `${base}${e.outcome === "intercepted" ? " (intercepted)" : ""}${e.length ? ` · ${e.length} m` : ""}`;
  // Ball speed stays internal: it is an unvalidated ground-plane estimate.
  if (e.type === "shot") return `${base} · ${(e.outcome || "").replace("-", " ")}`;
  return base;
}
