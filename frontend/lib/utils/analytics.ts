import type { MatchStats, MatchEvent } from '@/lib/types';
import { format } from 'date-fns';

export function formatTimestamp(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

export function generateNarrative(stats: MatchStats): string {
  const { score, possession, shots, passes } = stats;
  const xGDiff = (shots.home.xG - shots.away.xG).toFixed(2);
  const dominantTeam = possession.home > possession.away ? 'Home' : 'Away';
  const underdog = dominantTeam === 'Home' ? 'Away' : 'Home';

  const narrativeParts = [
    `${dominantTeam} Team controlled the tempo with ${Math.max(possession.home, possession.away)}% possession,` +
      ` weaving ${passes[dominantTeam === 'Home' ? 'home' : 'away'].completed} completed passes across a compact five-a-side canvas.`,
    `Yet possession yielded uneven opportunity: Home's xG stood at ${shots.home.xG.toFixed(2)} against Away's ${shots.away.xG.toFixed(2)},` +
      ` a delta of ${xGDiff} reflecting ${parseFloat(xGDiff) > 0 ? 'Home\'s cutting edge' : 'Away\'s clinical efficiency'}.`,
    `The scoreline—${score.home}–${score.away}—${
      Math.abs(score.home - score.away) <= 1 ? 'belies the razor-thin margins separating these sides' : 'tells a story of decisive moments'
    }.`,
    generateKeyMomentNarrative(stats.events),
  ];

  return narrativeParts.join(' ');
}

function generateKeyMomentNarrative(events: MatchEvent[]): string {
  const goals = events.filter((e) => e.type === 'goal');
  if (goals.length === 0) return 'Both sides fought fiercely, but the net remained silent.';

  const firstGoal = goals[0];
  const lastGoal = goals[goals.length - 1];
  return (
    `The game's fulcrum tilted at ${formatTimestamp(firstGoal.timestamp)} when ${firstGoal.teamSide === 'home' ? 'Home' : 'Away'} broke the deadlock` +
    (firstGoal.xG ? ` (xG: ${firstGoal.xG.toFixed(2)})` : '') +
    (goals.length > 1
      ? `, with the decisive blow landing at ${formatTimestamp(lastGoal.timestamp)}.`
      : ', a lead they would not relinquish.')
  );
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function estimateETA(bytesTransferred: number, totalBytes: number, startTime: number): string {
  const elapsed = (Date.now() - startTime) / 1000;
  const rate = bytesTransferred / elapsed;
  if (rate === 0) return '--';
  const remaining = (totalBytes - bytesTransferred) / rate;
  if (remaining < 60) return `${Math.ceil(remaining)}s`;
  return `${Math.ceil(remaining / 60)}m`;
}

export const SOCCER_TRIVIA = [
  'In five-a-side, possession correlates 0.78 with win probability.',
  'The average five-a-side match sees 40% more shots per minute than 11-a-side.',
  'Voronoi analysis reveals that dominant space control predicts goals 3 minutes before they happen.',
  'Expected Goals (xG) was pioneered by Opta Sports in the early 2010s.',
  'ByteTrack can maintain 98.4% tracking accuracy at 30 FPS on HD video.',
  'Roberto Carlos\'s famous free kick in 1997 had an exit velocity of 137 km/h.',
  'In five-a-side, the goalkeeper touches the ball every 45 seconds on average.',
  'Pass completion above 80% in the final third is a strong predictor of five-a-side victory.',
  'Pressure index — opponent proximity during possession — correlates with turnovers at r=0.72.',
  'YOLOv8 processes 1080p frames in under 8ms on a modern GPU.',
];
