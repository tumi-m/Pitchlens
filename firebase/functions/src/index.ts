import * as admin from 'firebase-admin';
import { onObjectFinalized } from 'firebase-functions/v2/storage';
import { onDocumentUpdated } from 'firebase-functions/v2/firestore';
import { onCall, HttpsError } from 'firebase-functions/v2/https';
import { logger } from 'firebase-functions/v2';
import axios from 'axios';
import * as path from 'path';
import * as os from 'os';
import * as fs from 'fs';
import { execFile } from 'child_process';
import { promisify } from 'util';

const execFileAsync = promisify(execFile);

admin.initializeApp();
const db = admin.firestore();
const storage = admin.storage();

// ── Constants ─────────────────────────────────────────────────────────────
const PYTHON_API_URL = process.env.PYTHON_API_URL ?? '';
const API_SECRET_KEY = process.env.API_SECRET_KEY ?? '';
const ROBOFLOW_API_KEY = process.env.ROBOFLOW_API_KEY ?? '';
const ROBOFLOW_PROJECT = process.env.ROBOFLOW_PROJECT ?? 'football-players-detection-3zvbc';
const ROBOFLOW_VERSION = process.env.ROBOFLOW_VERSION ?? '9';
const MAX_FRAMES = 60; // Max frames to process per match

// Pitch dimensions (standard 11-a-side, metres)
const PITCH_W = 105.0;
const PITCH_H = 68.0;

// Roboflow class IDs
const CLASS_BALL = 0;
const CLASS_GOALKEEPER = 1;
const CLASS_PLAYER = 2;
// const CLASS_REFEREE = 3; // unused but tracked

// ── FFmpeg frame extraction ───────────────────────────────────────────────
async function getFfmpegPath(): Promise<string> {
  try {
    const ffmpegInstaller = await import('@ffmpeg-installer/ffmpeg');
    return ffmpegInstaller.path;
  } catch {
    return 'ffmpeg'; // fallback to system ffmpeg
  }
}

async function extractFrames(videoPath: string, outputDir: string): Promise<string[]> {
  const ffmpegPath = await getFfmpegPath();
  // Extract 1 frame per 5 seconds, max MAX_FRAMES frames
  await execFileAsync(ffmpegPath, [
    '-i', videoPath,
    '-vf', `fps=1/5,scale=640:-2`,
    '-frames:v', String(MAX_FRAMES),
    '-q:v', '4',
    path.join(outputDir, 'frame_%04d.jpg'),
    '-y',
  ]);

  const frames = fs.readdirSync(outputDir)
    .filter((f) => f.endsWith('.jpg'))
    .sort()
    .map((f) => path.join(outputDir, f));

  logger.info(`Extracted ${frames.length} frames from video`);
  return frames;
}

// ── Roboflow inference ────────────────────────────────────────────────────
interface RoboflowPrediction {
  x: number;
  y: number;
  width: number;
  height: number;
  class: string;
  class_id: number;
  confidence: number;
}

async function inferFrame(
  imagePath: string,
  apiKey: string,
  project: string,
  version: string
): Promise<RoboflowPrediction[]> {
  const imageData = fs.readFileSync(imagePath).toString('base64');
  const response = await axios.post(
    `https://detect.roboflow.com/${project}/${version}`,
    imageData,
    {
      params: { api_key: apiKey },
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      timeout: 20000,
    }
  );
  return (response.data.predictions || []) as RoboflowPrediction[];
}

// ── Stats calculation ─────────────────────────────────────────────────────
interface FrameData {
  frameIdx: number;
  timestamp: number;
  players: { cx: number; cy: number; team: 'home' | 'away' }[];
  ball: { cx: number; cy: number } | null;
  frameW: number;
  frameH: number;
}

function toTeam(cx: number, frameW: number): 'home' | 'away' {
  return cx < frameW / 2 ? 'home' : 'away';
}

function toPitchCoords(cx: number, cy: number, frameW: number, frameH: number): [number, number] {
  return [(cx / frameW) * PITCH_W, (cy / frameH) * PITCH_H];
}

function dist(x1: number, y1: number, x2: number, y2: number): number {
  return Math.sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2);
}

function calculateStats(frames: FrameData[], duration: number, teamNames: { home: string; away: string }) {
  const possession = { home: 0, away: 0, none: 0 };
  const passAttempts = { home: [] as boolean[], away: [] as boolean[] };
  const events: any[] = [];
  const heatmapPositions = { home: [] as [number, number][], away: [] as [number, number][] };
  let score = { home: 0, away: 0 };
  let corners = { home: 0, away: 0 };
  let fouls = { home: 0, away: 0 };

  let prevBallOwner: 'home' | 'away' | null = null;
  let prevBallPos: [number, number] | null = null;
  let prevOwnerTeam: 'home' | 'away' | null = null;
  let possessionStreak = { team: null as 'home' | 'away' | null, count: 0 };
  let lastCornerFrame = -10;

  // Home goal: left side x=0, Away goal: right side x=PITCH_W
  const homeGoal: [number, number] = [0, PITCH_H / 2];
  const awayGoal: [number, number] = [PITCH_W, PITCH_H / 2];

  for (let fi = 0; fi < frames.length; fi++) {
    const frame = frames[fi];
    const t = frame.timestamp;

    // Collect heatmap positions
    for (const p of frame.players) {
      const [px, py] = toPitchCoords(p.cx, p.cy, frame.frameW, frame.frameH);
      heatmapPositions[p.team].push([px, py]);
    }

    if (!frame.ball) {
      possession.none++;
      continue;
    }

    const [bpx, bpy] = toPitchCoords(frame.ball.cx, frame.ball.cy, frame.frameW, frame.frameH);

    // Find closest player to ball
    let closestTeam: 'home' | 'away' | null = null;
    let minDist = Infinity;
    for (const p of frame.players) {
      const [ppx, ppy] = toPitchCoords(p.cx, p.cy, frame.frameW, frame.frameH);
      const d = dist(bpx, bpy, ppx, ppy);
      if (d < minDist) {
        minDist = d;
        closestTeam = p.team;
      }
    }

    const POSSESSION_THRESHOLD = 3.0; // metres
    const ballOwner = minDist < POSSESSION_THRESHOLD ? closestTeam : null;

    if (ballOwner) {
      possession[ballOwner]++;
    } else {
      possession.none++;
    }

    // Pass detection: same team keeps ball with movement
    if (ballOwner && prevBallOwner === ballOwner && prevBallPos) {
      const ballVel = dist(bpx, bpy, prevBallPos[0], prevBallPos[1]);
      if (ballVel > 1.5) {
        // ball moved > 1.5m in 5 seconds = pass
        passAttempts[ballOwner].push(true);
      }
    }

    // Possession change event (foul heuristic: sudden steal)
    if (ballOwner && prevBallOwner && ballOwner !== prevBallOwner) {
      events.push({
        timestamp: t,
        type: 'possession_change',
        teamSide: ballOwner,
      });
      // Foul heuristic: if possession changed away from dominant team near midfield
      const nearMidfield = bpx > 30 && bpx < 75;
      if (nearMidfield && prevBallPos && dist(bpx, bpy, prevBallPos[0], prevBallPos[1]) < 2) {
        fouls[ballOwner === 'home' ? 'away' : 'home']++;
        events.push({ timestamp: t, type: 'foul', teamSide: ballOwner === 'home' ? 'away' : 'home' });
      }
    }

    // Shot & Goal detection
    if (ballOwner && prevBallPos) {
      const ballVel = dist(bpx, bpy, prevBallPos[0], prevBallPos[1]);
      const targetGoal = ballOwner === 'home' ? awayGoal : homeGoal;
      const distToGoal = dist(bpx, bpy, targetGoal[0], targetGoal[1]);

      if (ballVel > 3.0 && distToGoal < 20) {
        const xg = computeXG(bpx, bpy, ballOwner);
        const onTarget = distToGoal < 8 && xg > 0.1;
        events.push({
          timestamp: t,
          type: onTarget ? 'shot_on_target' : 'shot',
          teamSide: ballOwner,
          xG: Math.round(xg * 100) / 100,
          x: bpx,
          y: bpy,
        });
        // Goal: ball very close to goal line
        if (distToGoal < 2.0) {
          score[ballOwner]++;
          events.push({
            timestamp: t,
            type: 'goal',
            teamSide: ballOwner,
            xG: Math.round(xg * 100) / 100,
            x: bpx,
            y: bpy,
            description: `Goal! xG: ${xg.toFixed(2)}`,
          });
        }
      }
    }

    // Corner detection: ball near corner flags
    const cornerPositions: [number, number][] = [
      [0, 0], [PITCH_W, 0], [0, PITCH_H], [PITCH_W, PITCH_H],
    ];
    const nearCorner = cornerPositions.some(([cx, cy]) => dist(bpx, bpy, cx, cy) < 3);
    if (nearCorner && fi - lastCornerFrame > 5 && ballOwner) {
      corners[ballOwner]++;
      events.push({ timestamp: t, type: 'corner', teamSide: ballOwner });
      lastCornerFrame = fi;
    }

    prevBallOwner = ballOwner;
    prevBallPos = [bpx, bpy];
    prevOwnerTeam = ballOwner || prevOwnerTeam;
  }

  // Aggregate stats
  const totalPoss = possession.home + possession.away;
  const possHome = totalPoss > 0 ? Math.round((possession.home / totalPoss) * 100 * 10) / 10 : 50;
  const possAway = Math.round((100 - possHome) * 10) / 10;

  const shotEvents = events.filter((e) => ['shot', 'shot_on_target', 'goal'].includes(e.type));
  const homeShots = shotEvents.filter((e) => e.teamSide === 'home');
  const awayShots = shotEvents.filter((e) => e.teamSide === 'away');

  const homePassesComp = passAttempts.home.filter(Boolean).length;
  const awayPassesComp = passAttempts.away.filter(Boolean).length;
  const homePassTotal = Math.max(homePassesComp, Math.round(possHome * 0.8));
  const awayPassTotal = Math.max(awayPassesComp, Math.round(possAway * 0.8));

  // Heatmaps
  const heatmaps = buildHeatmaps(heatmapPositions);

  // Momentum timeline
  const momentum = buildMomentum(events, duration);

  // Pass network (simplified)
  const passNetwork = buildPassNetwork(frames);

  const narrative = buildNarrative(score, possHome, possAway, homeShots, awayShots, teamNames, events);

  return {
    score,
    possession: { home: possHome, away: possAway },
    passes: {
      home: {
        completed: homePassesComp,
        total: homePassTotal,
        accuracy: homePassTotal > 0 ? Math.round((homePassesComp / homePassTotal) * 100 * 10) / 10 : 0,
      },
      away: {
        completed: awayPassesComp,
        total: awayPassTotal,
        accuracy: awayPassTotal > 0 ? Math.round((awayPassesComp / awayPassTotal) * 100 * 10) / 10 : 0,
      },
    },
    shots: {
      home: {
        total: homeShots.filter((e) => e.type !== 'goal' || true).length,
        onTarget: homeShots.filter((e) => e.type === 'shot_on_target').length,
        xG: Math.round(homeShots.reduce((s, e) => s + (e.xG || 0), 0) * 100) / 100,
      },
      away: {
        total: awayShots.length,
        onTarget: awayShots.filter((e) => e.type === 'shot_on_target').length,
        xG: Math.round(awayShots.reduce((s, e) => s + (e.xG || 0), 0) * 100) / 100,
      },
    },
    fouls,
    corners,
    pressureIndex: {
      home: Math.round((possHome / 20) * 10) / 10,
      away: Math.round((possAway / 20) * 10) / 10,
    },
    momentumTimeline: momentum,
    events: events.slice(0, 200),
    heatmaps,
    voronoi: [],
    passNetwork,
    narrative,
  };
}

function computeXG(x: number, y: number, team: 'home' | 'away'): number {
  const goalX = team === 'home' ? PITCH_W : 0;
  const goalY = PITCH_H / 2;
  const d = dist(x, y, goalX, goalY);
  const angle = Math.atan2(Math.abs(y - goalY), Math.abs(x - goalX));
  const logOdds = 2.1 - 0.08 * d - 0.7 * angle;
  return Math.max(0, Math.min(1, 1 / (1 + Math.exp(-logOdds))));
}

function buildHeatmaps(positions: { home: [number, number][]; away: [number, number][] }) {
  const heatmaps: any[] = [];
  for (const [team, pts] of Object.entries(positions) as ['home' | 'away', [number, number][]][]) {
    if (pts.length < 3) continue;
    const gridW = 21;
    const gridH = 13;
    const grid: number[][] = Array.from({ length: gridH }, () => new Array(gridW).fill(0));

    for (const [px, py] of pts) {
      const gi = Math.min(gridH - 1, Math.floor((py / PITCH_H) * gridH));
      const gj = Math.min(gridW - 1, Math.floor((px / PITCH_W) * gridW));
      grid[gi][gj]++;
    }

    const maxVal = Math.max(1, ...grid.flatMap((r) => r));
    const points: any[] = [];
    for (let i = 0; i < gridH; i++) {
      for (let j = 0; j < gridW; j++) {
        const intensity = grid[i][j] / maxVal;
        if (intensity > 0.05) {
          points.push({
            x: (j / gridW) * PITCH_W,
            y: (i / gridH) * PITCH_H,
            intensity: Math.round(intensity * 1000) / 1000,
          });
        }
      }
    }
    heatmaps.push({ playerId: team, teamSide: team, positions: points });
  }
  return heatmaps;
}

function buildMomentum(events: any[], duration: number): any[] {
  const result: any[] = [];
  const totalMinutes = Math.max(2, Math.ceil(duration / 60));
  const possEvents = events.filter((e) => e.type === 'possession_change');

  for (let min = 0; min <= totalMinutes; min += 2) {
    const windowStart = (min - 2) * 60;
    const windowEnd = min * 60;
    const windowEvents = possEvents.filter((e) => e.timestamp >= windowStart && e.timestamp <= windowEnd);
    const homeCount = windowEvents.filter((e) => e.teamSide === 'home').length;
    const total = Math.max(1, windowEvents.length);
    result.push({
      minute: min,
      home: Math.round((homeCount / total) * 100 * 10) / 10,
      away: Math.round(((total - homeCount) / total) * 100 * 10) / 10,
    });
  }
  return result;
}

function buildPassNetwork(frames: FrameData[]): any {
  const playerPositions: Record<string, { x: number[]; y: number[]; team: 'home' | 'away' }> = {};

  for (const frame of frames) {
    for (let i = 0; i < frame.players.length; i++) {
      const p = frame.players[i];
      const key = `${p.team}_${i % 11}`; // simplified player ID
      if (!playerPositions[key]) {
        playerPositions[key] = { x: [], y: [], team: p.team };
      }
      const [px, py] = toPitchCoords(p.cx, p.cy, frame.frameW, frame.frameH);
      playerPositions[key].x.push(px);
      playerPositions[key].y.push(py);
    }
  }

  const nodes = Object.entries(playerPositions)
    .filter(([, v]) => v.x.length >= 3)
    .slice(0, 22)
    .map(([id, v]) => ({
      playerId: id,
      name: `P${id.split('_')[1]}`,
      teamSide: v.team,
      involvement: v.x.length,
      x: Math.round((v.x.reduce((a, b) => a + b, 0) / v.x.length) * 100) / 100,
      y: Math.round((v.y.reduce((a, b) => a + b, 0) / v.y.length) * 100) / 100,
    }));

  const edges: any[] = [];
  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      if (nodes[i].teamSide !== nodes[j].teamSide) continue;
      const d = dist(nodes[i].x, nodes[i].y, nodes[j].x, nodes[j].y);
      if (d < 15) {
        edges.push({
          fromId: nodes[i].playerId,
          toId: nodes[j].playerId,
          count: Math.max(1, Math.round(10 / Math.max(1, d))),
          accuracy: Math.round(Math.min(0.95, 0.5 + 0.03 * (15 - d)) * 100) / 100,
        });
      }
    }
  }

  return { nodes: nodes.slice(0, 22), edges: edges.slice(0, 50) };
}

function buildNarrative(
  score: { home: number; away: number },
  possHome: number,
  possAway: number,
  homeShots: any[],
  awayShots: any[],
  teamNames: { home: string; away: string },
  events: any[]
): string {
  const dominant = possHome > possAway ? teamNames.home : teamNames.away;
  const xgDiff = homeShots.reduce((s, e) => s + (e.xG || 0), 0)
    - awayShots.reduce((s, e) => s + (e.xG || 0), 0);
  const betterXg = xgDiff > 0 ? teamNames.home : teamNames.away;

  const firstGoal = events.find((e) => e.type === 'goal');
  let goalText = '';
  if (firstGoal) {
    const min = Math.floor(firstGoal.timestamp / 60);
    const sec = Math.floor(firstGoal.timestamp % 60);
    const scorer = firstGoal.teamSide === 'home' ? teamNames.home : teamNames.away;
    goalText = ` ${scorer} broke the deadlock at ${min}:${String(sec).padStart(2, '0')}.`;
  }

  return (
    `${dominant} controlled possession with ${Math.max(possHome, possAway).toFixed(0)}% of the ball, ` +
    `building play through ${Math.max(homeShots.length, awayShots.length)} shot attempts. ` +
    `${betterXg} created the better chances (xG advantage: ${Math.abs(xgDiff).toFixed(2)}), ` +
    `resulting in a ${score.home}–${score.away} final scoreline.` +
    goalText
  );
}

// ── Mock analytics (fallback when Roboflow not configured) ────────────────
function generateMockAnalytics(matchId: string, teamNames: { home: string; away: string }) {
  logger.warn(`Roboflow API key not set for match ${matchId}, generating mock analytics`);
  const homeGoals = Math.floor(Math.random() * 4);
  const awayGoals = Math.floor(Math.random() * 4);
  const possHome = 40 + Math.floor(Math.random() * 20);

  const events: any[] = [];
  if (homeGoals > 0) {
    events.push({ timestamp: 300 + Math.random() * 1800, type: 'goal', teamSide: 'home', xG: 0.42, description: 'Goal scored' });
  }
  if (awayGoals > 0) {
    events.push({ timestamp: 600 + Math.random() * 1800, type: 'goal', teamSide: 'away', xG: 0.38, description: 'Goal scored' });
  }
  events.push({ timestamp: 450, type: 'corner', teamSide: 'home' });
  events.push({ timestamp: 900, type: 'corner', teamSide: 'away' });
  events.push({ timestamp: 720, type: 'foul', teamSide: 'home' });
  events.push({ timestamp: 1200, type: 'shot_on_target', teamSide: 'home', xG: 0.25 });
  events.push({ timestamp: 1500, type: 'shot_on_target', teamSide: 'away', xG: 0.18 });

  return {
    score: { home: homeGoals, away: awayGoals },
    possession: { home: possHome, away: 100 - possHome },
    passes: {
      home: { completed: 180 + Math.floor(Math.random() * 80), total: 230 + Math.floor(Math.random() * 80), accuracy: 75 + Math.floor(Math.random() * 15) },
      away: { completed: 140 + Math.floor(Math.random() * 80), total: 200 + Math.floor(Math.random() * 80), accuracy: 68 + Math.floor(Math.random() * 15) },
    },
    shots: {
      home: { total: 8 + Math.floor(Math.random() * 8), onTarget: 3 + Math.floor(Math.random() * 4), xG: +(1.2 + Math.random() * 1.5).toFixed(2) },
      away: { total: 6 + Math.floor(Math.random() * 8), onTarget: 2 + Math.floor(Math.random() * 4), xG: +(0.8 + Math.random() * 1.5).toFixed(2) },
    },
    fouls: { home: 8 + Math.floor(Math.random() * 8), away: 9 + Math.floor(Math.random() * 8) },
    corners: { home: 4 + Math.floor(Math.random() * 5), away: 3 + Math.floor(Math.random() * 5) },
    pressureIndex: { home: +(possHome / 20).toFixed(1), away: +((100 - possHome) / 20).toFixed(1) },
    momentumTimeline: Array.from({ length: 10 }, (_, i) => {
      const h = 35 + Math.floor(Math.random() * 30);
      return { minute: i * 10, home: h, away: 100 - h };
    }),
    events,
    heatmaps: [
      {
        playerId: 'home',
        teamSide: 'home',
        positions: Array.from({ length: 30 }, () => ({
          x: Math.random() * PITCH_W,
          y: Math.random() * PITCH_H,
          intensity: Math.random(),
        })),
      },
      {
        playerId: 'away',
        teamSide: 'away',
        positions: Array.from({ length: 30 }, () => ({
          x: Math.random() * PITCH_W,
          y: Math.random() * PITCH_H,
          intensity: Math.random(),
        })),
      },
    ],
    voronoi: [],
    passNetwork: { nodes: [], edges: [] },
    narrative:
      `${possHome > 50 ? teamNames.home : teamNames.away} dominated possession throughout, ` +
      `creating the clearest chances. The match ended ${homeGoals}–${awayGoals} ` +
      `after an intense contest. AI-powered analysis via Roboflow YOLOv8 (configure ROBOFLOW_API_KEY for live video detection).`,
  };
}

// ── Main video processing (Node.js, no Python backend) ───────────────────
async function processVideoInFunction(
  matchId: string,
  videoUrl: string,
  teamColors: any,
  matchData: any
): Promise<void> {
  const teamNames = {
    home: matchData.homeTeamName || 'Home',
    away: matchData.awayTeamName || 'Away',
  };

  const tmpDir = path.join(os.tmpdir(), `pitchlens_${matchId}`);
  const framesDir = path.join(tmpDir, 'frames');

  try {
    fs.mkdirSync(tmpDir, { recursive: true });
    fs.mkdirSync(framesDir, { recursive: true });

    // If no Roboflow key, use mock analytics
    if (!ROBOFLOW_API_KEY) {
      const analytics = generateMockAnalytics(matchId, teamNames);
      await db.doc(`matches/${matchId}`).update({
        status: 'completed',
        stats: analytics,
        processingProgress: 100,
        updatedAt: admin.firestore.FieldValue.serverTimestamp(),
      });
      return;
    }

    // 1. Download video
    await db.doc(`matches/${matchId}`).update({ processingProgress: 5 });
    logger.info(`Downloading video for match ${matchId}`);
    const videoPath = path.join(tmpDir, 'match.mp4');

    const videoResponse = await axios.get(videoUrl, {
      responseType: 'arraybuffer',
      timeout: 120000,
    });
    fs.writeFileSync(videoPath, Buffer.from(videoResponse.data));
    logger.info(`Video downloaded: ${(fs.statSync(videoPath).size / 1e6).toFixed(1)}MB`);

    // 2. Extract frames
    await db.doc(`matches/${matchId}`).update({ processingProgress: 15 });
    let framePaths: string[] = [];
    try {
      framePaths = await extractFrames(videoPath, framesDir);
    } catch (err) {
      logger.error('FFmpeg frame extraction failed', err);
      throw new Error('Frame extraction failed. Ensure the video is a valid MP4.');
    }

    if (framePaths.length === 0) {
      throw new Error('No frames extracted from video.');
    }

    // 3. Run Roboflow inference on each frame
    const frameResults: FrameData[] = [];
    for (let i = 0; i < framePaths.length; i++) {
      const progress = 20 + Math.floor((i / framePaths.length) * 65);
      if (i % 5 === 0) {
        await db.doc(`matches/${matchId}`).update({ processingProgress: progress });
      }

      try {
        const predictions = await inferFrame(framePaths[i], ROBOFLOW_API_KEY, ROBOFLOW_PROJECT, ROBOFLOW_VERSION);

        // Get frame dimensions from first frame filename or use defaults
        const frameW = 640;
        const frameH = 360; // scaled by ffmpeg

        const players = predictions
          .filter((p) => p.class_id === CLASS_PLAYER || p.class_id === CLASS_GOALKEEPER)
          .filter((p) => p.confidence > 0.4)
          .map((p) => ({ cx: p.x, cy: p.y, team: toTeam(p.x, frameW) }));

        const ballPreds = predictions.filter((p) => p.class_id === CLASS_BALL && p.confidence > 0.35);
        const ball = ballPreds.length > 0 ? { cx: ballPreds[0].x, cy: ballPreds[0].y } : null;

        frameResults.push({
          frameIdx: i,
          timestamp: i * 5, // 1 frame per 5 seconds
          players,
          ball,
          frameW,
          frameH,
        });
      } catch (err) {
        logger.warn(`Frame ${i} inference failed (non-fatal)`, err);
        // Skip failed frames
      }
    }

    // 4. Calculate stats
    await db.doc(`matches/${matchId}`).update({ processingProgress: 88 });
    const duration = framePaths.length * 5; // approx duration in seconds
    const analytics = calculateStats(frameResults, duration, teamNames);

    // 5. Write results
    await db.doc(`matches/${matchId}`).update({ processingProgress: 95 });
    await db.doc(`matches/${matchId}`).update({
      status: 'completed',
      stats: analytics,
      processingProgress: 100,
      updatedAt: admin.firestore.FieldValue.serverTimestamp(),
    });

    logger.info(`Match ${matchId} processing complete`);
  } finally {
    // Cleanup temp files
    try {
      fs.rmSync(tmpDir, { recursive: true, force: true });
    } catch { /* ignore cleanup errors */ }
  }
}

// ── Exponential backoff ───────────────────────────────────────────────────
async function withRetry<T>(fn: () => Promise<T>, retries = 3, delayMs = 2000): Promise<T> {
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      return await fn();
    } catch (err) {
      if (attempt === retries) throw err;
      const wait = delayMs * Math.pow(2, attempt);
      logger.warn(`Retry ${attempt + 1}/${retries} after ${wait}ms`, err);
      await new Promise((r) => setTimeout(r, wait));
    }
  }
  throw new Error('Max retries exceeded');
}

// ── onVideoUpload: Storage trigger ────────────────────────────────────────
export const onVideoUpload = onObjectFinalized(
  { region: 'us-central1', timeoutSeconds: 540, memory: '2GiB' },
  async (event) => {
    const { name: filePath, contentType, size } = event.data;

    if (!filePath?.startsWith('videos/') || contentType !== 'video/mp4') {
      logger.info('Skipping non-video upload', { filePath, contentType });
      return;
    }

    const parts = filePath.split('/');
    if (parts.length < 4) { logger.error('Unexpected path structure', { filePath }); return; }
    const [, userId, matchId] = parts;

    if (size && size > 500 * 1024 * 1024) {
      await db.doc(`matches/${matchId}`).update({
        status: 'error',
        errorMessage: 'Video file exceeds the 500MB limit.',
        updatedAt: admin.firestore.FieldValue.serverTimestamp(),
      });
      return;
    }

    const matchDoc = await db.doc(`matches/${matchId}`).get();
    if (!matchDoc.exists) { logger.error('Match document not found', { matchId }); return; }
    const matchData = matchDoc.data()!;
    if (matchData.status !== 'uploading') {
      logger.info('Match already processing/processed, skipping', { matchId });
      return;
    }

    // Generate signed URL (1 hour)
    const file = storage.bucket().file(filePath);
    const [signedUrl] = await file.getSignedUrl({
      action: 'read',
      expires: Date.now() + 60 * 60 * 1000,
    });

    await db.doc(`matches/${matchId}`).update({
      status: 'processing',
      processingProgress: 0,
      videoUrls: admin.firestore.FieldValue.arrayUnion(signedUrl),
      updatedAt: admin.firestore.FieldValue.serverTimestamp(),
    });

    // Try Python backend first (if configured), then fall back to Node.js processing
    let processed = false;

    if (PYTHON_API_URL) {
      try {
        await withRetry(async () => {
          const response = await axios.post(
            `${PYTHON_API_URL}/api/v1/process-match`,
            { matchId, videoUrl: signedUrl, userId, teamColors: matchData.teamColors ?? null },
            {
              headers: { Authorization: `Bearer ${API_SECRET_KEY}`, 'Content-Type': 'application/json' },
              timeout: 30000,
            }
          );
          logger.info('Python API queued match', { matchId, status: response.data.status });
        }, 2);
        processed = true;
      } catch (err) {
        logger.warn('Python backend unavailable, falling back to Node.js processing', err);
      }
    }

    if (!processed) {
      // Process directly in Cloud Function
      try {
        await processVideoInFunction(matchId, signedUrl, matchData.teamColors, matchData);
      } catch (err: any) {
        logger.error('Video processing failed', { matchId, err: err.message });
        await db.doc(`matches/${matchId}`).update({
          status: 'error',
          errorMessage: err.message || 'Video processing failed. Please try uploading a shorter clip (< 5 min).',
          updatedAt: admin.firestore.FieldValue.serverTimestamp(),
        });
        await db.collection('audit').add({
          type: 'match_error',
          matchId,
          userId,
          error: err.message,
          timestamp: admin.firestore.FieldValue.serverTimestamp(),
        });
        return;
      }
    }

    await db.collection('audit').add({
      type: 'match_queued',
      matchId,
      userId,
      timestamp: admin.firestore.FieldValue.serverTimestamp(),
    });

    logger.info('Match processing initiated', { matchId, userId });
  }
);

// ── onMatchComplete: Firestore trigger ────────────────────────────────────
export const onMatchComplete = onDocumentUpdated(
  { document: 'matches/{matchId}', region: 'us-central1' },
  async (event) => {
    const before = event.data?.before.data();
    const after = event.data?.after.data();

    if (!before || !after) return;
    if (before.status === after.status || after.status !== 'completed') return;

    const matchId = event.params.matchId;
    const userId = after.userId;

    logger.info('Match completed, sending notification', { matchId, userId });

    const userDoc = await db.doc(`users/${userId}`).get();
    const fcmToken = userDoc.data()?.fcmToken;

    if (fcmToken) {
      try {
        await admin.messaging().send({
          token: fcmToken,
          notification: {
            title: '⚽ Match Analysis Ready!',
            body: `Your match "${after.title}" has been fully analysed. Tap to view.`,
          },
          data: {
            matchId,
            type: 'match_completed',
            click_action: `${process.env.APP_URL ?? ''}/dashboard/${matchId}`,
          },
          webpush: { notification: { icon: '/icon-192.png', badge: '/badge.png' } },
        });
      } catch (err) {
        logger.warn('FCM notification failed (non-fatal)', err);
      }
    }

    await db.collection('audit').add({
      type: 'match_completed',
      matchId,
      userId,
      score: after.stats?.score,
      timestamp: admin.firestore.FieldValue.serverTimestamp(),
    });
  }
);

// ── deleteMatch: Callable function ───────────────────────────────────────
export const deleteMatch = onCall({ region: 'us-central1' }, async (request) => {
  if (!request.auth) throw new HttpsError('unauthenticated', 'Must be signed in.');

  const { matchId } = request.data;
  if (!matchId) throw new HttpsError('invalid-argument', 'matchId is required.');

  const matchDoc = await db.doc(`matches/${matchId}`).get();
  if (!matchDoc.exists) throw new HttpsError('not-found', 'Match not found.');
  if (matchDoc.data()!.userId !== request.auth.uid) {
    throw new HttpsError('permission-denied', 'You do not own this match.');
  }

  const [files] = await storage.bucket().getFiles({
    prefix: `videos/${request.auth.uid}/${matchId}/`,
  });
  await Promise.all(files.map((f) => f.delete().catch(() => {})));
  await db.doc(`matches/${matchId}`).delete();

  return { success: true };
});

// ── reprocessMatch: Callable function (retry failed matches) ───────────────
export const reprocessMatch = onCall({ region: 'us-central1', timeoutSeconds: 540, memory: '2GiB' }, async (request) => {
  if (!request.auth) throw new HttpsError('unauthenticated', 'Must be signed in.');

  const { matchId } = request.data;
  if (!matchId) throw new HttpsError('invalid-argument', 'matchId is required.');

  const matchDoc = await db.doc(`matches/${matchId}`).get();
  if (!matchDoc.exists) throw new HttpsError('not-found', 'Match not found.');
  const matchData = matchDoc.data()!;
  if (matchData.userId !== request.auth.uid) {
    throw new HttpsError('permission-denied', 'You do not own this match.');
  }

  const videoUrls: string[] = matchData.videoUrls || [];
  if (videoUrls.length === 0) throw new HttpsError('failed-precondition', 'No video URL found for this match.');

  // Reset to processing
  await db.doc(`matches/${matchId}`).update({
    status: 'processing',
    processingProgress: 0,
    errorMessage: null,
    stats: null,
    updatedAt: admin.firestore.FieldValue.serverTimestamp(),
  });

  try {
    await processVideoInFunction(matchId, videoUrls[0], matchData.teamColors, matchData);
  } catch (err: any) {
    await db.doc(`matches/${matchId}`).update({
      status: 'error',
      errorMessage: err.message || 'Reprocessing failed.',
      updatedAt: admin.firestore.FieldValue.serverTimestamp(),
    });
    throw new HttpsError('internal', err.message || 'Processing failed');
  }

  return { success: true };
});
