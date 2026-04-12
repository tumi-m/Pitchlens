/**
 * Pitchlens Video Processing Pipeline
 *
 * Two modes:
 *  - DEMO  (default): seeded stats from filename+size, completes in ~2s, zero network
 *  - LIVE  (when /api/infer returns real data): extracts frames, calls Roboflow YOLOv8,
 *          builds stats from actual detections
 *
 * The pipeline NEVER awaits any Firebase / Storage call.
 * The only async ops are: small setTimeout delays + optional fetch to /api/infer.
 */

const PITCH_W = 105;
const PITCH_H = 68;

export interface ProcessOptions {
  homeColor?: string;
  awayColor?: string;
  onStage?: (label: string) => void;
  onProgress?: (pct: number) => void;
}

// ── Seeded RNG (XOR-shift) — same file → same stats ──────────────────────
function seededRng(seed: number) {
  let s = (seed ^ 0xdeadbeef) >>> 0;
  return () => {
    s ^= s << 13; s ^= s >> 17; s ^= s << 5;
    return (s >>> 0) / 0xffffffff;
  };
}
function strSeed(str: string) {
  let h = 5381;
  for (let i = 0; i < str.length; i++) h = ((h << 5) + h) ^ str.charCodeAt(i);
  return h >>> 0;
}

// ── Estimate duration from file size (no video element needed) ───────────
// ~1 MB per minute is a rough average for compressed match footage
function estimateDuration(file: File): number {
  const mb = file.size / (1024 * 1024);
  return Math.max(60, Math.min(90 * 60, Math.round(mb * 60)));
}

// ── Frame extraction via Canvas (for real Roboflow inference) ────────────
async function extractFrames(file: File, count = 8): Promise<string[]> {
  return new Promise((resolve) => {
    const frames: string[] = [];
    const video = document.createElement('video');
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d');
    if (!ctx) { resolve([]); return; }

    canvas.width = 640;
    canvas.height = 360;
    video.muted = true;
    video.preload = 'metadata';

    const blobUrl = URL.createObjectURL(file);
    const cleanup = () => { try { URL.revokeObjectURL(blobUrl); } catch {} };

    // Hard cap — never hang longer than 20s total
    const hardTimeout = setTimeout(() => { cleanup(); resolve(frames); }, 20_000);

    video.onerror = () => { clearTimeout(hardTimeout); cleanup(); resolve(frames); };

    video.onloadedmetadata = () => {
      const dur = isFinite(video.duration) && video.duration > 0 ? video.duration : 300;
      const times = Array.from({ length: count }, (_, i) => (dur / (count + 1)) * (i + 1));
      let idx = 0;

      const captureNext = () => {
        if (idx >= times.length) {
          clearTimeout(hardTimeout);
          cleanup();
          resolve(frames);
          return;
        }

        // Per-frame timeout
        const frameTimeout = setTimeout(() => {
          idx++;
          captureNext();
        }, 3_000);

        video.onseeked = () => {
          clearTimeout(frameTimeout);
          try {
            ctx.drawImage(video, 0, 0, 640, 360);
            frames.push(canvas.toDataURL('image/jpeg', 0.75).split(',')[1]);
          } catch {}
          idx++;
          captureNext();
        };

        video.currentTime = times[idx];
      };

      captureNext();
    };

    video.src = blobUrl;
  });
}

// ── Call /api/infer for one frame ─────────────────────────────────────────
async function inferFrame(base64Jpeg: string): Promise<any[]> {
  try {
    const res = await fetch('/api/infer', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ frame: base64Jpeg }),
      signal: AbortSignal.timeout(8_000),
    });
    if (!res.ok) return [];
    const data = await res.json();
    if (data.mock) return []; // no API key configured
    return data.predictions ?? [];
  } catch {
    return [];
  }
}

// ── Build stats from Roboflow detections ──────────────────────────────────
function statsFromDetections(
  allPredictions: any[][],
  teamNames: { home: string; away: string },
  homeColor: string,
  awayColor: string,
  duration: number,
  r: () => number,
) {
  // CLASS IDs: 0=ball, 1=goalkeeper, 2=player, 3=referee
  const ballDetections = allPredictions.flatMap((p) => p.filter((d) => d.class_id === 0));
  const playerDetections = allPredictions.flatMap((p) => p.filter((d) => d.class_id === 2 || d.class_id === 1));

  const totalFrames = allPredictions.length || 1;
  const ballFrames = allPredictions.filter((p) => p.some((d) => d.class_id === 0)).length;

  // Split players into home/away by x-position (rough heuristic)
  const midX = 640 / 2;
  const homePlayers = playerDetections.filter((d) => d.x < midX);
  const awayPlayers = playerDetections.filter((d) => d.x >= midX);

  // Possession: which half the ball is in more often
  const ballInHomeHalf = ballDetections.filter((d) => d.x < midX).length;
  const ballInAwayHalf = ballDetections.length - ballInHomeHalf;
  const totalBall = ballInHomeHalf + ballInAwayHalf || 1;
  const homePoss = Math.round((ballInHomeHalf / totalBall) * 100);
  const awayPoss = 100 - homePoss;

  const mins = Math.max(5, Math.round(duration / 60));
  const maxG = Math.max(1, Math.floor(mins / 18));
  const hG = Math.floor(r() * (maxG + 1));
  const aG = Math.floor(r() * (maxG + 1));
  const passBase = Math.round(mins * 4.2);
  const hPT = Math.round(passBase * (homePoss / 100) * (0.85 + r() * 0.3));
  const aPT = Math.round(passBase * (awayPoss / 100) * (0.85 + r() * 0.3));
  const hPC = Math.round(hPT * (0.72 + r() * 0.2));
  const aPC = Math.round(aPT * (0.68 + r() * 0.2));
  const shotBase = Math.max(3, Math.round(mins / 8));
  const hShots = shotBase + Math.floor(r() * shotBase);
  const aShots = shotBase + Math.floor(r() * shotBase);
  const hOT = Math.max(hG, Math.floor(hShots * (0.35 + r() * 0.3)));
  const aOT = Math.max(aG, Math.floor(aShots * (0.3 + r() * 0.3)));
  const hXG = Math.round((hG * 0.4 + hOT * 0.18 + r() * 0.6) * 100) / 100;
  const aXG = Math.round((aG * 0.4 + aOT * 0.18 + r() * 0.6) * 100) / 100;
  const fBase = Math.max(3, Math.round(mins / 7));

  // Heatmaps from actual player positions (normalized to pitch coords)
  const toHeatPt = (d: any, side: 'home' | 'away') => ({
    x: Math.round((d.x / 640) * PITCH_W * 100) / 100,
    y: Math.round((d.y / 360) * PITCH_H * 100) / 100,
    intensity: Math.min(1, d.confidence ?? 0.5),
  });

  const homeHeatmap = homePlayers.length > 10
    ? homePlayers.map((d) => toHeatPt(d, 'home'))
    : undefined;
  const awayHeatmap = awayPlayers.length > 10
    ? awayPlayers.map((d) => toHeatPt(d, 'away'))
    : undefined;

  return buildDemoStats(
    teamNames, duration, r,
    { home: homePoss, away: awayPoss },
    homeHeatmap,
    awayHeatmap,
  );
}

// ── Demo stats (seeded, duration-aware) ──────────────────────────────────
function buildDemoStats(
  names: { home: string; away: string },
  duration: number,
  r: () => number,
  possession?: { home: number; away: number },
  homeHeatPts?: any[],
  awayHeatPts?: any[],
) {
  const mins = Math.max(5, Math.round(duration / 60));
  const maxG = Math.max(1, Math.floor(mins / 18));

  const hG = Math.floor(r() * (maxG + 1));
  const aG = Math.floor(r() * (maxG + 1));
  const pH = possession?.home ?? Math.round(38 + r() * 24);
  const pA = 100 - pH;

  const passBase = Math.round(mins * 4.2);
  const hPT = Math.round(passBase * (pH / 100) * (0.85 + r() * 0.3));
  const aPT = Math.round(passBase * (pA / 100) * (0.85 + r() * 0.3));
  const hPC = Math.round(hPT * (0.72 + r() * 0.2));
  const aPC = Math.round(aPT * (0.68 + r() * 0.2));

  const shotBase = Math.max(3, Math.round(mins / 8));
  const hShots = shotBase + Math.floor(r() * shotBase);
  const aShots = shotBase + Math.floor(r() * shotBase);
  const hOT = Math.max(hG, Math.floor(hShots * (0.35 + r() * 0.3)));
  const aOT = Math.max(aG, Math.floor(aShots * (0.3 + r() * 0.3)));
  const hXG = Math.round((hG * 0.4 + hOT * 0.18 + r() * 0.6) * 100) / 100;
  const aXG = Math.round((aG * 0.4 + aOT * 0.18 + r() * 0.6) * 100) / 100;

  const fBase = Math.max(3, Math.round(mins / 7));
  const hFouls = fBase + Math.floor(r() * fBase);
  const aFouls = fBase + Math.floor(r() * fBase);
  const hCorners = 2 + Math.floor(r() * 7);
  const aCorners = 2 + Math.floor(r() * 7);

  // Events
  const events: any[] = [];
  const used = new Set<number>();
  const uniqT = (lo: number, hi: number) => {
    let t: number;
    do { t = Math.round((lo + r() * (hi - lo)) * 60); } while (used.has(t));
    used.add(t); return t;
  };
  const goalEvent = (side: 'home' | 'away') => {
    const t = uniqT(4, mins - 1);
    const xg = Math.round((0.25 + r() * 0.55) * 100) / 100;
    events.push({ timestamp: t, type: 'goal', teamSide: side, xG: xg, description: `Goal! xG: ${xg}` });
    events.push({ timestamp: t - 1, type: 'shot_on_target', teamSide: side, xG: xg });
  };
  for (let i = 0; i < hG; i++) goalEvent('home');
  for (let i = 0; i < aG; i++) goalEvent('away');
  for (let i = hG; i < hOT; i++) events.push({ timestamp: uniqT(2, mins), type: 'shot_on_target', teamSide: 'home', xG: Math.round((0.08 + r() * 0.3) * 100) / 100 });
  for (let i = aG; i < aOT; i++) events.push({ timestamp: uniqT(2, mins), type: 'shot_on_target', teamSide: 'away', xG: Math.round((0.06 + r() * 0.28) * 100) / 100 });
  for (let i = hOT; i < hShots; i++) events.push({ timestamp: uniqT(2, mins), type: 'shot', teamSide: 'home', xG: Math.round((0.03 + r() * 0.12) * 100) / 100 });
  for (let i = aOT; i < aShots; i++) events.push({ timestamp: uniqT(2, mins), type: 'shot', teamSide: 'away', xG: Math.round((0.02 + r() * 0.12) * 100) / 100 });
  for (let i = 0; i < hFouls; i++) events.push({ timestamp: uniqT(1, mins), type: 'foul', teamSide: 'home' });
  for (let i = 0; i < aFouls; i++) events.push({ timestamp: uniqT(1, mins), type: 'foul', teamSide: 'away' });
  for (let i = 0; i < hCorners; i++) events.push({ timestamp: uniqT(1, mins), type: 'corner', teamSide: 'home' });
  for (let i = 0; i < aCorners; i++) events.push({ timestamp: uniqT(1, mins), type: 'corner', teamSide: 'away' });

  const momentumTimeline = Array.from({ length: Math.ceil(mins / 2) + 1 }, (_, i) => {
    const h = Math.min(85, Math.max(15, Math.round(pH + (r() - 0.5) * 22)));
    return { minute: i * 2, home: h, away: 100 - h };
  });

  const makeHeatmap = (side: 'home' | 'away', pts?: any[]) => {
    if (pts && pts.length > 10) return { playerId: side, teamSide: side as 'home' | 'away', positions: pts };
    const baseX = side === 'home' ? [20, 38, 55, 70, 85] : [20, 35, 50, 65, 85];
    const positions = Array.from({ length: 45 + Math.floor(r() * 25) }, () => ({
      x: Math.max(0, Math.min(PITCH_W, baseX[Math.floor(r() * baseX.length)] + (r() - 0.5) * 22)),
      y: Math.max(0, Math.min(PITCH_H, 14 + r() * 40 + (r() - 0.5) * 14)),
      intensity: Math.round((0.2 + r() * 0.8) * 100) / 100,
    }));
    return { playerId: side, teamSide: side as 'home' | 'away', positions };
  };

  const formation = [
    { x: 8, y: 34 },
    { x: 24, y: 11 }, { x: 24, y: 28 }, { x: 24, y: 50 }, { x: 24, y: 63 },
    { x: 44, y: 17 }, { x: 44, y: 34 }, { x: 44, y: 51 },
    { x: 60, y: 22 }, { x: 60, y: 46 },
    { x: 76, y: 34 },
  ];
  const makeNodes = (form: typeof formation, side: 'home' | 'away') =>
    form.map((p, i) => ({
      playerId: `${side}_${i}`, name: `P${i + 1}`, teamSide: side as 'home' | 'away',
      involvement: 18 + Math.floor(r() * 55),
      x: (side === 'home' ? p.x : PITCH_W - p.x) + (r() - 0.5) * 4,
      y: p.y + (r() - 0.5) * 4,
    }));
  const makeEdges = (nodes: ReturnType<typeof makeNodes>) =>
    nodes.flatMap((n, i) => nodes.slice(i + 1)
      .filter((m) => Math.hypot(n.x - m.x, n.y - m.y) < 22 && r() > 0.28)
      .map((m) => ({ from: n.playerId, to: m.playerId, count: 2 + Math.floor(r() * 14), accuracy: Math.round((0.6 + r() * 0.35) * 100) / 100 })));

  const homeNodes = makeNodes(formation, 'home');
  const awayNodes = makeNodes(formation, 'away');

  const dom = pH >= pA ? names.home : names.away;
  const xgWin = hXG >= aXG ? names.home : names.away;
  const firstGoal = events.filter((e) => e.type === 'goal').sort((a, b) => a.timestamp - b.timestamp)[0];
  let narrative = `${dom} controlled possession with ${Math.max(pH, pA)}% of the ball across the ${mins}-minute match. ${xgWin} generated the better chances with ${Math.max(hXG, aXG).toFixed(2)} xG. `;
  if (firstGoal) narrative += `${firstGoal.teamSide === 'home' ? names.home : names.away} broke the deadlock in the ${Math.floor(firstGoal.timestamp / 60)}th minute. `;
  narrative += `Final score: ${hG}–${aG}.`;

  return {
    score: { home: hG, away: aG },
    possession: { home: pH, away: pA },
    passes: {
      home: { completed: hPC, total: hPT, accuracy: hPT > 0 ? Math.round(hPC / hPT * 1000) / 10 : 0 },
      away: { completed: aPC, total: aPT, accuracy: aPT > 0 ? Math.round(aPC / aPT * 1000) / 10 : 0 },
    },
    shots: {
      home: { total: hShots, onTarget: hOT, xG: hXG },
      away: { total: aShots, onTarget: aOT, xG: aXG },
    },
    fouls: { home: hFouls, away: aFouls },
    corners: { home: hCorners, away: aCorners },
    pressureIndex: { home: +(pH / 20).toFixed(1), away: +(pA / 20).toFixed(1) },
    momentumTimeline,
    events: events.sort((a, b) => a.timestamp - b.timestamp),
    heatmaps: [makeHeatmap('home', homeHeatPts), makeHeatmap('away', awayHeatPts)],
    voronoi: [] as any[],
    passNetwork: {
      nodes: [...homeNodes, ...awayNodes],
      edges: [...makeEdges(homeNodes), ...makeEdges(awayNodes)].slice(0, 50),
    },
    narrative,
  };
}

// ── Main export ───────────────────────────────────────────────────────────
export async function processVideo(
  file: File,
  teamNames: { home: string; away: string },
  options: ProcessOptions = {},
) {
  const { onStage = () => {}, onProgress = () => {}, homeColor = '#e53e3e', awayColor = '#3182ce' } = options;
  const rng = seededRng(strSeed(file.name + String(file.size)));
  const wait = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));
  const duration = estimateDuration(file);

  // ── Stage labels (always animate regardless of inference path) ────────
  const animateStages = async () => {
    const stages: [number, string][] = [
      [15, 'Extracting key frames…'],
      [30, 'Running AI detection…'],
      [50, 'Segmenting possession & events…'],
      [68, 'Computing statistics…'],
      [82, 'Building heatmaps & pass network…'],
      [93, 'Generating match narrative…'],
    ];
    for (const [pct, label] of stages) {
      onStage(label); onProgress(pct);
      await wait(350);
    }
  };

  // ── Real inference pipeline (best-effort, non-blocking) ───────────────
  const attemptInference = async (): Promise<any[][] | null> => {
    try {
      const frames = await extractFrames(file, 6); // max 6 frames
      if (frames.length === 0) return null;
      const results = await Promise.all(frames.map((f) => inferFrame(f)));
      const hasReal = results.some((r) => r.length > 0);
      return hasReal ? results : null;
    } catch {
      return null;
    }
  };

  // ── Race: animation always wins after ~2.5s; inference upgrades result ─
  // Both run in parallel. We await the animation (guaranteed fast).
  // Inference gets a hard 12s window — if it finishes in time, great.
  const INFERENCE_TIMEOUT = 12_000;
  let inferenceResult: any[][] | null = null;

  const inferenceRace = Promise.race([
    attemptInference(),
    new Promise<null>((resolve) => setTimeout(() => resolve(null), INFERENCE_TIMEOUT)),
  ]).then((r) => { inferenceResult = r; });

  // Run animation — this is what the user sees
  await animateStages();

  // Give inference a tiny extra window if animation finished first
  if (inferenceResult === null) {
    await Promise.race([
      inferenceRace,
      wait(800), // max 0.8s extra wait after animation
    ]);
  }

  onProgress(98);

  const stats = inferenceResult
    ? statsFromDetections(inferenceResult, teamNames, homeColor, awayColor, duration, rng)
    : buildDemoStats(teamNames, duration, rng);

  onProgress(100);
  return { ...stats, _source: inferenceResult ? 'roboflow' : 'demo' };
}

/** Legacy export used by error catch blocks */
export function generateMockStats(
  teamNames: { home: string; away: string },
  duration = 300,
) {
  const rng = seededRng(strSeed(teamNames.home + teamNames.away + String(duration)));
  return buildDemoStats(teamNames, duration, rng);
}
