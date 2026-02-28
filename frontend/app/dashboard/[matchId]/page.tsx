'use client';
import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { Loader2, FileDown, AlertCircle, ChevronLeft, Activity, Grid, Network } from 'lucide-react';
import { Navbar } from '@/components/ui/Navbar';
import { PitchSVG } from '@/components/pitch/PitchSVG';
import {
  PossessionDonut,
  ShotsBars,
  MomentumLine,
  PassAccuracyBars,
} from '@/components/charts/StatsCharts';
import { useMatch } from '@/lib/hooks/useMatch';
import { formatTimestamp } from '@/lib/utils/analytics';
import { cn } from '@/lib/utils/cn';
import type { MatchEvent } from '@/lib/types';

type PitchMode = 'heatmap' | 'voronoi' | 'passnetwork';

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

const EVENT_LABELS: Record<string, string> = {
  goal: '⚽ Goal',
  shot: '🎯 Shot',
  shot_on_target: '🎯 On Target',
  foul: '🚨 Foul',
  corner: '🚩 Corner',
  possession_change: '↔ Poss.',
  pass: '→ Pass',
  pressure: '⚡ Press',
};

export default function DashboardPage() {
  const { matchId } = useParams<{ matchId: string }>();
  const { match, loading } = useMatch(matchId);
  const router = useRouter();
  const [pitchMode, setPitchMode] = useState<PitchMode>('heatmap');
  const [activeTab, setActiveTab] = useState<'overview' | 'events' | 'passes'>('overview');

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
          {isError && <ErrorCard message={match.errorMessage} />}

          {/* Score */}
          {stats && (
            <div className="grid grid-cols-3 glass-card p-6 text-center">
              <div>
                <p className="text-pitch-muted text-xs uppercase tracking-widest mb-1">{match.homeTeamName}</p>
                <p className="text-5xl font-black text-pitch-white">{stats.score.home}</p>
              </div>
              <div className="flex items-center justify-center">
                <span className="text-pitch-indigo-glow font-bold text-xl">VS</span>
              </div>
              <div>
                <p className="text-pitch-muted text-xs uppercase tracking-widest mb-1">{match.awayTeamName}</p>
                <p className="text-5xl font-black text-pitch-white">{stats.score.away}</p>
              </div>
            </div>
          )}

          {stats && (
            <div className="grid lg:grid-cols-[280px,1fr,260px] gap-6">
              {/* Left: Event Timeline */}
              <div className="glass-card p-4 space-y-3">
                <h2 className="text-sm font-semibold text-pitch-white uppercase tracking-widest">Event Log</h2>
                <div className="space-y-2 max-h-[500px] overflow-y-auto no-scrollbar pr-1">
                  {stats.events
                    .filter((e) => ['goal', 'shot_on_target', 'foul', 'corner'].includes(e.type))
                    .sort((a, b) => a.timestamp - b.timestamp)
                    .map((event, i) => (
                      <EventRow key={i} event={event} />
                    ))}
                  {stats.events.filter((e) => ['goal', 'shot_on_target', 'foul', 'corner'].includes(e.type)).length === 0 && (
                    <p className="text-pitch-muted text-xs italic text-center py-4">No key events recorded</p>
                  )}
                </div>
              </div>

              {/* Centre: Pitch visualization */}
              <div className="glass-card p-4 space-y-4">
                <div className="flex items-center justify-between">
                  <h2 className="text-sm font-semibold text-pitch-white uppercase tracking-widest">Pitch View</h2>
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
                  <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-red-500" />{match.homeTeamName}</span>
                  <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-blue-500" />{match.awayTeamName}</span>
                </div>
              </div>

              {/* Right: Charts */}
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
                  <h3 className="text-xs font-semibold text-pitch-muted uppercase tracking-widest mb-3">Key Stats</h3>
                  <ShotsBars stats={stats} homeTeamName={match.homeTeamName} awayTeamName={match.awayTeamName} />
                </div>
              </div>
            </div>
          )}

          {/* Bottom tabs */}
          {stats && (
            <div className="glass-card p-6 space-y-4">
              <div className="flex gap-1 border-b border-pitch-indigo-soft/20 pb-3">
                {(['overview', 'events', 'passes'] as const).map((tab) => (
                  <button
                    key={tab}
                    onClick={() => setActiveTab(tab)}
                    className={cn(
                      'px-4 py-1.5 rounded-lg text-sm font-medium capitalize transition-all',
                      activeTab === tab
                        ? 'bg-pitch-indigo-soft/30 text-pitch-white'
                        : 'text-pitch-muted hover:text-pitch-white'
                    )}
                  >
                    {tab}
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

              {activeTab === 'events' && (
                <div className="space-y-2">
                  {stats.events.sort((a, b) => a.timestamp - b.timestamp).map((e, i) => (
                    <EventRow key={i} event={e} expanded />
                  ))}
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

function EventRow({ event, expanded }: { event: MatchEvent; expanded?: boolean }) {
  const colorClass = EVENT_COLORS[event.type] ?? 'bg-gray-500/20 text-gray-400 border-gray-500/30';
  const label = EVENT_LABELS[event.type] ?? event.type;

  return (
    <div className="flex items-start gap-3 py-1.5">
      <span className="text-pitch-muted text-xs font-mono shrink-0 pt-0.5 w-12 text-right">
        {formatTimestamp(event.timestamp)}
      </span>
      <span className={cn('event-pill border', colorClass, 'shrink-0')}>{label}</span>
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
  const configs = {
    uploading: { color: 'bg-yellow-500/20 text-yellow-400', label: 'Uploading…' },
    processing: { color: 'bg-blue-500/20 text-blue-400', label: `Processing${progress ? ` ${progress}%` : '…'}` },
    completed: { color: 'bg-green-500/20 text-green-400', label: 'Completed' },
    error: { color: 'bg-red-500/20 text-red-400', label: 'Error' },
  };
  const { color, label } = configs[status as keyof typeof configs] ?? configs.processing;
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
  return (
    <div className="glass-card p-6 text-center space-y-4">
      <div className="w-12 h-12 mx-auto bg-pitch-indigo-soft/20 rounded-full flex items-center justify-center">
        <Loader2 className="animate-spin text-pitch-indigo-glow" size={24} />
      </div>
      <div>
        <p className="text-pitch-white font-semibold">Analysing your match…</p>
        <p className="text-pitch-muted text-sm mt-1">
          YOLOv8 is detecting players, ByteTrack is building trajectories. This takes under 60 seconds.
        </p>
      </div>
      {match.processingProgress && (
        <div className="max-w-xs mx-auto">
          <div className="h-1.5 bg-pitch-indigo-deep rounded-full overflow-hidden">
            <div
              className="h-full bg-pitch-green rounded-full transition-all"
              style={{ width: `${match.processingProgress}%` }}
            />
          </div>
          <p className="text-pitch-muted text-xs mt-1">{match.processingProgress}%</p>
        </div>
      )}
    </div>
  );
}

function ErrorCard({ message }: { message?: string }) {
  return (
    <div className="glass-card p-6 border-red-500/30 flex items-start gap-4">
      <AlertCircle className="text-red-400 shrink-0 mt-0.5" size={20} />
      <div>
        <p className="text-pitch-white font-semibold">Processing Failed</p>
        <p className="text-pitch-muted text-sm mt-1">
          {message || 'An unexpected error occurred. Please try uploading again.'}
        </p>
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
