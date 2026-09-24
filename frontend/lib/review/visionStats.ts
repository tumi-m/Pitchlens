import type { VisionFrame, VisionResult } from "./vision";

/** Same rule as backend/app/vision/metrics.py possession_owner. */
export function possessionOwner(frame: VisionFrame): number | null {
  const ball = frame.ball;
  if (!ball) return null;
  const candidates: [number, number][] = [];
  for (const p of frame.players) {
    if (p.team !== 0 && p.team !== 1) continue;
    const [x1, y1, x2, y2] = p.box;
    const scale = Math.max(y2 - y1, 1);
    const distance =
      Math.min(
        ...[x1, (x1 + x2) / 2, x2].map((x) => Math.hypot(ball.x - x, ball.y - y2)),
      ) / scale;
    if (distance <= 0.55) candidates.push([distance, p.team]);
  }
  candidates.sort((a, b) => a[0] - b[0]);
  if (!candidates.length) return null;
  if (candidates.length > 1 && candidates[1][0] - candidates[0][0] < 0.12) return null;
  return candidates[0][1];
}

export type TeamPair = [number, number];

export type MatchStats = {
  controlSeconds: TeamPair;
  controlShare: [number | null, number | null];
  controlCoverage: number;
  passCandidates: TeamPair;
  turnoversWon: TeamPair;
  avgPlayers: TeamPair;
  peakPlayers: TeamPair;
  playerCoverage: number;
  ballCoverage: number;
  /** Per-minute observed ball control, -1 (Kit B) … +1 (Kit A); null = no evidence. */
  momentum: (number | null)[];
  /** 0–10 composite of how much of the video the models could observe. */
  evidenceScore: number;
  minutes: number;
  possessions: [number | null, number | null];
  avgPossession: [number | null, number | null];
  longestPossession: [number | null, number | null];
  passesPerPossession: [number | null, number | null];
  passesAllowedPerRegain: [number | null, number | null];
  /** Plausible range for Kit A's control share given how few possessions were seen. */
  shareRange: [number, number] | null;
  /** Cumulative observed control seconds over time, per team: [t, A, B]. */
  cumulative: [number, number, number][];
};

/** Wilson score interval: small samples get honest, wide ranges. */
export function wilson(successes: number, n: number, z = 1.96): [number, number] {
  if (n <= 0) return [0, 100];
  const p = successes / n;
  const denom = 1 + (z * z) / n;
  const centre = (p + (z * z) / (2 * n)) / denom;
  const half = (z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n))) / denom;
  return [Math.round(Math.max(0, centre - half) * 100), Math.round(Math.min(1, centre + half) * 100)];
}

const pct = (a: number, b: number) => (b ? Math.round((a / b) * 1000) / 10 : 0);

export function matchStats(result: VisionResult): MatchStats {
  const m = result.metrics;
  const frames = result.frames;
  const count = (type: string, team: number) =>
    m.events.filter((e) => e.type === type && e.team === team).length;
  const players: TeamPair = [0, 0];
  const peak: TeamPair = [0, 0];
  for (const f of frames) {
    const n: TeamPair = [0, 0];
    for (const p of f.players) if (p.team === 0 || p.team === 1) n[p.team]++;
    players[0] += n[0];
    players[1] += n[1];
    peak[0] = Math.max(peak[0], n[0]);
    peak[1] = Math.max(peak[1], n[1]);
  }
  const minutes = Math.max(1, Math.ceil(result.analysedDuration / 60));
  const perMinute = Array.from({ length: minutes }, () => [0, 0] as TeamPair);
  for (const f of frames) {
    const owner = possessionOwner(f);
    if (owner === null) continue;
    perMinute[Math.min(minutes - 1, Math.floor(f.t / 60))][owner]++;
  }
  const momentum = perMinute.map(([a, b]) =>
    a + b >= 2 ? Math.round(((a - b) / (a + b)) * 100) / 100 : null,
  );
  const playerCoverage = pct(m.playerFrames, m.sampledFrames);
  const ballCoverage = pct(m.ballFrames, m.sampledFrames);
  const evidenceScore =
    Math.round(
      ((playerCoverage * 0.3 + ballCoverage * 0.4 + Math.min(100, m.possessionCoverage) * 0.3) /
        10) *
        10,
    ) / 10;
  const n = Math.max(1, frames.length);
  const pos = m.possessions;
  const pair = <K extends "count" | "averageSeconds" | "longestSeconds" | "passesPerPossession" | "passesAllowedPerRegain">(
    key: K,
  ): [number | null, number | null] =>
    pos ? [pos.teams[0]?.[key] ?? null, pos.teams[1]?.[key] ?? null] : [null, null];
  // Possessions, not frames, are the independent units: consecutive frames of
  // one spell are not separate evidence (sample-size lesson from the library).
  const chainCount = pos?.chains.length ?? 0;
  const aShare = m.teamSeconds[0] + m.teamSeconds[1] > 0 ? m.teamSeconds[0] / (m.teamSeconds[0] + m.teamSeconds[1]) : 0;
  const shareRange =
    chainCount >= 2 ? wilson(Math.round(aShare * chainCount), chainCount) : null;
  const cumulative: [number, number, number][] = [[0, 0, 0]];
  if (pos) {
    let a = 0;
    let b = 0;
    for (const c of pos.chains) {
      cumulative.push([c.start, a, b]);
      if (c.team === 0) a += c.controlSeconds;
      else b += c.controlSeconds;
      cumulative.push([c.end, a, b]);
    }
    cumulative.push([result.analysedDuration, a, b]);
  }
  return {
    controlSeconds: [m.teamSeconds[0] ?? 0, m.teamSeconds[1] ?? 0],
    controlShare: [m.possessionShare[0] ?? null, m.possessionShare[1] ?? null],
    controlCoverage: m.possessionCoverage,
    passCandidates: [count("pass-candidate", 0), count("pass-candidate", 1)],
    turnoversWon: [count("turnover-candidate", 0), count("turnover-candidate", 1)],
    avgPlayers: [Math.round((players[0] / n) * 10) / 10, Math.round((players[1] / n) * 10) / 10],
    peakPlayers: peak,
    playerCoverage,
    ballCoverage,
    momentum,
    evidenceScore,
    minutes,
    possessions: pair("count"),
    avgPossession: pair("averageSeconds"),
    longestPossession: pair("longestSeconds"),
    passesPerPossession: pair("passesPerPossession"),
    passesAllowedPerRegain: pair("passesAllowedPerRegain"),
    shareRange,
    cumulative,
  };
}
