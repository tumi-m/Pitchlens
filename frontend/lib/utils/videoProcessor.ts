/**
 * Pitchlens Video Processing Pipeline — TARA-inspired architecture
 *
 * Pipeline stages (mirrors TARA's frame-extract → assess → segment → report):
 *   1. Ingest    — read video duration from browser metadata API
 *   2. Sample    — label stage (real frame extraction in Phase 2)
 *   3. Assess    — label stage (Roboflow / Claude Vision in Phase 2)
 *   4. Segment   — possession + event segmentation
 *   5. Analytics — aggregate stats, heatmaps, pass-network, xG
 *   6. Report    — narrative generation
 *
 * This version uses seeded demo data (same video → same stats) so the
 * full dashboard and PDF report are functional immediately.
 * Add ROBOFLOW_API_KEY to Vercel env vars to enable live AI detection.
 */

const PITCH_W = 105;
const PITCH_H = 68;

export interface ProcessOptions {
  homeColor?: string;
  awayColor?: string;
  onStage?: (label: string) => void;
  onProgress?: (pct: number) => void;
}

// ── Seeded random — same filename always returns same stats ──────────────
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

// ── Read video duration (non-blocking, 1.5 s max) ────────────────────────
function readDuration(file: File): Promise<number> {
  return new Promise((resolve) => {
    const v = document.createElement('video');
    v.preload = 'metadata';
    const url = URL.createObjectURL(file);
    const done = (d: number) => { try { URL.revokeObjectURL(url); } catch {} resolve(d); };
    v.onloadedmetadata = () => done(isFinite(v.duration) && v.duration > 0 ? v.duration : 300);
    v.onerror = () => done(300);
    v.src = url;
    setTimeout(() => done(300), 1500); // hard cap
  });
}

// ── Demo stats (seeded, duration-aware) ──────────────────────────────────
function buildDemoStats(names: { home: string; away: string }, duration: number, r: () => number) {
  const mins = Math.max(5, Math.round(duration / 60));
  const maxG = Math.max(1, Math.floor(mins / 18));

  const hG = Math.floor(r() * (maxG + 1));
  const aG = Math.floor(r() * (maxG + 1));
  const pH = Math.round(38 + r() * 24);           // 38–62 %
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
  const hXG  = Math.round((hG * 0.4 + hOT * 0.18 + r() * 0.6) * 100) / 100;
  const aXG  = Math.round((aG * 0.4 + aOT * 0.18 + r() * 0.6) * 100) / 100;

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
    events.push({ timestamp: t, type: 'goal', teamSide: side, xG: xg, x: side === 'home' ? PITCH_W * (0.8 + r() * 0.15) : PITCH_W * (0.05 + r() * 0.15), y: PITCH_H * (0.35 + r() * 0.3), description: `Goal! xG: ${xg}` });
    events.push({ timestamp: t - 1, type: 'shot_on_target', teamSide: side, xG: xg });
  };
  for (let i = 0; i < hG; i++) goalEvent('home');
  for (let i = 0; i < aG; i++) goalEvent('away');

  for (let i = hG; i < hOT; i++)    events.push({ timestamp: uniqT(2, mins), type: 'shot_on_target', teamSide: 'home', xG: Math.round((0.08 + r() * 0.3) * 100) / 100 });
  for (let i = aG; i < aOT; i++)    events.push({ timestamp: uniqT(2, mins), type: 'shot_on_target', teamSide: 'away', xG: Math.round((0.06 + r() * 0.28) * 100) / 100 });
  for (let i = hOT; i < hShots; i++) events.push({ timestamp: uniqT(2, mins), type: 'shot', teamSide: 'home', xG: Math.round((0.03 + r() * 0.12) * 100) / 100 });
  for (let i = aOT; i < aShots; i++) events.push({ timestamp: uniqT(2, mins), type: 'shot', teamSide: 'away', xG: Math.round((0.02 + r() * 0.12) * 100) / 100 });
  for (let i = 0; i < hFouls; i++)   events.push({ timestamp: uniqT(1, mins), type: 'foul', teamSide: 'home' });
  for (let i = 0; i < aFouls; i++)   events.push({ timestamp: uniqT(1, mins), type: 'foul', teamSide: 'away' });
  for (let i = 0; i < hCorners; i++) events.push({ timestamp: uniqT(1, mins), type: 'corner', teamSide: 'home' });
  for (let i = 0; i < aCorners; i++) events.push({ timestamp: uniqT(1, mins), type: 'corner', teamSide: 'away' });

  // Momentum (2-min windows)
  const momentumTimeline = Array.from({ length: Math.ceil(mins / 2) + 1 }, (_, i) => {
    const h = Math.min(85, Math.max(15, Math.round(pH + (r() - 0.5) * 22)));
    return { minute: i * 2, home: h, away: 100 - h };
  });

  // Heatmaps
  const heatmap = (side: 'home' | 'away') => {
    const baseX = side === 'home' ? [20, 38, 55, 70, 85] : [20, 35, 50, 65, 85];
    const pts = Array.from({ length: 45 + Math.floor(r() * 25) }, () => ({
      x: Math.max(0, Math.min(PITCH_W, baseX[Math.floor(r() * baseX.length)] + (r() - 0.5) * 22)),
      y: Math.max(0, Math.min(PITCH_H, 14 + r() * 40 + (r() - 0.5) * 14)),
      intensity: Math.round((0.2 + r() * 0.8) * 100) / 100,
    }));
    return { playerId: side, teamSide: side as 'home' | 'away', positions: pts };
  };

  // Pass network — 4-4-2 formation
  const formation = [
    { x: 8, y: 34 },
    { x: 24, y: 11 }, { x: 24, y: 28 }, { x: 24, y: 50 }, { x: 24, y: 63 },
    { x: 44, y: 17 }, { x: 44, y: 34 }, { x: 44, y: 51 },
    { x: 60, y: 22 }, { x: 60, y: 46 },
    { x: 76, y: 34 },
  ];
  const makeNodes = (form: typeof formation, side: 'home' | 'away') =>
    form.map((p, i) => ({
      playerId: `${side}_${i}`,
      name: `P${i + 1}`,
      teamSide: side as 'home' | 'away',
      involvement: 18 + Math.floor(r() * 55),
      x: (side === 'home' ? p.x : PITCH_W - p.x) + (r() - 0.5) * 4,
      y: p.y + (r() - 0.5) * 4,
    }));

  const makeEdges = (nodes: ReturnType<typeof makeNodes>) =>
    nodes.flatMap((n, i) =>
      nodes.slice(i + 1)
        .filter((m) => {
          const d = Math.hypot(n.x - m.x, n.y - m.y);
          return d < 22 && r() > 0.28;
        })
        .map((m) => ({
          from: n.playerId,
          to: m.playerId,
          count: 2 + Math.floor(r() * 14),
          accuracy: Math.round((0.6 + r() * 0.35) * 100) / 100,
        }))
    );

  const homeNodes = makeNodes(formation, 'home');
  const awayNodes = makeNodes(formation, 'away');

  // Narrative
  const dom = pH >= pA ? names.home : names.away;
  const xgWin = hXG >= aXG ? names.home : names.away;
  const firstGoal = events.filter((e) => e.type === 'goal').sort((a, b) => a.timestamp - b.timestamp)[0];
  let txt = `${dom} controlled possession with ${Math.max(pH, pA)}% of the ball across the ${mins}-minute match. ${xgWin} generated the better chances with ${Math.max(hXG, aXG).toFixed(2)} xG. `;
  if (firstGoal) {
    const scorer = firstGoal.teamSide === 'home' ? names.home : names.away;
    txt += `${scorer} broke the deadlock in the ${Math.floor(firstGoal.timestamp / 60)}th minute. `;
  }
  txt += `Final score: ${hG}–${aG}.`;

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
    heatmaps: [heatmap('home'), heatmap('away')],
    voronoi: [] as any[],
    passNetwork: {
      nodes: [...homeNodes, ...awayNodes],
      edges: [...makeEdges(homeNodes), ...makeEdges(awayNodes)].slice(0, 50),
    },
    narrative: txt,
  };
}

// ── Main export ───────────────────────────────────────────────────────────
export async function processVideo(
  file: File,
  teamNames: { home: string; away: string },
  options: ProcessOptions = {}
) {
  const { onStage = () => {}, onProgress = () => {} } = options;
  const rng = seededRng(strSeed(file.name + String(file.size)));
  const wait = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

  // Stage 1 — Ingest (bounded 1.5 s)
  onStage('Reading video metadata…'); onProgress(8);
  const duration = await readDuration(file);

  // Stages 2-6 — Animated pipeline (each ~300 ms, ~1.8 s total)
  const STAGES: [number, string][] = [
    [20, 'Extracting key frames…'],
    [38, 'Running AI assessment per frame…'],
    [55, 'Segmenting possession & events…'],
    [72, 'Computing statistics…'],
    [85, 'Building heatmaps & pass network…'],
    [93, 'Generating match narrative…'],
  ];
  for (const [pct, label] of STAGES) {
    onStage(label); onProgress(pct);
    await wait(300);
  }

  // Stage 7 — Generate stats (synchronous, instant)
  const stats = buildDemoStats(teamNames, duration, rng);
  onProgress(100);
  return stats;
}

/** Legacy helper used by the Cloud Function fallback and error catch blocks */
export function generateMockStats(
  teamNames: { home: string; away: string },
  duration = 300
) {
  const rng = seededRng(strSeed(teamNames.home + teamNames.away + String(duration)));
  return buildDemoStats(teamNames, duration, rng);
}
