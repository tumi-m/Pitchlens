import type { Detection } from "@/lib/review/types";
import type { TrackingFrame, TrackingPayload, TrackingPlayer } from "./types";

export interface HostedFrame {
  timestamp: number;
  width: number;
  height: number;
  predictions: Detection[];
}

type Box = { id: number; x1: number; y1: number; x2: number; y2: number; conf: number; name: string };

function mapClass(label: string): "player" | "ball" | null {
  const l = label.toLowerCase();
  if (l.includes("ball")) return "ball";
  if (l.includes("person") || l.includes("player") || l.includes("goal")) return "player";
  if (l.includes("ref")) return null;
  return "player";
}

function toBox(p: Detection, scaleX: number, scaleY: number): Box | null {
  const name = mapClass(p.class);
  if (!name) return null;
  const w = p.width * scaleX;
  const h = p.height * scaleY;
  const cx = p.x * scaleX;
  const cy = p.y * scaleY;
  return { id: 0, x1: cx - w / 2, y1: cy - h / 2, x2: cx + w / 2, y2: cy + h / 2, conf: p.confidence, name };
}

function iou(a: Box, b: Box): number {
  const x1 = Math.max(a.x1, b.x1);
  const y1 = Math.max(a.y1, b.y1);
  const x2 = Math.min(a.x2, b.x2);
  const y2 = Math.min(a.y2, b.y2);
  const inter = Math.max(0, x2 - x1) * Math.max(0, y2 - y1);
  const ua = Math.max(0, a.x2 - a.x1) * Math.max(0, a.y2 - a.y1);
  const ub = Math.max(0, b.x2 - b.x1) * Math.max(0, b.y2 - b.y1);
  const u = ua + ub - inter;
  return u ? inter / u : 0;
}

function trackPeople(frames: Box[][]): Box[][] {
  let next = 1;
  let prev: Box[] = [];
  return frames.map((people) => {
    const used = new Set<number>();
    const out: Box[] = [];
    for (const det of people) {
      let bestI = 0;
      let best: Box | undefined;
      for (const p of prev) {
        if (used.has(p.id)) continue;
        const v = iou(det, p);
        if (v > bestI) {
          bestI = v;
          best = p;
        }
      }
      const id = best && bestI >= 0.25 ? best.id : next++;
      if (best) used.add(id);
      out.push({ ...det, id });
    }
    prev = out;
    return out;
  });
}

function teamOf(tracks: { id: number; midX: number }[]): Record<number, "home" | "away" | "unknown"> {
  if (tracks.length < 2) return Object.fromEntries(tracks.map((t) => [t.id, "unknown"]));
  const xs = tracks.map((t) => t.midX).sort((a, b) => a - b);
  const mid = xs[Math.floor(xs.length / 2)];
  const out: Record<number, "home" | "away" | "unknown"> = {};
  for (const t of tracks) out[t.id] = t.midX <= mid ? "home" : "away";
  return out;
}

export function buildHostedTracking(
  samples: HostedFrame[],
  video: { duration: number; width: number; height: number },
): TrackingPayload {
  const peoplePer: Box[][] = [];
  const balls: (Box | null)[] = [];
  for (const s of samples) {
    const sx = video.width / Math.max(1, s.width);
    const sy = video.height / Math.max(1, s.height);
    const boxes = s.predictions.map((p) => toBox(p, sx, sy)).filter((b): b is Box => !!b);
    peoplePer.push(boxes.filter((b) => b.name === "player"));
    const ballCands = boxes.filter((b) => b.name === "ball");
    balls.push(ballCands.sort((a, b) => b.conf - a.conf)[0] ?? null);
  }
  const tracked = trackPeople(peoplePer);
  const means = new Map<number, number[]>();
  tracked.forEach((frame) => {
    for (const p of frame) {
      const bag = means.get(p.id) ?? [];
      bag.push((p.x1 + p.x2) / 2);
      means.set(p.id, bag);
    }
  });
  const teams = teamOf([...means.entries()].map(([id, xs]) => ({ id, midX: xs.reduce((a, b) => a + b, 0) / xs.length })));
  const timeline: TrackingFrame[] = samples.map((s, i) => {
    const players: TrackingPlayer[] = tracked[i].map((p) => ({
      id: p.id, team: teams[p.id] ?? "unknown", x: (p.x1 + p.x2) / 2, y: p.y2,
      bx: p.x1, by: p.y1, bw: p.x2 - p.x1, bh: p.y2 - p.y1, conf: p.conf,
    }));
    const b = balls[i];
    return {
      t: s.timestamp, frame: i, players,
      ball: b ? { x: (b.x1 + b.x2) / 2, y: (b.y1 + b.y2) / 2, bx: b.x1, by: b.y1, bw: b.x2 - b.x1, bh: b.y2 - b.y1, conf: b.conf } : null,
    };
  });
  const events: TrackingPayload["events"] = [];
  let prevOwner: { id: number; team: string } | null = null;
  let lastPass = -1e9;
  const owners: string[] = [];
  for (const f of timeline) {
    if (!f.ball || !f.players.length) { owners.push("no_ball"); prevOwner = null; continue; }
    const bx = f.ball.x ?? 0, by = f.ball.y ?? 0;
    let best = f.players[0], bestD = Infinity;
    for (const p of f.players) {
      const d = Math.hypot(p.x - bx, p.y - by);
      if (d < bestD) { bestD = d; best = p; }
    }
    const radius = Math.max(video.width, video.height) * 0.08;
    if (bestD > radius) { owners.push("unknown"); prevOwner = null; continue; }
    owners.push(best.team);
    if (prevOwner && prevOwner.id !== best.id && f.t - lastPass > 1.2) {
      const same = prevOwner.team === best.team && best.team !== "unknown";
      events.push({
        timestamp: f.t, type: same ? "pass" : "turnover", team: best.team, track_id: best.id,
        x: bx, y: by, confidence: 0.35, space: "image", needs_review: true,
        reason: `Nearest player changed ${prevOwner.id} → ${best.id}`,
      });
      lastPass = f.t;
    }
    prevOwner = { id: best.id, team: best.team };
  }
  const known = owners.filter((o) => o === "home" || o === "away").length || 1;
  const homeN = owners.filter((o) => o === "home").length;
  const awayN = owners.filter((o) => o === "away").length;
  return {
    schemaVersion: 2, provenance: "hosted-roboflow-candidates", detector: "roboflow", space: "image", calibrated: false,
    video: { fps: samples.length / Math.max(video.duration, 1), duration: video.duration, width: video.width, height: video.height, framesProcessed: samples.length, stride: 1 },
    pitch: { kind: "five-a-side", length: video.width, width: video.height, unit: "px" },
    teams,
    tracks: [...means.entries()].map(([id, xs]) => ({ trackId: id, team: teams[id] ?? "unknown", samples: xs.length, durationSec: video.duration, distance: 0, distanceUnit: "px", meanX: xs.reduce((a, b) => a + b, 0) / xs.length, meanY: 0 })),
    possession: {
      home: Math.round((1000 * homeN) / known) / 10, away: Math.round((1000 * awayN) / known) / 10,
      knownFrames: homeN + awayN, totalFrames: owners.length,
      ballVisibleRate: owners.filter((o) => o !== "no_ball").length / Math.max(1, owners.length),
      unknownRate: owners.filter((o) => o === "unknown").length / Math.max(1, owners.length),
      note: "Hosted Roboflow samples. Teams are left/right of frame. Possession only when a ball box exists.",
    },
    events,
    eventCounts: {
      home: { pass: events.filter((e) => e.team === "home" && e.type === "pass").length, turnover: events.filter((e) => e.team === "home" && e.type === "turnover").length },
      away: { pass: events.filter((e) => e.team === "away" && e.type === "pass").length, turnover: events.filter((e) => e.team === "away" && e.type === "turnover").length },
    },
    heatmaps: { home: [], away: [] },
    timeline,
    limitations: [
      "Hosted Roboflow frames, not a local GPU engine.",
      "Sparse samples. Fast actions can be missed.",
      "Team split is left/right of the camera, not jersey colour.",
      "Pixel coordinates. No pitch calibration.",
      "Events are candidates. Confirm on the video.",
    ],
  };
}

export function sampleTimes(duration: number, maxFrames = 40): number[] {
  const n = Math.max(4, Math.min(maxFrames, Math.floor(duration / 2) + 1));
  return Array.from({ length: n }, (_, i) => ((i + 1) / (n + 1)) * duration);
}
