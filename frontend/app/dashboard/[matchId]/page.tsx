'use client';
import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import {
  Loader2, FileDown, AlertCircle, ChevronLeft, Activity,
  Grid, Network, RefreshCw, BarChart2,
} from 'lucide-react';
import { Navbar } from '@/components/ui/Navbar';
import { PitchSVG } from '@/components/pitch/PitchSVG';
import {
  PossessionDonut,
  ShotsBars,
  MomentumLine,
  PassAccuracyBars,
} from '@/components/charts/StatsCharts';
import { useMatch } from '@/lib/hooks/useMatch';
import { reprocessMatch } from '@/lib/firebase/firestore';
import { formatTimestamp } from '@/lib/utils/analytics';
import { cn } from '@/lib/utils/cn';
import toast from 'react-hot-toast';
import type { MatchEvent } from '@/lib/types';

type PitchMode = 'heatmap' | 'voronoi' | 'passnetwork';
type Tab = 'overview' | 'stats' | 'events' | 'passes';

const EVENT_COLORS: Record<string, string> = {
  goal: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
  shot: 'bg-red-500/20 text-red-400 border-red-500/30',
  shot_on_target: 'bg-orange-500/20 text-orange-400 border-orange-500/30',
  foul: 'bg-purple-500/20 text-purple-400 border-purple-500/30',
  corner: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  possession_change: 'bg-gray-500/20 text-gray-400 border-gray-500/30',
  pass: 'bg-green-500/20 text-green-400 border-green-500/30',
  pressure: 'bg-pink-500/20 text-pink-400 border-pink-500/30',
};

const EVENT_ICONS: Record<string, string> = {
  goal: '⚽',
  shot: '🎯',
  shot_on_target: '🎯',
  foul: '🟨',
  corner: '🚩',
  possession_change: '↔',
  pass: '→',
  pressure: '⚡',
};

const EVENT_LABELS: Record<string, string> = {
  goal: 'Goal',
  shot: 'Shot',
  shot_on_target: 'On Target',
  foul: 'Foul',
  corner: 'Corner',
  possession_change: 'Poss. Change',
  pass: 'Pass',
  pressure: 'Pressure',
};

export default function DashboardPage() {
  const { matchId } = useParams<{ matchId: string }>();
  const { match, loading } = useMatch(matchId);
  const router = useRouter();
  const [pitchMode, setPitchMode] = useState<PitchMode>('heatmap');
  const [activeTab, setActiveTab] = useState<Tab>('overview');
  const [retrying, setRetrying] = useState(false);

  const handleRetry = async () => {
    setRetrying(true);
    try {
      await reprocessMatch(matchId);
      toast.success('Reprocessing started!');
    } catch (err: any) {
      toast.error(err.message || 'Retry failed. Please re-upload the video.');
    } finally {
      setRetrying(false);
    }
  };

  if (loading) return <LoadingScreen />;
  if (!match) return <NotFound />;

  const stats = match.stats;
  const isProcessing = match.status === 'processing' || match.status === 'uploading';
  const isError = match.status === 'error';

  return (
    <>
      <Navbar />
      <main className="min-h-screen pt-20 pb-16 px-4">
        <div className="max-w-7xl mx-auto space-y-6">
          {/* Header */}
          <div className="flex items-start justify-between gap-4 flex-wrap">
            <div>
              <Link
                href="/dashboard"
                className="flex items-center gap-1 text-pitch-muted hover:text-pitch-white text-sm mb-2 transition-colors"
              >
                <ChevronLeft size={16} /> All Matches
              </Link>
              <h1 className="text-2xl font-bold text-pitch-white">{match.title}</h1>
              <div className="flex items-center gap-3 mt-1.5">
                <StatusBadge status={match.status} progress={match.processingProgress} />
              </div>
            </div>
            {match.status === 'completed' && (
              <Link href={`/report/${matchId}`} className="pitch-button-secondary gap-2">
                <FileDown size={16} /> Export Report
              </Link>
            )}
          </div>

          {/* Processing state */}
          {isProcessing && <ProcessingCard match={match} />}
          {isError && <ErrorCard message={match.errorMessage} onRetry={handleRetry} retrying={retrying} />}

          {/* Demo mode notice */}
          {stats && (
            <div className="flex items-center gap-3 px-4 py-2.5 rounded-xl bg-pitch-indigo-deep/60 border border-pitch-indigo-soft/20 text-xs text-pitch-muted">
              <span className="shrink-0">🔬</span>
              <span>
                <span className="text-pitch-white font-medium">Demo analytics</span>
                {' '}— stats are generated from your video metadata.{' '}
                <a href="https://roboflow.com" target="_blank" rel="noopener noreferrer" className="text-pitch-indigo-glow hover:underline">
                  Add a Roboflow API key
                </a>
                {' '}to Vercel environment variables to enable live AI player detection.
              </span>
            </div>
          )}

          {/* Score Board — Sofascore style */}
          {stats && (
            <motion.div
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              className="glass-card overflow-hidden"
            >
              {/* Score header */}
              <div className="bg-pitch-indigo-deep/60 px-6 py-5 grid grid-cols-[1fr,auto,1fr] items-center gap-4">
                <div className="text-center">
                  <div
                    className="w-10 h-10 rounded-full mx-auto mb-2 border-2 border-white/10"
                    style={{ backgroundColor: match.homeTeamColor || '#ef4444' }}
                  />
                  <p className="text-pitch-white font-bold text-sm">{match.homeTeamName}</p>
                  <p className="text-pitch-muted text-xs">Home</p>
                </div>
                <div className="text-center px-4">
                  <p className="text-6xl font-black text-pitch-white tracking-tight">
                    {stats.score.home}
                    <span className="text-pitch-muted mx-2">–</span>
                    {stats.score.away}
                  </p>
                  <p className="text-pitch-muted text-xs mt-1 uppercase tracking-widest">Full Time</p>
                </div>
                <div className="text-center">
                  <div
                    className="w-10 h-10 rounded-full mx-auto mb-2 border-2 border-white/10"
                    style={{ backgroundColor: match.awayTeamColor || '#3b82f6' }}
                  />
                  <p className="text-pitch-white font-bold text-sm">{match.awayTeamName}</p>
                  <p className="text-pitch-muted text-xs">Away</p>
                </div>
              </div>

              {/* Quick stats bar */}
              <div className="grid grid-cols-3 sm:grid-cols-6 divide-x divide-pitch-indigo-soft/20 border-t border-pitch-indigo-soft/20">
                {[
                  { label: 'Possession', home: `${stats.possession.home}%`, away: `${stats.possession.away}%` },
                  { label: 'Shots', home: stats.shots.home.total, away: stats.shots.away.total },
                  { label: 'On Target', home: stats.shots.home.onTarget, away: stats.shots.away.onTarget },
                  { label: 'Corners', home: stats.corners.home, away: stats.corners.away },
                  { label: 'Fouls', home: stats.fouls.home, away: stats.fouls.away },
                  { label: 'xG', home: stats.shots.home.xG.toFixed(2), away: stats.shots.away.xG.toFixed(2) },
                ].map(({ label, home, away }) => (
                  <div key={label} className="py-3 px-2 text-center">
                    <p className="text-pitch-muted text-xs mb-1">{label}</p>
                    <div className="flex justify-center items-center gap-1 text-xs font-bold">
                      <span className="text-red-400">{home}</span>
                      <span className="text-pitch-muted">–</span>
                      <span className="text-blue-400">{away}</span>
                    </div>
                  </div>
                ))}
              </div>
            </motion.div>
          )}

          {/* Main grid */}
          {stats && (
            <div className="grid lg:grid-cols-[260px,1fr,280px] gap-6">
              {/* Left: Event Timeline */}
              <div className="glass-card p-4 space-y-3">
                <h2 className="text-xs font-semibold text-pitch-muted uppercase tracking-widest">Key Events</h2>
                <div className="space-y-1 max-h-[480px] overflow-y-auto no-scrollbar">
                  {stats.events
                    .filter((e) => ['goal', 'shot_on_target', 'foul', 'corner'].includes(e.type))
                    .sort((a, b) => a.timestamp - b.timestamp)
                    .map((event, i) => <EventRow key={i} event={event} />)}
                  {stats.events.filter((e) =>
                    ['goal', 'shot_on_target', 'foul', 'corner'].includes(e.type)).length === 0 && (
                    <p className="text-pitch-muted text-xs italic text-center py-4">No key events recorded</p>
                  )}
                </div>
              </div>

              {/* Centre: Pitch */}
              <div className="glass-card p-4 space-y-4">
                <div className="flex items-center justify-between">
                  <h2 className="text-xs font-semibold text-pitch-muted uppercase tracking-widest">Pitch View</h2>
                  <div className="flex gap-1">
                    {([
                      { mode: 'heatmap', icon: Activity, label: 'Heat' },
                      { mode: 'voronoi', icon: Grid, label: 'Space' },
                      { mode: 'passnetwork', icon: Network, label: 'Passes' },
                    ] as const).map(({ mode, icon: Icon, label }) => (
                      <button
                        key={mode}
                        onClick={() => setPitchMode(mode)}
                        className={cn(
                          'flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs transition-all',
                          pitchMode === mode
                            ? 'bg-pitch-green/20 text-pitch-green border border-pitch-green/30'
                            : 'text-pitch-muted hover:text-pitch-white hover:bg-pitch-indigo-deep'
                        )}
                      >
                        <Icon size={12} />
                        {label}
                      </button>
                    ))}
                  </div>
                </div>
                <PitchSVG
                  heatmaps={stats.heatmaps}
                  voronoi={stats.voronoi}
                  passNetwork={stats.passNetwork}
                  mode={pitchMode}
                />
                <div className="flex gap-4 justify-center text-xs text-pitch-muted">
                  <span className="flex items-center gap-1.5">
                    <span className="w-2 h-2 rounded-full bg-red-500" />{match.homeTeamName}
                  </span>
                  <span className="flex items-center gap-1.5">
                    <span className="w-2 h-2 rounded-full bg-blue-500" />{match.awayTeamName}
                  </span>
                </div>
              </div>

              {/* Right: Stats */}
              <div className="space-y-4">
                <div className="glass-card p-4">
                  <h3 className="text-xs font-semibold text-pitch-muted uppercase tracking-widest mb-3">Possession</h3>
                  <PossessionDonut stats={stats} homeTeamName={match.homeTeamName} awayTeamName={match.awayTeamName} />
                  <div className="flex justify-center gap-4 text-xs mt-2">
                    <span className="text-red-400 font-bold">{stats.possession.home}%</span>
                    <span className="text-pitch-muted">vs</span>
                    <span className="text-blue-400 font-bold">{stats.possession.away}%</span>
                  </div>
                </div>
                <div className="glass-card p-4">
                  <h3 className="text-xs font-semibold text-pitch-muted uppercase tracking-widest mb-3">Attacking</h3>
                  <ShotsBars stats={stats} homeTeamName={match.homeTeamName} awayTeamName={match.awayTeamName} />
                </div>
              </div>
            </div>
          )}

          {/* Bottom tabs */}
          {stats && (
            <div className="glass-card p-6 space-y-4">
              <div className="flex gap-1 border-b border-pitch-indigo-soft/20 pb-3 overflow-x-auto">
                {(['overview', 'stats', 'events', 'passes'] as const).map((tab) => (
                  <button
                    key={tab}
                    onClick={() => setActiveTab(tab)}
                    className={cn(
                      'px-4 py-1.5 rounded-lg text-sm font-medium capitalize transition-all whitespace-nowrap',
                      activeTab === tab
                        ? 'bg-pitch-indigo-soft/30 text-pitch-white'
                        : 'text-pitch-muted hover:text-pitch-white'
                    )}
                  >
                    {tab === 'stats' ? '📊 Full Stats' : tab === 'overview' ? '📈 Overview' : tab === 'events' ? '📋 Events' : '🔗 Passes'}
                  </button>
                ))}
              </div>

              {activeTab === 'overview' && (
                <div className="space-y-6">
                  <div>
                    <h3 className="text-xs font-semibold text-pitch-muted uppercase tracking-widest mb-3">Momentum Shift</h3>
                    <MomentumLine stats={stats} homeTeamName={match.homeTeamName} awayTeamName={match.awayTeamName} />
                  </div>
                  <div>
                    <h3 className="text-xs font-semibold text-pitch-muted uppercase tracking-widest mb-3">Match Narrative</h3>
                    <p className="text-pitch-muted text-sm leading-relaxed italic border-l-2 border-pitch-green/40 pl-4">
                      "{stats.narrative}"
                    </p>
                  </div>
                </div>
              )}

              {activeTab === 'stats' && (
                <div>
                  <SofascoreStatsTable stats={stats} homeTeamName={match.homeTeamName} awayTeamName={match.awayTeamName} />
                </div>
              )}

              {activeTab === 'events' && (
                <div className="space-y-1.5">
                  {stats.events
                    .filter((e) => e.type !== 'possession_change')
                    .sort((a, b) => a.timestamp - b.timestamp)
                    .map((e, i) => <EventRow key={i} event={e} expanded />)}
                  {stats.events.filter((e) => e.type !== 'possession_change').length === 0 && (
                    <p className="text-pitch-muted text-sm text-center py-6">No events to display</p>
                  )}
                </div>
              )}

              {activeTab === 'passes' && (
                <div>
                  <h3 className="text-xs font-semibold text-pitch-muted uppercase tracking-widest mb-3">Pass Accuracy</h3>
                  <PassAccuracyBars stats={stats} homeTeamName={match.homeTeamName} awayTeamName={match.awayTeamName} />
                  <div className="grid grid-cols-2 gap-4 mt-6">
                    {(['home', 'away'] as const).map((side) => (
                      <div key={side} className="space-y-2">
                        <p className="text-xs font-semibold text-pitch-muted uppercase tracking-widest">
                          {side === 'home' ? match.homeTeamName : match.awayTeamName}
                        </p>
                        <StatRow label="Completed" value={stats.passes[side].completed} />
                        <StatRow label="Total" value={stats.passes[side].total} />
                        <StatRow label="Accuracy" value={`${stats.passes[side].accuracy}%`} />
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </main>
    </>
  );
}

// ── Sofascore-style full stats table ─────────────────────────────────────
function SofascoreStatsTable({ stats, homeTeamName, awayTeamName }: { stats: any; homeTeamName: string; awayTeamName: string }) {
  const rows = [
    { category: 'Attack', items: [
      { label: 'Goals', home: stats.score.home, away: stats.score.away, key: 'goals' },
      { label: 'Shots', home: stats.shots.home.total, away: stats.shots.away.total, key: 'shots' },
      { label: 'Shots on Target', home: stats.shots.home.onTarget, away: stats.shots.away.onTarget, key: 'sot' },
      { label: 'Expected Goals (xG)', home: stats.shots.home.xG.toFixed(2), away: stats.shots.away.xG.toFixed(2), key: 'xg', isDecimal: true },
    ]},
    { category: 'Possession', items: [
      { label: 'Ball Possession', home: `${stats.possession.home}%`, away: `${stats.possession.away}%`, key: 'poss', isPercent: true, homeVal: stats.possession.home, awayVal: stats.possession.away },
      { label: 'Pressure Index', home: stats.pressureIndex?.home?.toFixed(1) ?? '—', away: stats.pressureIndex?.away?.toFixed(1) ?? '—', key: 'press' },
    ]},
    { category: 'Passing', items: [
      { label: 'Passes Completed', home: stats.passes.home.completed, away: stats.passes.away.completed, key: 'pass_comp' },
      { label: 'Total Passes', home: stats.passes.home.total, away: stats.passes.away.total, key: 'pass_total' },
      { label: 'Pass Accuracy', home: `${stats.passes.home.accuracy}%`, away: `${stats.passes.away.accuracy}%`, key: 'pass_acc', isPercent: true, homeVal: stats.passes.home.accuracy, awayVal: stats.passes.away.accuracy },
    ]},
    { category: 'Discipline', items: [
      { label: 'Fouls', home: stats.fouls.home, away: stats.fouls.away, key: 'fouls', lowerIsBetter: true },
      { label: 'Corners', home: stats.corners.home, away: stats.corners.away, key: 'corners' },
    ]},
  ];

  return (
    <div className="space-y-6">
      {/* Team headers */}
      <div className="grid grid-cols-[1fr,160px,1fr] items-center text-center text-sm font-semibold">
        <span className="text-red-400 text-left">{homeTeamName}</span>
        <span className="text-pitch-muted text-xs uppercase tracking-widest">Stat</span>
        <span className="text-blue-400 text-right">{awayTeamName}</span>
      </div>

      {rows.map(({ category, items }) => (
        <div key={category}>
          <p className="text-xs font-semibold text-pitch-muted uppercase tracking-widest mb-2">{category}</p>
          <div className="space-y-2">
            {items.map((row) => {
              const homeNum = typeof row.home === 'string' ? parseFloat(row.home) : Number(row.home);
              const awayNum = typeof row.away === 'string' ? parseFloat(row.away) : Number(row.away);
              const total = homeNum + awayNum || 1;
              const homeW = Math.round((homeNum / total) * 100);
              const awayW = 100 - homeW;
              const lowerBetter = (row as any).lowerIsBetter;
              const homeWins = lowerBetter ? homeNum < awayNum : homeNum > awayNum;
              const awayWins = lowerBetter ? awayNum < homeNum : awayNum > homeNum;

              return (
                <div key={row.key} className="space-y-1">
                  <div className="grid grid-cols-[1fr,160px,1fr] items-center text-sm">
                    <span className={cn('font-bold', homeWins ? 'text-pitch-white' : 'text-pitch-muted')}>{row.home}</span>
                    <span className="text-center text-pitch-muted text-xs">{row.label}</span>
                    <span className={cn('font-bold text-right', awayWins ? 'text-pitch-white' : 'text-pitch-muted')}>{row.away}</span>
                  </div>
                  {/* Bar */}
                  <div className="flex h-1 rounded-full overflow-hidden bg-pitch-indigo-deep/40">
                    <div className="bg-red-500/70 rounded-full" style={{ width: `${homeW}%` }} />
                    <div className="bg-blue-500/70 rounded-full flex-1" />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

function EventRow({ event, expanded }: { event: MatchEvent; expanded?: boolean }) {
  const colorClass = EVENT_COLORS[event.type] ?? 'bg-gray-500/20 text-gray-400 border-gray-500/30';
  const icon = EVENT_ICONS[event.type] ?? '•';
  const label = EVENT_LABELS[event.type] ?? event.type;

  return (
    <div className="flex items-start gap-3 py-1.5">
      <span className="text-pitch-muted text-xs font-mono shrink-0 pt-0.5 w-12 text-right">
        {formatTimestamp(event.timestamp)}
      </span>
      <span className={cn('event-pill border text-xs px-2 py-0.5 rounded-full shrink-0', colorClass)}>
        {icon} {label}
      </span>
      {expanded && (
        <span className="text-pitch-muted text-xs">
          {event.description || `${event.teamSide === 'home' ? 'Home' : 'Away'} team`}
          {event.xG ? <span className="ml-1 text-pitch-indigo-glow">xG {event.xG.toFixed(2)}</span> : null}
        </span>
      )}
    </div>
  );
}

function StatRow({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="flex justify-between text-sm">
      <span className="text-pitch-muted">{label}</span>
      <span className="text-pitch-white font-medium">{value}</span>
    </div>
  );
}

function StatusBadge({ status, progress }: { status: string; progress?: number }) {
  const configs: Record<string, { color: string; label: string }> = {
    uploading: { color: 'bg-yellow-500/20 text-yellow-400', label: 'Uploading…' },
    processing: { color: 'bg-blue-500/20 text-blue-400', label: `Processing${progress ? ` ${progress}%` : '…'}` },
    completed: { color: 'bg-green-500/20 text-green-400', label: 'Completed' },
    error: { color: 'bg-red-500/20 text-red-400', label: 'Error' },
  };
  const { color, label } = configs[status] ?? configs.processing;
  return (
    <span className={cn('inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium', color)}>
      {['uploading', 'processing'].includes(status) && (
        <span className="w-1.5 h-1.5 rounded-full bg-current animate-pulse" />
      )}
      {label}
    </span>
  );
}

function ProcessingCard({ match }: { match: any }) {
  const stages = [
    'Downloading video…',
    'Extracting frames…',
    'Running YOLOv8 detection…',
    'Tracking players…',
    'Computing statistics…',
    'Building heatmaps…',
    'Finalising report…',
  ];
  const prog = match.processingProgress ?? 0;
  const stageIdx = Math.min(stages.length - 1, Math.floor((prog / 100) * stages.length));

  return (
    <div className="glass-card p-6 text-center space-y-4">
      <div className="w-14 h-14 mx-auto bg-pitch-indigo-soft/20 rounded-full flex items-center justify-center">
        <Loader2 className="animate-spin text-pitch-indigo-glow" size={28} />
      </div>
      <div>
        <p className="text-pitch-white font-semibold">Analysing your match…</p>
        <p className="text-pitch-muted text-sm mt-1">{stages[stageIdx]}</p>
      </div>
      <div className="max-w-sm mx-auto">
        <div className="h-2 bg-pitch-indigo-deep rounded-full overflow-hidden">
          <motion.div
            className="h-full bg-gradient-to-r from-pitch-green to-emerald-400 rounded-full"
            animate={{ width: `${prog}%` }}
            transition={{ duration: 0.5 }}
          />
        </div>
        <p className="text-pitch-muted text-xs mt-2">{prog}% · Powered by Roboflow YOLOv8 + ByteTrack</p>
      </div>
    </div>
  );
}

function ErrorCard({ message, onRetry, retrying }: { message?: string; onRetry: () => void; retrying: boolean }) {
  return (
    <div className="glass-card p-6 border border-red-500/20 flex items-start gap-4">
      <AlertCircle className="text-red-400 shrink-0 mt-0.5" size={20} />
      <div className="flex-1">
        <p className="text-pitch-white font-semibold">Processing Failed</p>
        <p className="text-pitch-muted text-sm mt-1">
          {message || 'An unexpected error occurred. Please try uploading again.'}
        </p>
        <button
          onClick={onRetry}
          disabled={retrying}
          className="mt-3 flex items-center gap-2 px-4 py-2 bg-red-500/20 hover:bg-red-500/30 text-red-400 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
        >
          {retrying ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
          {retrying ? 'Retrying…' : 'Retry Analysis'}
        </button>
      </div>
    </div>
  );
}

function LoadingScreen() {
  return (
    <>
      <Navbar />
      <main className="min-h-screen pt-20 flex items-center justify-center">
        <Loader2 className="animate-spin text-pitch-indigo-glow" size={40} />
      </main>
    </>
  );
}

function NotFound() {
  return (
    <>
      <Navbar />
      <main className="min-h-screen pt-20 flex flex-col items-center justify-center gap-4">
        <p className="text-pitch-white text-xl font-semibold">Match not found</p>
        <Link href="/dashboard" className="pitch-button-secondary">Back to Dashboard</Link>
      </main>
    </>
  );
}

function cn(...classes: (string | undefined | false)[]) {
  return classes.filter(Boolean).join(' ');
}
