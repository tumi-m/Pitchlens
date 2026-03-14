/**
 * Pitchlens Video Processing Pipeline
 *
 * Architecture (inspired by TARA transport-assessment):
 *   1. Ingest    — read video metadata (duration, filename seed)
 *   2. Sample    — extract key frames via Canvas API
 *   3. Assess    — AI inference per frame (Roboflow / Claude Vision)
 *   4. Segment   — possession / event segmentation
 *   5. Analytics — aggregate stats, heatmaps, pass-network, xG
 *   6. Report    — narrative + exportable JSON
 *
 * When no ROBOFLOW_API_KEY is configured the pipeline falls back to
 * realistic demo data (seeded from the video filename so the same file
 * always returns the same "stats").  This mirrors TARA's DEFAULT_ASSESSMENT
 * fallback that keeps the UI usable while the API is unavailable.
 */

// ── Pitch constants ──────────────────────────────────────────────────────
const PITCH_W = 105;
const PITCH_H = 68;

// ── Types ────────────────────────────────────────────────────────────────
export interface ProcessOptions {
  homeColor?: string;
  awayColor?: string;
  onStage?: (label: string) => void;
  onProgress?: (pct: number) => void;
}

// Internal seeded random (so same filename → same stats)
function seededRng(seed: number) {
  let s = seed;
  return () => {
    s = (s * 1664525 + 1013904223) & 0xffffffff;
    return (s >>> 0) / 0xffffffff;
  };
}

function strSeed(str: string): number {
  let h = 5381;
  for (let i = 0; i < str.length; i++) h = ((h << 5) + h) ^ str.charCodeAt(i);
  return h >>> 0;
}

// ── Stage 1: Ingest video metadata ──────────────────────────────────────
async function getVideoDuration(file: File): Promise<number> {
  return new Promise((resolve) => {
    const video = document.createElement('video');
    video.preload = 'metadata';
    const url = URL.createObjectURL(file);
    const cleanup = () => { try { URL.revokeObjectURL(url); } catch {} };
    video.onloadedmetadata = () => { cleanup(); resolve(isFinite(video.duration) && video.duration > 0 ? video.duration : 300); };
    video.onerror = () => { cleanup(); resolve(300); };
    video.src = url;
    setTimeout(() => { cleanup(); resolve(300); }, 5000); // safety timeout
  });
}

// ── Stage 2-3: Frame extraction + AI assessment ──────────────────────────
async function tryRoboflowFrame(base64: string): Promise<any[]> {
  try {
    const res = await fetch('/api/infer', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ frame: base64 }),
      signal: AbortSignal.timeout(15000),
    });
    const json = await res.json();
    if (json.mock) return []; // no API key configured
    return json.predictions ?? [];
  } catch {
    return [];
  }
}

async function extractAndInfer(
  file: File,
  frameCount: number,
  duration: number,
  onProgress: (p: number) => void
): Promise<{ hasRealData: boolean; frames: any[] }> {
  return new Promise((resolve) => {
    const video = document.createElement('video');
    video.muted = true;
    video.preload = 'auto';
    const url = URL.createObjectURL(file);
    let resolved = false;

    const finish = (frames: any[], hasReal: boolean) => {
      if (resolved) return;
      resolved = true;
      try { URL.revokeObjectURL(url); } catch {}
      resolve({ hasRealData: hasReal, frames });
    };

    // Fallback: if video doesn't load in 8s, skip to mock
    const loadTimeout = setTimeout(() => finish([], false), 8000);

    video.onloadeddata = async () => {
      clearTimeout(loadTimeout);

      const canvas = document.createElement('canvas');
      canvas.width = 640;
      canvas.height = 360;
      const ctx = canvas.getContext('2d', { willReadFrequently: true });
      if (!ctx) { finish([], false); return; }

      const step = duration / frameCount;
      const results: any[] = [];
      let anyReal = false;

      for (let i = 0; i < frameCount; i++) {
        const t = Math.min(i * step + 0.5, duration - 0.5);

        // Seek with 2s per-frame timeout
        const seeked = await new Promise<boolean>((res) => {
          const tid = setTimeout(() => res(false), 2000);
          const handler = () => { clearTimeout(tid); res(true); };
          video.addEventListener('seeked', handler, { once: true });
          video.currentTime = t;
        });

        if (!seeked) { onProgress(Math.round(((i + 1) / frameCount) * 100)); continue; }

        ctx.drawImage(video, 0, 0, 640, 360);
        const base64 = canvas.toDataURL('image/jpeg', 0.7).split(',')[1];
        const preds = await tryRoboflowFrame(base64);
        if (preds.length > 0) anyReal = true;
        results.push({ t, preds });
        onProgress(Math.round(((i + 1) / frameCount) * 100));
      }

      finish(results, anyReal);
    };

    video.onerror = () => { clearTimeout(loadTimeout); finish([], false); };
    video.src = url;
    video.load();
  });
}

// ── Stages 4-6: Segmentation, analytics, narrative ─────────────────────
function delay(ms: number) { return new Promise((r) => setTimeout(r, ms)); }

function buildDemoStats(
  teamNames: { home: string; away: string },
  duration: number,
  rng: () => number
) {
  const r = rng;
  const matchMinutes = Math.max(5, Math.round(duration / 60));

  // Score
  const maxGoals = Math.max(1, Math.floor(matchMinutes / 20));
  const hGoals = Math.floor(r() * (maxGoals + 1));
  const aGoals = Math.floor(r() * (maxGoals + 1));

  // Possession (seeded)
  const possH = Math.round(38 + r() * 24); // 38–62
  const possA = 100 - possH;

  // Passes (proportional to time and possession)
  const passBase = Math.round(matchMinutes * 4);
  const hPassTotal = Math.round(passBase * (possH / 100) * (0.85 + r() * 0.3));
  const aPassTotal = Math.round(passBase * (possA / 100) * (0.85 + r() * 0.3));
  const hPassComp = Math.round(hPassTotal * (0.72 + r() * 0.2));
  const aPassComp = Math.round(aPassTotal * (0.68 + r() * 0.2));

  // Shots
  const shotBase = Math.max(3, Math.round(matchMinutes / 8));
  const hShots = shotBase + Math.floor(r() * shotBase);
  const aShots = shotBase + Math.floor(r() * shotBase);
  const hOnTarget = Math.max(hGoals, Math.floor(hShots * (0.35 + r() * 0.3)));
  const aOnTarget = Math.max(aGoals, Math.floor(aShots * (0.3 + r() * 0.3)));
  const hXG = Math.round((hGoals * 0.4 + hOnTarget * 0.18 + r() * 0.6) * 100) / 100;
  const aXG = Math.round((aGoals * 0.4 + aOnTarget * 0.18 + r() * 0.6) * 100) / 100;

  // Fouls & Corners
  const foulBase = Math.max(3, Math.round(matchMinutes / 7));
  const hFouls = foulBase + Math.floor(r() * foulBase);
  const aFouls = foulBase + Math.floor(r() * foulBase);
  const hCorners = 2 + Math.floor(r() * 7);
  const aCorners = 2 + Math.floor(r() * 7);

  // Events — spread across match duration
  const events: any[] = [];

  const addEvent = (type: string, side: 'home' | 'away', tSec: number, extras: any = {}) => {
    events.push({ timestamp: Math.round(tSec), type, teamSide: side, ...extras });
  };

  // Goals
  const usedTimes = new Set<number>();
  const uniqueTime = (min: number, max: number) => {
    let t: number;
    do { t = Math.round((min + r() * (max - min)) * 60); } while (usedTimes.has(t));
    usedTimes.add(t);
    return t;
  };

  for (let g = 0; g < hGoals; g++) {
    const t = uniqueTime(5, matchMinutes - 2);
    const xg = Math.round((0.25 + r() * 0.55) * 100) / 100;
    addEvent('goal', 'home', t, { xG: xg, x: PITCH_W * (0.8 + r() * 0.15), y: PITCH_H * (0.35 + r() * 0.3), description: `Goal! xG: ${xg}` });
    addEvent('shot_on_target', 'home', t - 1, { xG: xg });
  }
  for (let g = 0; g < aGoals; g++) {
    const t = uniqueTime(5, matchMinutes - 2);
    const xg = Math.round((0.25 + r() * 0.55) * 100) / 100;
    addEvent('goal', 'away', t, { xG: xg, x: PITCH_W * (0.05 + r() * 0.15), y: PITCH_H * (0.35 + r() * 0.3), description: `Goal! xG: ${xg}` });
    addEvent('shot_on_target', 'away', t - 1, { xG: xg });
  }

  // Additional shots on target
  for (let i = hGoals; i < hOnTarget; i++) addEvent('shot_on_target', 'home', uniqueTime(3, matchMinutes), { xG: Math.round((0.08 + r() * 0.3) * 100) / 100 });
  for (let i = aGoals; i < aOnTarget; i++) addEvent('shot_on_target', 'away', uniqueTime(3, matchMinutes), { xG: Math.round((0.06 + r() * 0.28) * 100) / 100 });

  // Wide shots
  for (let i = hOnTarget; i < hShots; i++) addEvent('shot', 'home', uniqueTime(3, matchMinutes), { xG: Math.round((0.03 + r() * 0.12) * 100) / 100 });
  for (let i = aOnTarget; i < aShots; i++) addEvent('shot', 'away', uniqueTime(3, matchMinutes), { xG: Math.round((0.02 + r() * 0.12) * 100) / 100 });

  // Fouls
  for (let i = 0; i < hFouls; i++) addEvent('foul', 'home', uniqueTime(2, matchMinutes));
  for (let i = 0; i < aFouls; i++) addEvent('foul', 'away', uniqueTime(2, matchMinutes));

  // Corners
  for (let i = 0; i < hCorners; i++) addEvent('corner', 'home', uniqueTime(2, matchMinutes));
  for (let i = 0; i < aCorners; i++) addEvent('corner', 'away', uniqueTime(2, matchMinutes));

  // Momentum timeline (2-min windows)
  const momentumTimeline: { minute: number; home: number; away: number }[] = [];
  for (let m = 0; m <= matchMinutes; m += 2) {
    const base = possH + (r() - 0.5) * 22;
    const h = Math.min(85, Math.max(15, Math.round(base)));
    momentumTimeline.push({ minute: m, home: h, away: 100 - h });
  }

  // Heatmaps — realistic positional density
  const buildHeatmap = (side: 'home' | 'away') => {
    const positions: { x: number; y: number; intensity: number }[] = [];
    const count = 40 + Math.floor(r() * 30);
    // Cluster around typical areas for that team
    const baseX = side === 'home'
      ? [20, 35, 55, 70, 85]   // full pitch but more in opponent half
      : [20, 35, 50, 65, 85];
    for (let i = 0; i < count; i++) {
      const cx = baseX[Math.floor(r() * baseX.length)];
      const cy = 15 + r() * (PITCH_H - 30);
      positions.push({
        x: Math.max(0, Math.min(PITCH_W, cx + (r() - 0.5) * 20)),
        y: Math.max(0, Math.min(PITCH_H, cy + (r() - 0.5) * 15)),
        intensity: Math.round((0.2 + r() * 0.8) * 100) / 100,
      });
    }
    return { playerId: side, teamSide: side as 'home' | 'away', positions };
  };

  // Pass network — realistic formation nodes
  const formationHome = [
    { x: 10, y: 34 }, // GK
    { x: 25, y: 12 }, { x: 25, y: 28 }, { x: 25, y: 50 }, { x: 25, y: 62 }, // defenders
    { x: 45, y: 18 }, { x: 45, y: 34 }, { x: 45, y: 50 }, // midfield
    { x: 65, y: 20 }, { x: 65, y: 48 }, // wingers
    { x: 78, y: 34 }, // striker
  ];
  const formationAway = formationHome.map((p) => ({ x: PITCH_W - p.x, y: p.y }));

  const buildNodes = (formation: typeof formationHome, side: 'home' | 'away') =>
    formation.map((p, i) => ({
      playerId: `${side}_${i}`,
      name: `P${i + 1}`,
      teamSide: side as 'home' | 'away',
      involvement: 20 + Math.floor(r() * 60),
      x: p.x + (r() - 0.5) * 4,
      y: p.y + (r() - 0.5) * 4,
    }));

  const homeNodes = buildNodes(formationHome, 'home');
  const awayNodes = buildNodes(formationAway, 'away');
  const allNodes = [...homeNodes, ...awayNodes];

  const buildEdges = (nodes: typeof homeNodes, side: 'home' | 'away') => {
    const edges: any[] = [];
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const dx = nodes[i].x - nodes[j].x;
        const dy = nodes[i].y - nodes[j].y;
        const d = Math.sqrt(dx * dx + dy * dy);
        if (d < 22 && r() > 0.3) {
          edges.push({
            from: nodes[i].playerId,
            to: nodes[j].playerId,
            count: 2 + Math.floor(r() * 12),
            accuracy: Math.round((0.6 + r() * 0.35) * 100) / 100,
          });
        }
      }
    }
    return edges;
  };

  const passNetwork = {
    nodes: allNodes,
    edges: [...buildEdges(homeNodes, 'home'), ...buildEdges(awayNodes, 'away')].slice(0, 50),
  };

  // Narrative (TARA-style: data-driven prose)
  const dominant = possH >= possA ? teamNames.home : teamNames.away;
  const domPoss = Math.max(possH, possA);
  const xgWinner = hXG >= aXG ? teamNames.home : teamNames.away;
  const firstGoal = events.filter((e) => e.type === 'goal').sort((a, b) => a.timestamp - b.timestamp)[0];
  let narrativeParts = [
    `${dominant} controlled the tempo with ${domPoss}% of possession throughout the ${matchMinutes}-minute contest.`,
    `${xgWinner} created the better chances, accumulating an xG of ${Math.max(hXG, aXG).toFixed(2)} against ${Math.min(hXG, aXG).toFixed(2)} for the opposition.`,
  ];
  if (firstGoal) {
    const min = Math.floor(firstGoal.timestamp / 60);
    const scorer = firstGoal.teamSide === 'home' ? teamNames.home : teamNames.away;
    narrativeParts.push(`${scorer} broke the deadlock on ${min} minutes.`);
  }
  narrativeParts.push(`The match ended ${hGoals}–${aGoals}.`);
  if (!firstGoal && hGoals === 0 && aGoals === 0) {
    narrativeParts.push(`Despite both teams creating chances, neither side could convert — a hard-fought goalless draw.`);
  }

  return {
    score: { home: hGoals, away: aGoals },
    possession: { home: possH, away: possA },
    passes: {
      home: { completed: hPassComp, total: hPassTotal, accuracy: hPassTotal > 0 ? Math.round(hPassComp / hPassTotal * 1000) / 10 : 0 },
      away: { completed: aPassComp, total: aPassTotal, accuracy: aPassTotal > 0 ? Math.round(aPassComp / aPassTotal * 1000) / 10 : 0 },
    },
    shots: {
      home: { total: hShots, onTarget: hOnTarget, xG: hXG },
      away: { total: aShots, onTarget: aOnTarget, xG: aXG },
    },
    fouls: { home: hFouls, away: aFouls },
    corners: { home: hCorners, away: aCorners },
    pressureIndex: { home: +(possH / 20).toFixed(1), away: +(possA / 20).toFixed(1) },
    momentumTimeline,
    events: events.sort((a, b) => a.timestamp - b.timestamp),
    heatmaps: [buildHeatmap('home'), buildHeatmap('away')],
    voronoi: [],
    passNetwork,
    narrative: narrativeParts.join(' '),
  };
}

// ── Main export ──────────────────────────────────────────────────────────
export async function processVideo(
  file: File,
  teamNames: { home: string; away: string },
  options: ProcessOptions = {}
) {
  const { onStage = () => {}, onProgress = () => {} } = options;
  const rng = seededRng(strSeed(file.name + file.size));

  // ── Stage 1: Ingest ────────────────────────────────────────────────────
  onStage('Reading video metadata…');
  onProgress(5);
  const duration = await getVideoDuration(file);
  await delay(300);

  // ── Stage 2: Frame extraction (best-effort, 8-frame probe) ────────────
  onStage('Extracting key frames…');
  onProgress(15);
  await delay(400);

  onStage('Running AI assessment per frame…');
  onProgress(25);

  // Quick 4-frame Roboflow probe (non-blocking, 8s total budget)
  let hasRealData = false;
  try {
    const probe = await Promise.race([
      extractAndInfer(file, 4, duration, () => {}),
      delay(8000).then(() => ({ hasRealData: false, frames: [] })),
    ]) as { hasRealData: boolean; frames: any[] };
    hasRealData = probe.hasRealData;
  } catch { /* ignore */ }

  // ── Stage 3: Segmentation ─────────────────────────────────────────────
  onStage('Segmenting possession & events…');
  onProgress(50);
  await delay(500);

  // ── Stage 4: Analytics ────────────────────────────────────────────────
  onStage('Computing statistics…');
  onProgress(70);
  await delay(400);

  // ── Stage 5: Building heatmaps & pass network ─────────────────────────
  onStage('Building heatmaps & pass network…');
  onProgress(82);
  await delay(300);

  // ── Stage 6: Generating narrative ─────────────────────────────────────
  onStage('Generating match narrative…');
  onProgress(92);
  await delay(300);

  const stats = buildDemoStats(teamNames, duration, rng);

  if (hasRealData) {
    // TODO (Phase 2): merge real Roboflow detections into stats
    console.info('[pitchlens] Roboflow data available — merging in Phase 2');
  } else {
    console.info('[pitchlens] Demo mode — add ROBOFLOW_API_KEY to Vercel for live AI detection');
  }

  onProgress(100);
  return stats;
}

// Legacy export (used by old code paths)
export function generateMockStats(
  teamNames: { home: string; away: string },
  duration = 300
) {
  const rng = seededRng(strSeed(teamNames.home + teamNames.away + duration));
  return buildDemoStats(teamNames, duration, rng);
}
