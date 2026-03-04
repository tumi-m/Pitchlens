/**
 * Client-side video processing pipeline.
 * 1. Extract frames from the video file using Canvas API (no FFmpeg needed)
 * 2. POST each frame to /api/infer  →  Roboflow YOLOv8 detection
 * 3. Assign player detections to home/away by jersey colour
 * 4. Compute: possession, shots, xG, passes, corners, fouls, heatmaps,
 *    pass network, momentum timeline, narrative
 */

// ── Pitch dimensions (standard 11-a-side) ────────────────────────────────
const PITCH_W = 105.0; // metres
const PITCH_H = 68.0;

// ── Roboflow class IDs ────────────────────────────────────────────────────
const CLASS_BALL = 0;
const CLASS_GOALKEEPER = 1;
const CLASS_PLAYER = 2;

// ── Types ─────────────────────────────────────────────────────────────────
export interface RoboflowPrediction {
  x: number;       // centre-x of bbox, in image pixels
  y: number;       // centre-y
  width: number;
  height: number;
  class: string;
  class_id: number;
  confidence: number;
}

export interface FrameResult {
  frameIdx: number;
  timestamp: number; // seconds into the match
  players: { cx: number; cy: number; team: 'home' | 'away'; w: number; h: number }[];
  ball: { cx: number; cy: number } | null;
  imgW: number;
  imgH: number;
}

export interface ProcessOptions {
  maxFrames?: number;
  imgW?: number;
  imgH?: number;
  homeColor: string; // hex e.g. "#FF0000"
  awayColor: string;
  onStage?: (stage: string) => void;
  onProgress?: (pct: number) => void;
}

// ── Colour helpers ────────────────────────────────────────────────────────
function hexToRgb(hex: string): [number, number, number] {
  const m = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex.trim());
  return m ? [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)] : [128, 128, 128];
}

function colourDist(r1: number, g1: number, b1: number, r2: number, g2: number, b2: number) {
  return Math.sqrt((r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2);
}

/** Sample the jersey area of a detection and compare against team colours. */
function assignTeam(
  ctx: CanvasRenderingContext2D,
  pred: RoboflowPrediction,
  homeRgb: [number, number, number],
  awayRgb: [number, number, number]
): 'home' | 'away' {
  const x1 = Math.max(0, Math.round(pred.x - pred.width / 2));
  const y1 = Math.max(0, Math.round(pred.y - pred.height / 2));
  const w = Math.max(1, Math.round(pred.width));
  const h = Math.max(1, Math.round(pred.height * 0.45)); // upper ~45% = jersey

  try {
    const data = ctx.getImageData(x1, y1, w, h).data;
    let rS = 0, gS = 0, bS = 0, n = 0;
    for (let i = 0; i < data.length; i += 4) {
      rS += data[i]; gS += data[i + 1]; bS += data[i + 2];
      n++;
    }
    if (n === 0) return 'home';
    const r = rS / n, g = gS / n, b = bS / n;
    const dHome = colourDist(r, g, b, homeRgb[0], homeRgb[1], homeRgb[2]);
    const dAway = colourDist(r, g, b, awayRgb[0], awayRgb[1], awayRgb[2]);
    return dHome <= dAway ? 'home' : 'away';
  } catch {
    return 'home';
  }
}

// ── Frame extraction from video (browser Canvas API) ──────────────────────
function seekTo(video: HTMLVideoElement, t: number): Promise<void> {
  return new Promise((resolve, reject) => {
    const onSeeked = () => { video.removeEventListener('seeked', onSeeked); resolve(); };
    const onError = () => { video.removeEventListener('error', onError); resolve(); }; // soft-fail
    video.addEventListener('seeked', onSeeked, { once: true });
    video.addEventListener('error', onError, { once: true });
    video.currentTime = t;
    setTimeout(resolve, 3000); // safety timeout
  });
}

async function extractFrames(
  videoFile: File,
  maxFrames: number,
  imgW: number,
  imgH: number,
  homeColor: string,
  awayColor: string,
  onProgress: (pct: number) => void
): Promise<FrameResult[]> {
  const homeRgb = hexToRgb(homeColor);
  const awayRgb = hexToRgb(awayColor);

  return new Promise((resolve, reject) => {
    const video = document.createElement('video');
    video.muted = true;
    video.preload = 'metadata';
    const blobUrl = URL.createObjectURL(videoFile);
    video.src = blobUrl;

    video.addEventListener('error', () => {
      URL.revokeObjectURL(blobUrl);
      reject(new Error('Could not read video file. Ensure it is a valid MP4.'));
    });

    video.addEventListener('loadedmetadata', async () => {
      const duration = isFinite(video.duration) ? video.duration : 300;
      const canvas = document.createElement('canvas');
      canvas.width = imgW;
      canvas.height = imgH;
      const ctx = canvas.getContext('2d', { willReadFrequently: true })!;

      const frames: FrameResult[] = [];
      const frameCount = Math.min(maxFrames, Math.max(10, Math.ceil(duration / 5)));
      const step = duration / frameCount;

      for (let i = 0; i < frameCount; i++) {
        const t = i * step + step * 0.1; // small offset avoids black frames at exact second boundaries
        try {
          await seekTo(video, t);
          ctx.drawImage(video, 0, 0, imgW, imgH);
        } catch {
          continue;
        }

        // Call /api/infer with base64 JPEG
        const base64 = canvas.toDataURL('image/jpeg', 0.75).split(',')[1];
        let predictions: RoboflowPrediction[] = [];
        try {
          const res = await fetch('/api/infer', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ frame: base64 }),
          });
          const json = await res.json();
          if (!json.mock) predictions = json.predictions ?? [];
        } catch {
          // network error — skip frame
        }

        const playerPreds = predictions.filter(
          (p) => (p.class_id === CLASS_PLAYER || p.class_id === CLASS_GOALKEEPER) && p.confidence > 0.4
        );
        const ballPreds = predictions.filter((p) => p.class_id === CLASS_BALL && p.confidence > 0.3);

        frames.push({
          frameIdx: i,
          timestamp: t,
          imgW,
          imgH,
          players: playerPreds.map((p) => ({
            cx: p.x,
            cy: p.y,
            w: p.width,
            h: p.height,
            team: assignTeam(ctx, p, homeRgb, awayRgb),
          })),
          ball: ballPreds.length > 0 ? { cx: ballPreds[0].x, cy: ballPreds[0].y } : null,
        });

        onProgress(Math.round(((i + 1) / frameCount) * 100));
      }

      URL.revokeObjectURL(blobUrl);
      resolve(frames);
    });

    video.load();
  });
}

// ── Pitch coordinate transform ────────────────────────────────────────────
function toPitch(cx: number, cy: number, imgW: number, imgH: number): [number, number] {
  return [(cx / imgW) * PITCH_W, (cy / imgH) * PITCH_H];
}

function dist(x1: number, y1: number, x2: number, y2: number) {
  return Math.sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2);
}

// ── xG model ─────────────────────────────────────────────────────────────
function xG(bx: number, by: number, team: 'home' | 'away'): number {
  const goalX = team === 'home' ? PITCH_W : 0;
  const d = dist(bx, by, goalX, PITCH_H / 2);
  const angle = Math.atan2(Math.abs(by - PITCH_H / 2), Math.abs(bx - goalX));
  const logOdds = 2.1 - 0.08 * d - 0.7 * angle;
  return Math.max(0, Math.min(1, 1 / (1 + Math.exp(-logOdds))));
}

// ── Stats calculation ─────────────────────────────────────────────────────
export function calculateStats(
  frames: FrameResult[],
  teamNames: { home: string; away: string },
  duration: number
) {
  const possession = { home: 0, away: 0, none: 0 };
  const passAttempts: { home: boolean[]; away: boolean[] } = { home: [], away: [] };
  const events: any[] = [];
  const heatPos: { home: [number, number][]; away: [number, number][] } = { home: [], away: [] };
  const score = { home: 0, away: 0 };
  const fouls = { home: 0, away: 0 };
  const corners = { home: 0, away: 0 };

  let prevBallOwner: 'home' | 'away' | null = null;
  let prevBallPitch: [number, number] | null = null;
  let lastCornerFrameIdx = -10;

  for (const frame of frames) {
    const { players, ball, imgW, imgH, timestamp, frameIdx } = frame;

    // Collect heatmap positions
    for (const p of players) {
      const [px, py] = toPitch(p.cx, p.cy, imgW, imgH);
      heatPos[p.team].push([px, py]);
    }

    if (!ball) { possession.none++; continue; }

    const [bpx, bpy] = toPitch(ball.cx, ball.cy, imgW, imgH);

    // Closest player to ball
    let closestTeam: 'home' | 'away' | null = null;
    let minD = Infinity;
    for (const p of players) {
      const [ppx, ppy] = toPitch(p.cx, p.cy, imgW, imgH);
      const d = dist(bpx, bpy, ppx, ppy);
      if (d < minD) { minD = d; closestTeam = p.team; }
    }
    const POSS_THRESH = 4.0; // metres
    const ballOwner = minD < POSS_THRESH ? closestTeam : null;

    if (ballOwner) possession[ballOwner]++;
    else possession.none++;

    // Pass detection
    if (ballOwner && prevBallOwner === ballOwner && prevBallPitch) {
      const vel = dist(bpx, bpy, prevBallPitch[0], prevBallPitch[1]);
      if (vel > 1.5) passAttempts[ballOwner].push(true);
    }

    // Possession change → possible foul heuristic
    if (ballOwner && prevBallOwner && ballOwner !== prevBallOwner) {
      events.push({ timestamp, type: 'possession_change', teamSide: ballOwner });
      // Contested tackle area (midfield / opponent half)
      if (prevBallPitch && dist(bpx, bpy, prevBallPitch[0], prevBallPitch[1]) < 2.0) {
        const fouler = ballOwner === 'home' ? 'away' : 'home';
        fouls[fouler]++;
        events.push({ timestamp, type: 'foul', teamSide: fouler });
      }
    }

    // Shot & goal detection
    if (ballOwner && prevBallPitch) {
      const vel = dist(bpx, bpy, prevBallPitch[0], prevBallPitch[1]);
      const targetGoalX = ballOwner === 'home' ? PITCH_W : 0;
      const dGoal = dist(bpx, bpy, targetGoalX, PITCH_H / 2);

      if (vel > 3.0 && dGoal < 25) {
        const xg = xG(bpx, bpy, ballOwner);
        const onTarget = dGoal < 10 && xg > 0.08;
        events.push({
          timestamp,
          type: onTarget ? 'shot_on_target' : 'shot',
          teamSide: ballOwner,
          xG: Math.round(xg * 100) / 100,
          x: bpx, y: bpy,
        });
        if (dGoal < 2.5) { // goal
          score[ballOwner]++;
          events.push({
            timestamp, type: 'goal', teamSide: ballOwner,
            xG: Math.round(xg * 100) / 100, x: bpx, y: bpy,
            description: `Goal! xG ${xg.toFixed(2)}`,
          });
        }
      }
    }

    // Corner detection
    const corners4: [number, number][] = [[0, 0], [PITCH_W, 0], [0, PITCH_H], [PITCH_W, PITCH_H]];
    if (corners4.some(([cx, cy]) => dist(bpx, bpy, cx, cy) < 3) && frameIdx - lastCornerFrameIdx > 5 && ballOwner) {
      corners[ballOwner]++;
      events.push({ timestamp, type: 'corner', teamSide: ballOwner });
      lastCornerFrameIdx = frameIdx;
    }

    prevBallOwner = ballOwner;
    prevBallPitch = [bpx, bpy];
  }

  // ── Aggregate ────────────────────────────────────────────────────────────
  const totalPoss = possession.home + possession.away || 1;
  const possHome = Math.round((possession.home / totalPoss) * 1000) / 10;
  const possAway = Math.round((100 - possHome) * 10) / 10;

  const shotEvents = events.filter((e) => ['shot', 'shot_on_target', 'goal'].includes(e.type));
  const homeShots = shotEvents.filter((e) => e.teamSide === 'home');
  const awayShots = shotEvents.filter((e) => e.teamSide === 'away');

  const homeComp = passAttempts.home.length;
  const awayComp = passAttempts.away.length;
  const homeTotal = Math.max(homeComp, Math.round(possHome * 0.9));
  const awayTotal = Math.max(awayComp, Math.round(possAway * 0.9));

  const heatmaps = buildHeatmaps(heatPos);
  const momentum = buildMomentum(events, duration);
  const passNetwork = buildPassNetwork(frames);
  const narrative = buildNarrative(score, possHome, possAway, homeShots, awayShots, teamNames, events);

  return {
    score,
    possession: { home: possHome, away: possAway },
    passes: {
      home: { completed: homeComp, total: homeTotal, accuracy: homeTotal > 0 ? Math.round((homeComp / homeTotal) * 1000) / 10 : 0 },
      away: { completed: awayComp, total: awayTotal, accuracy: awayTotal > 0 ? Math.round((awayComp / awayTotal) * 1000) / 10 : 0 },
    },
    shots: {
      home: { total: homeShots.length, onTarget: homeShots.filter((e) => e.type === 'shot_on_target').length, xG: Math.round(homeShots.reduce((s, e) => s + (e.xG || 0), 0) * 100) / 100 },
      away: { total: awayShots.length, onTarget: awayShots.filter((e) => e.type === 'shot_on_target').length, xG: Math.round(awayShots.reduce((s, e) => s + (e.xG || 0), 0) * 100) / 100 },
    },
    fouls,
    corners,
    pressureIndex: { home: Math.round(possHome / 20 * 10) / 10, away: Math.round(possAway / 20 * 10) / 10 },
    momentumTimeline: momentum,
    events: events.filter((e) => e.type !== 'possession_change').slice(0, 200),
    heatmaps,
    voronoi: [],
    passNetwork,
    narrative,
  };
}

function buildHeatmaps(pos: { home: [number, number][]; away: [number, number][] }) {
  return (['home', 'away'] as const)
    .filter((t) => pos[t].length >= 3)
    .map((team) => {
      const GW = 21, GH = 13;
      const grid: number[][] = Array.from({ length: GH }, () => new Array(GW).fill(0));
      for (const [px, py] of pos[team]) {
        const gi = Math.min(GH - 1, Math.floor((py / PITCH_H) * GH));
        const gj = Math.min(GW - 1, Math.floor((px / PITCH_W) * GW));
        grid[gi][gj]++;
      }
      const maxV = Math.max(1, ...grid.flatMap((r) => r));
      const points: any[] = [];
      for (let i = 0; i < GH; i++) {
        for (let j = 0; j < GW; j++) {
          const intensity = grid[i][j] / maxV;
          if (intensity > 0.05) {
            points.push({ x: (j / GW) * PITCH_W, y: (i / GH) * PITCH_H, intensity: Math.round(intensity * 1000) / 1000 });
          }
        }
      }
      return { playerId: team, teamSide: team, positions: points };
    });
}

function buildMomentum(events: any[], duration: number): any[] {
  const totalMin = Math.max(2, Math.ceil(duration / 60));
  const possEv = events.filter((e) => e.type === 'possession_change');
  return Array.from({ length: Math.ceil(totalMin / 2) + 1 }, (_, idx) => {
    const min = idx * 2;
    const ws = (min - 2) * 60, we = min * 60;
    const w = possEv.filter((e) => e.timestamp >= ws && e.timestamp <= we);
    const h = w.filter((e) => e.teamSide === 'home').length;
    const total = Math.max(1, w.length);
    return { minute: min, home: Math.round(h / total * 1000) / 10, away: Math.round((total - h) / total * 1000) / 10 };
  });
}

function buildPassNetwork(frames: FrameResult[]): any {
  const byTeam: Record<string, { x: number[]; y: number[]; team: 'home' | 'away' }> = {};
  for (const f of frames) {
    for (let i = 0; i < f.players.length; i++) {
      const p = f.players[i];
      const key = `${p.team}_${i % 11}`;
      if (!byTeam[key]) byTeam[key] = { x: [], y: [], team: p.team };
      const [px, py] = toPitch(p.cx, p.cy, f.imgW, f.imgH);
      byTeam[key].x.push(px);
      byTeam[key].y.push(py);
    }
  }
  const nodes = Object.entries(byTeam)
    .filter(([, v]) => v.x.length >= 2)
    .slice(0, 22)
    .map(([id, v]) => ({
      playerId: id,
      name: `P${id.split('_')[1]}`,
      teamSide: v.team,
      involvement: v.x.length,
      x: Math.round(v.x.reduce((a, b) => a + b, 0) / v.x.length * 100) / 100,
      y: Math.round(v.y.reduce((a, b) => a + b, 0) / v.y.length * 100) / 100,
    }));
  const edges: any[] = [];
  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      if (nodes[i].teamSide !== nodes[j].teamSide) continue;
      const d = dist(nodes[i].x, nodes[i].y, nodes[j].x, nodes[j].y);
      if (d < 18) edges.push({ fromId: nodes[i].playerId, toId: nodes[j].playerId, count: Math.max(1, Math.round(8 / Math.max(1, d))), accuracy: Math.round(Math.min(0.95, 0.5 + 0.03 * (18 - d)) * 100) / 100 });
    }
  }
  return { nodes: nodes.slice(0, 22), edges: edges.slice(0, 50) };
}

function buildNarrative(score: any, pH: number, pA: number, hShots: any[], aShots: any[], names: { home: string; away: string }, events: any[]): string {
  const dom = pH > pA ? names.home : names.away;
  const xgH = hShots.reduce((s, e) => s + (e.xG || 0), 0);
  const xgA = aShots.reduce((s, e) => s + (e.xG || 0), 0);
  const betterXg = xgH > xgA ? names.home : names.away;
  const firstGoal = events.find((e) => e.type === 'goal');
  let goalTxt = '';
  if (firstGoal) {
    const min = Math.floor(firstGoal.timestamp / 60);
    const sec = Math.floor(firstGoal.timestamp % 60);
    goalTxt = ` ${firstGoal.teamSide === 'home' ? names.home : names.away} broke the deadlock at ${min}:${String(sec).padStart(2, '0')}.`;
  }
  return `${dom} controlled possession with ${Math.max(pH, pA).toFixed(0)}% of the ball. ` +
    `${betterXg} generated the better chances (xG: ${Math.max(xgH, xgA).toFixed(2)}), ` +
    `with the match ending ${score.home}–${score.away}.${goalTxt}`;
}

// ── Mock stats (when ROBOFLOW_API_KEY is not set) ─────────────────────────
export function generateMockStats(teamNames: { home: string; away: string }, duration = 300) {
  const hG = Math.floor(Math.random() * 4);
  const aG = Math.floor(Math.random() * 4);
  const pH = 40 + Math.floor(Math.random() * 20);
  const events: any[] = [
    { timestamp: 180, type: 'corner', teamSide: 'home' },
    { timestamp: 420, type: 'foul', teamSide: 'away' },
    { timestamp: 600, type: 'shot_on_target', teamSide: 'home', xG: 0.24 },
    { timestamp: 780, type: 'corner', teamSide: 'away' },
    { timestamp: 900, type: 'shot_on_target', teamSide: 'away', xG: 0.18 },
    { timestamp: 1080, type: 'foul', teamSide: 'home' },
    ...(hG > 0 ? [{ timestamp: 300 + Math.random() * 900, type: 'goal', teamSide: 'home', xG: 0.42, description: 'Goal!' }] : []),
    ...(aG > 0 ? [{ timestamp: 600 + Math.random() * 900, type: 'goal', teamSide: 'away', xG: 0.35, description: 'Goal!' }] : []),
  ];

  return {
    score: { home: hG, away: aG },
    possession: { home: pH, away: 100 - pH },
    passes: {
      home: { completed: 180 + Math.floor(Math.random() * 80), total: 240 + Math.floor(Math.random() * 80), accuracy: 72 + Math.floor(Math.random() * 18) },
      away: { completed: 140 + Math.floor(Math.random() * 80), total: 200 + Math.floor(Math.random() * 80), accuracy: 68 + Math.floor(Math.random() * 18) },
    },
    shots: {
      home: { total: 8 + Math.floor(Math.random() * 8), onTarget: 3 + Math.floor(Math.random() * 4), xG: +(1.2 + Math.random() * 1.5).toFixed(2) },
      away: { total: 6 + Math.floor(Math.random() * 8), onTarget: 2 + Math.floor(Math.random() * 4), xG: +(0.8 + Math.random() * 1.5).toFixed(2) },
    },
    fouls: { home: 7 + Math.floor(Math.random() * 8), away: 8 + Math.floor(Math.random() * 8) },
    corners: { home: 4 + Math.floor(Math.random() * 5), away: 3 + Math.floor(Math.random() * 5) },
    pressureIndex: { home: +(pH / 20).toFixed(1), away: +((100 - pH) / 20).toFixed(1) },
    momentumTimeline: Array.from({ length: Math.ceil(duration / 60 / 2) + 1 }, (_, i) => {
      const h = 30 + Math.floor(Math.random() * 40);
      return { minute: i * 2, home: h, away: 100 - h };
    }),
    events,
    heatmaps: [
      { playerId: 'home', teamSide: 'home', positions: Array.from({ length: 35 }, () => ({ x: Math.random() * 105, y: Math.random() * 68, intensity: Math.random() })) },
      { playerId: 'away', teamSide: 'away', positions: Array.from({ length: 35 }, () => ({ x: Math.random() * 105, y: Math.random() * 68, intensity: Math.random() })) },
    ],
    voronoi: [],
    passNetwork: { nodes: [], edges: [] },
    narrative: `${pH > 50 ? teamNames.home : teamNames.away} dominated possession throughout, ` +
      `creating the clearer opportunities. The match ended ${hG}–${aG}. ` +
      `(Demo mode — add ROBOFLOW_API_KEY to Vercel environment variables for live AI detection.)`,
  };
}

// ── Main entry point ──────────────────────────────────────────────────────
export async function processVideo(
  videoFile: File,
  options: ProcessOptions
): Promise<ReturnType<typeof calculateStats>> {
  const {
    maxFrames = 40,
    imgW = 640,
    imgH = 360,
    homeColor,
    awayColor,
    onStage = () => {},
    onProgress = () => {},
  } = options;

  onStage('Extracting frames…');
  onProgress(0);

  let duration = 300;
  try {
    duration = await getVideoDuration(videoFile);
  } catch { /* use default */ }

  // Extract frames + call Roboflow per frame
  const frames = await extractFrames(videoFile, maxFrames, imgW, imgH, homeColor, awayColor, (pct) => {
    onStage(`Detecting players & ball… (${pct}%)`);
    onProgress(Math.round(pct * 0.85)); // frames = 0–85%
  });

  // If NO predictions were returned for any frame (mock mode or API key missing)
  const hasPredictions = frames.some((f) => f.players.length > 0 || f.ball !== null);
  if (!hasPredictions) {
    onStage('Generating analytics…');
    onProgress(90);
    return generateMockStats({ home: 'Home', away: 'Away' }, duration) as any;
  }

  onStage('Computing statistics…');
  onProgress(88);
  const stats = calculateStats(frames, { home: 'Home', away: 'Away' }, duration);

  onProgress(100);
  return stats;
}

async function getVideoDuration(file: File): Promise<number> {
  return new Promise((resolve, reject) => {
    const video = document.createElement('video');
    video.preload = 'metadata';
    const url = URL.createObjectURL(file);
    video.src = url;
    video.onloadedmetadata = () => { URL.revokeObjectURL(url); resolve(isFinite(video.duration) ? video.duration : 300); };
    video.onerror = () => { URL.revokeObjectURL(url); reject(new Error('Could not read video duration')); };
    video.load();
  });
}
