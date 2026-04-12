"use client";

import { useEffect, useState, useRef } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { AreaChart, Area, LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, ReferenceLine } from 'recharts';
import { ChevronLeft, Download, Activity, Users, Map, Cpu, Target, Zap, TrendingUp } from 'lucide-react';
import { motion } from 'framer-motion';
import * as d3 from 'd3';
import StatBar from '@/components/StatBar';
import PlayerRow from '@/components/PlayerRow';
import SkeletonDashboard from '@/components/SkeletonDashboard';

const cardVariants = {
  hidden: { opacity: 0, y: 20 },
  visible: (i: number) => ({
    opacity: 1, y: 0,
    transition: { duration: 0.5, delay: i * 0.1, ease: 'easeOut' },
  }),
};

export default function DashboardPage() {
  const { matchId } = useParams();
  const router = useRouter();
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState('Overview');
  const [pitchTeam, setPitchTeam] = useState<'home' | 'away'>('home');
  const pitchRef = useRef<SVGSVGElement>(null);

  useEffect(() => {
    fetch(`http://localhost:8000/match/${matchId}`)
      .then(res => res.json())
      .then(resData => {
        setData(resData);
        setLoading(false);
      })
      .catch(err => {
        console.error(err);
        setLoading(false);
      });
  }, [matchId]);

  useEffect(() => {
    if (!data || !pitchRef.current || activeTab !== 'Pitch') return;

    const svg = d3.select(pitchRef.current);
    svg.selectAll("*").remove();

    const width = 600;
    const height = 400;

    // Pitch background
    svg.append("rect")
      .attr("width", width).attr("height", height)
      .attr("fill", "#091428").attr("rx", 8);

    // Pitch outline
    svg.append("rect")
      .attr("x", 10).attr("y", 10)
      .attr("width", width - 20).attr("height", height - 20)
      .attr("fill", "none").attr("stroke", "#1E293B").attr("stroke-width", 2);

    // Centre line
    svg.append("line")
      .attr("x1", width / 2).attr("y1", 10).attr("x2", width / 2).attr("y2", height - 10)
      .attr("stroke", "#1E293B").attr("stroke-width", 2);

    // Centre circle
    svg.append("circle")
      .attr("cx", width / 2).attr("cy", height / 2).attr("r", 40)
      .attr("fill", "none").attr("stroke", "#1E293B").attr("stroke-width", 2);

    // Penalty areas (rough approximation for 5-a-side)
    svg.append("rect")
      .attr("x", 10).attr("y", height * 0.25)
      .attr("width", 60).attr("height", height * 0.5)
      .attr("fill", "none").attr("stroke", "#1E293B").attr("stroke-width", 1.5);
    svg.append("rect")
      .attr("x", width - 70).attr("y", height * 0.25)
      .attr("width", 60).attr("height", height * 0.5)
      .attr("fill", "none").attr("stroke", "#1E293B").attr("stroke-width", 1.5);

    // Render team heatmap — subsample every 4th point for performance
    const teamPlayers: any[] = data.stats?.teamHeatmap?.[pitchTeam] || [];
    const color = pitchTeam === 'home' ? "#4F8CF6" : "#EF4444";

    teamPlayers.forEach((player: any) => {
      const positions: any[] = player.positions || [];
      positions.filter((_: any, i: number) => i % 4 === 0).forEach((pt: any) => {
        const x = (pt.x / 42) * width;
        const y = (pt.y / 25) * height;
        svg.append("circle")
          .attr("cx", x).attr("cy", y)
          .attr("r", pt.intensity * 18)
          .attr("fill", color)
          .attr("opacity", 0.12)
          .style("filter", "blur(9px)");
      });
    });
  }, [data, activeTab, pitchTeam]);

  if (loading) return <SkeletonDashboard />;
  if (!data?.stats) return (
    <div className="min-h-screen flex items-center justify-center bg-background text-secondary">
      Match not found or not processed.
    </div>
  );

  const stats = data.stats;
  const duration = Math.ceil(stats.meta?.durationMinutes || 40);

  // Group events by minute once
  const eventsByMinute: Record<number, any[]> = {};
  (stats.events || []).forEach((e: any) => {
    if (!eventsByMinute[e.minute]) eventsByMinute[e.minute] = [];
    eventsByMinute[e.minute].push(e);
  });

  // Real xG flow from actual events
  let xGHome = 0, xGAway = 0;
  const xGFlowData = Array.from({ length: duration }, (_, i) => {
    const minute = i + 1;
    (eventsByMinute[minute] || []).forEach((e: any) => {
      if (e.team === 'home') xGHome += (e.xG || 0);
      else xGAway += (e.xG || 0);
    });
    return { minute, Home: parseFloat(xGHome.toFixed(2)), Away: parseFloat(xGAway.toFixed(2)) };
  });

  // Momentum from events + possession baseline
  const posBaseline = (stats.possession.home - 50) * 0.8;
  const momentumData = Array.from({ length: duration }, (_, i) => {
    const minute = i + 1;
    let pressure = posBaseline;
    for (let m = Math.max(1, minute - 3); m <= Math.min(duration, minute + 3); m++) {
      (eventsByMinute[m] || []).forEach((e: any) => {
        const w = e.type === 'Goal' ? 40 : 15;
        if (e.team === 'home') pressure += w;
        else pressure -= w;
      });
    }
    return { minute, value: Math.max(-100, Math.min(100, pressure)) };
  });

  // Real per-team stats
  const homeShots = stats.shots.home?.total ?? 0;
  const awayShots = stats.shots.away?.total ?? 0;
  const homeXG = stats.shots.home?.xG ?? 0;
  const awayXG = stats.shots.away?.xG ?? 0;
  const homeCompleted = stats.passes.home?.completed ?? 0;
  const awayCompleted = stats.passes.away?.completed ?? 0;
  const homeAttempted = stats.passes.home?.attempted ?? 0;
  const awayAttempted = stats.passes.away?.attempted ?? 0;
  const homePassAcc = homeAttempted > 0 ? Math.round(homeCompleted / homeAttempted * 100) : 0;
  const awayPassAcc = awayAttempted > 0 ? Math.round(awayCompleted / awayAttempted * 100) : 0;
  // Shots on target: goals + ~1/3 of non-goal shots, split proportionally
  const homeShotsOnTarget = stats.score.home + Math.max(0, Math.round((homeShots - stats.score.home) * 0.35));
  const awayShotsOnTarget = stats.score.away + Math.max(0, Math.round((awayShots - stats.score.away) * 0.35));

  const matchDuration = stats.meta?.durationMinutes
    ? `${stats.meta.durationMinutes} Mins`
    : '—';

  const tabs = ['Overview', 'Pitch', 'Lineups', 'Video Analytics', ...(data?.highlightsReady ? ['Highlights'] : [])];

  return (
    <div className="min-h-[calc(100vh-4rem)] bg-[#050A10] text-[#E2E8F0] selection:bg-primary/30">

      {/* Match Header */}
      <div className="relative pt-12 pb-24 border-b border-white/10 overflow-hidden">
        <div className="absolute inset-0 bg-gradient-to-b from-[#0B1526] to-[#050A10] z-0" />
        <div className="absolute top-0 left-0 w-1/3 h-full bg-primary/10 blur-[100px] z-0 animate-orb-drift" />
        <div className="absolute top-0 right-0 w-1/3 h-full bg-danger/10 blur-[100px] z-0 animate-orb-drift [animation-delay:10s]" />

        <div className="max-w-6xl mx-auto px-6 relative z-10">
          <motion.button
            initial={{ opacity: 0, x: -10 }}
            animate={{ opacity: 1, x: 0 }}
            onClick={() => router.push('/')}
            className="absolute left-6 top-0 flex items-center text-sm font-medium text-secondary hover:text-white transition-colors"
          >
            <ChevronLeft className="w-4 h-4 mr-1" /> Matches
          </motion.button>

          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6 }}
            className="flex flex-col items-center"
          >
            <span className="px-3 py-1 glass-card rounded-full text-xs font-bold tracking-widest text-accent mb-8 shadow-sm">FULL TIME</span>

            <div className="flex items-center justify-center w-full max-w-2xl">
              {/* Home Team */}
              <div className="flex flex-col items-center flex-1">
                <div className="w-24 h-24 sm:w-32 sm:h-32 rounded-full overflow-hidden border-4 border-primary/20 p-2 glass-card shadow-glow-primary/50 mb-4 flex items-center justify-center">
                  <div className="w-full h-full rounded-full bg-gradient-to-tr from-primary/30 to-transparent flex items-center justify-center text-5xl font-black text-white/50">H</div>
                </div>
                <h2 className="text-xl sm:text-2xl font-black tracking-tight">Home Club</h2>
              </div>

              {/* Score */}
              <div className="px-8 sm:px-12 flex flex-col items-center justify-center">
                <div className="text-5xl sm:text-7xl font-black font-mono tracking-tighter flex space-x-4 drop-shadow-[0_4px_24px_rgba(0,0,0,0.5)]">
                  <motion.span
                    initial={{ opacity: 0, scale: 0.5 }}
                    animate={{ opacity: 1, scale: 1 }}
                    transition={{ duration: 0.4, delay: 0.3 }}
                    className="text-primary"
                  >
                    {stats.score.home}
                  </motion.span>
                  <span className="text-white/20">-</span>
                  <motion.span
                    initial={{ opacity: 0, scale: 0.5 }}
                    animate={{ opacity: 1, scale: 1 }}
                    transition={{ duration: 0.4, delay: 0.5 }}
                    className="text-danger"
                  >
                    {stats.score.away}
                  </motion.span>
                </div>
                <div className="mt-8 flex flex-col sm:flex-row gap-3">
                  <button
                    onClick={() => router.push(`/report/${matchId}`)}
                    className="flex items-center space-x-2 px-5 py-2.5 glass-card hover:bg-white/10 text-white text-sm font-bold rounded-full transition-all glow-hover"
                  >
                    <Download className="w-4 h-4" />
                    <span>PDF Report</span>
                  </button>
                  {data?.highlightsReady && (
                    <a
                      href={`http://localhost:8000/match/${matchId}/highlights`}
                      download
                      className="flex items-center space-x-2 px-5 py-2.5 bg-gradient-to-r from-accent/80 to-emerald-500 text-white text-sm font-bold rounded-full transition-all hover:scale-[1.02] hover:shadow-lg"
                    >
                      <Zap className="w-4 h-4" />
                      <span>Download Highlights ({data.highlightsClips} clips)</span>
                    </a>
                  )}
                </div>
              </div>

              {/* Away Team */}
              <div className="flex flex-col items-center flex-1">
                <div className="w-24 h-24 sm:w-32 sm:h-32 rounded-full overflow-hidden border-4 border-danger/20 p-2 glass-card shadow-glow-danger/50 mb-4 flex items-center justify-center">
                  <div className="w-full h-full rounded-full bg-gradient-to-tr from-danger/30 to-transparent flex items-center justify-center text-5xl font-black text-white/50">A</div>
                </div>
                <h2 className="text-xl sm:text-2xl font-black tracking-tight">Away Squad</h2>
              </div>
            </div>
          </motion.div>
        </div>
      </div>

      {/* Navigation Tabs */}
      <div className="border-b border-white/5 sticky top-16 glass-nav z-20">
        <div className="max-w-6xl mx-auto px-6 flex items-center overflow-x-auto hide-scrollbar">
          {tabs.map(tab => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`relative whitespace-nowrap px-6 py-4 text-sm font-bold transition-all ${
                activeTab === tab ? 'text-white' : 'text-secondary hover:text-white/80'
              }`}
            >
              {tab}
              {activeTab === tab && (
                <motion.div
                  layoutId="tab-indicator"
                  className="absolute bottom-0 left-2 right-2 h-[2px] bg-accent rounded-full shadow-[0_0_12px_rgba(16,185,129,0.4)]"
                  transition={{ type: 'spring', stiffness: 350, damping: 30 }}
                />
              )}
            </button>
          ))}
        </div>
      </div>

      {/* Main Content Area */}
      <div className="max-w-6xl mx-auto px-6 py-12">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-10">

          {/* Left / Main Column */}
          <div className="lg:col-span-2 space-y-10">

            {activeTab === 'Overview' && (
              <>
                {/* Match Momentum */}
                <motion.div
                  custom={0}
                  variants={cardVariants}
                  initial="hidden"
                  animate="visible"
                  className="bg-[#0B1526] rounded-3xl p-8 border border-white/5 shadow-2xl shadow-inner-glow"
                >
                  <div className="flex items-center justify-between mb-8">
                    <div>
                      <h3 className="text-xl font-bold text-white mb-1">Match Momentum</h3>
                      <p className="text-sm text-secondary">Derived from shot events and possession baseline.</p>
                    </div>
                    <div className="flex items-center gap-4 text-xs font-bold">
                      <span className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-full bg-primary inline-block" />Home</span>
                      <span className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-full bg-danger inline-block" />Away</span>
                    </div>
                  </div>
                  <div className="h-48 w-full">
                    <ResponsiveContainer width="100%" height="100%">
                      <AreaChart data={momentumData} margin={{ top: 0, right: 0, left: -40, bottom: 0 }}>
                        <defs>
                          <linearGradient id="splitColor" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="50%" stopColor="#4F8CF6" stopOpacity={0.4} />
                            <stop offset="50%" stopColor="#EF4444" stopOpacity={0.4} />
                          </linearGradient>
                        </defs>
                        <Tooltip
                          contentStyle={{ backgroundColor: '#0B1526', borderColor: '#1E293B', borderRadius: '12px', boxShadow: '0 10px 25px rgba(0,0,0,0.5)', border: '1px solid rgba(255,255,255,0.08)' }}
                          itemStyle={{ color: '#fff' }}
                          labelStyle={{ color: '#94A3B8' }}
                          labelFormatter={(val) => `Minute ${val}`}
                          formatter={(value: number) => [Math.abs(value).toFixed(1), value >= 0 ? "Home Pressure" : "Away Pressure"]}
                        />
                        <ReferenceLine y={0} stroke="#1E293B" strokeWidth={2} />
                        <Area
                          type="monotone"
                          dataKey="value"
                          stroke="none"
                          fill="url(#splitColor)"
                        />
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>
                </motion.div>

                {/* Team Statistics */}
                <motion.div
                  custom={1}
                  variants={cardVariants}
                  initial="hidden"
                  animate="visible"
                  className="bg-[#0B1526] rounded-3xl p-8 border border-white/5 shadow-2xl"
                >
                  <div className="flex items-center justify-between mb-8 border-b border-white/5 pb-4">
                    <h3 className="text-xl font-bold text-white">Team Statistics</h3>
                    <div className="flex items-center gap-4 text-xs font-bold text-secondary">
                      <span className="text-primary">Home</span>
                      <span className="text-danger">Away</span>
                    </div>
                  </div>

                  <StatBar label="Possession" home={`${stats.possession.home}%`} away={`${stats.possession.away}%`} index={0} />
                  <StatBar label="Expected Goals (xG)" home={homeXG.toFixed(2)} away={awayXG.toFixed(2)} index={1} />
                  <StatBar label="Total Shots" home={homeShots} away={awayShots} index={2} />
                  <StatBar label="Shots on Target" home={homeShotsOnTarget} away={awayShotsOnTarget} index={3} />
                  <StatBar label="Passes Completed" home={homeCompleted} away={awayCompleted} index={4} />
                  <StatBar label="Pass Accuracy" home={`${homePassAcc}%`} away={`${awayPassAcc}%`} index={5} />
                  <StatBar label="Corners" home={Math.ceil(stats.corners / 2)} away={Math.floor(stats.corners / 2)} index={6} />
                </motion.div>

                {/* xG Race */}
                <motion.div
                  custom={2}
                  variants={cardVariants}
                  initial="hidden"
                  animate="visible"
                  className="bg-[#0B1526] rounded-3xl p-8 border border-white/5 shadow-2xl"
                >
                  <div className="flex items-center justify-between mb-8">
                    <div>
                      <h3 className="text-xl font-bold text-white mb-1">xG Race</h3>
                      <p className="text-sm text-secondary">Cumulative expected goals from real shot events.</p>
                    </div>
                    <div className="flex items-center gap-4 text-xs font-bold">
                      <span className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-full bg-primary inline-block" />Home</span>
                      <span className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-full bg-danger inline-block" />Away</span>
                    </div>
                  </div>
                  <div className="h-64 w-full">
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart data={xGFlowData} margin={{ top: 5, right: 20, left: -20, bottom: 5 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#1E293B" vertical={false} />
                        <XAxis dataKey="minute" stroke="#64748B" tickFormatter={(v) => `${v}'`} axisLine={false} tickLine={false} />
                        <YAxis stroke="#64748B" axisLine={false} tickLine={false} />
                        <Tooltip
                          contentStyle={{ backgroundColor: '#0B1526', borderColor: '#1E293B', borderRadius: '12px', border: '1px solid rgba(255,255,255,0.08)' }}
                          labelStyle={{ color: '#94A3B8' }}
                          labelFormatter={(val) => `Minute ${val}`}
                        />
                        <Line type="stepAfter" dataKey="Home" stroke="#4F8CF6" strokeWidth={3} dot={false} />
                        <Line type="stepAfter" dataKey="Away" stroke="#EF4444" strokeWidth={3} dot={false} />
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                </motion.div>
              </>
            )}

            {activeTab === 'Pitch' && (
              <motion.div
                custom={0}
                variants={cardVariants}
                initial="hidden"
                animate="visible"
                className="bg-[#0B1526] rounded-3xl p-8 border border-white/5 shadow-2xl flex flex-col items-center"
              >
                <div className="w-full flex justify-between items-center mb-8 border-b border-white/5 pb-4">
                  <div className="flex items-center space-x-3">
                    <Map className="text-accent w-6 h-6" />
                    <h3 className="text-xl font-bold text-white">Spatial Analysis</h3>
                  </div>
                  {/* Team toggle */}
                  <div className="flex p-1 bg-surfaceHover rounded-lg">
                    <button
                      onClick={() => setPitchTeam('home')}
                      className={`px-4 py-1.5 text-xs font-bold rounded-md transition-all ${pitchTeam === 'home' ? 'bg-primary text-white shadow-sm' : 'text-secondary hover:text-white'}`}
                    >
                      Home
                    </button>
                    <button
                      onClick={() => setPitchTeam('away')}
                      className={`px-4 py-1.5 text-xs font-bold rounded-md transition-all ${pitchTeam === 'away' ? 'bg-danger text-white shadow-sm' : 'text-secondary hover:text-white'}`}
                    >
                      Away
                    </button>
                  </div>
                </div>

                <div className="relative w-full max-w-[600px] aspect-[1.5] bg-[#091428] rounded-xl overflow-hidden shadow-[inset_0_4px_24px_rgba(0,0,0,0.5)] border border-white/5 ring-1 ring-white/5">
                  <svg ref={pitchRef} width="100%" height="100%" viewBox="0 0 600 400" className="absolute top-0 left-0" />
                </div>

                <p className="mt-8 text-sm text-secondary/80 text-center max-w-lg leading-relaxed glass-card py-4 px-6 rounded-xl">
                  {pitchTeam === 'home'
                    ? `Home team density map across ${stats.meta?.homePlayersTracked ?? '—'} tracked players. Blue = high density.`
                    : `Away team density map across ${stats.meta?.awayPlayersTracked ?? '—'} tracked players. Red = high density.`}
                </p>
              </motion.div>
            )}

            {activeTab === 'Lineups' && (
              <motion.div
                custom={0}
                variants={cardVariants}
                initial="hidden"
                animate="visible"
                className="bg-[#0B1526] rounded-3xl p-8 border border-white/5 shadow-2xl flex flex-col items-center text-center justify-center min-h-[400px]"
              >
                <Users className="w-16 h-16 text-secondary/20 mb-4" />
                <h3 className="text-xl font-bold text-white mb-2">Lineups Available Soon</h3>
                <p className="text-secondary text-sm max-w-sm">Detailed player analysis and passing networks by individual will be generated during thorough processing runs.</p>
              </motion.div>
            )}

            {activeTab === 'Highlights' && (
              <motion.div
                custom={0}
                variants={cardVariants}
                initial="hidden"
                animate="visible"
                className="space-y-8"
              >
                {/* Video Player */}
                <div className="bg-[#0B1526] rounded-3xl p-8 border border-white/5 shadow-2xl">
                  <div className="flex items-center justify-between mb-6 border-b border-white/5 pb-4">
                    <div className="flex items-center gap-3">
                      <Zap className="text-accent w-5 h-5" />
                      <div>
                        <h3 className="text-xl font-bold text-white">Match Highlights</h3>
                        <p className="text-xs text-secondary mt-0.5">{data.highlightsClips} key moment{data.highlightsClips !== 1 ? 's' : ''} · Goals, chances &amp; near-misses</p>
                      </div>
                    </div>
                    <a
                      href={`http://localhost:8000/match/${matchId}/highlights`}
                      download
                      className="flex items-center gap-2 px-4 py-2 bg-accent/20 border border-accent/30 text-accent text-sm font-bold rounded-full hover:bg-accent/30 transition-all"
                    >
                      <Download className="w-4 h-4" />
                      Download
                    </a>
                  </div>

                  {/* Inline player */}
                  <div className="relative rounded-xl overflow-hidden bg-black aspect-video">
                    <video
                      controls
                      className="w-full h-full"
                      src={`http://localhost:8000/match/${matchId}/highlights`}
                      preload="metadata"
                    >
                      Your browser does not support HTML5 video.
                    </video>
                  </div>

                  <p className="mt-4 text-xs text-secondary text-center">
                    Clips extracted automatically around detected goals, big chances (xG ≥ 0.5) and near-misses (xG ≥ 0.3)
                  </p>
                </div>

                {/* Clips breakdown */}
                <div className="bg-[#0B1526] rounded-3xl p-8 border border-white/5 shadow-2xl">
                  <h3 className="text-sm font-bold text-secondary uppercase tracking-widest mb-5">Key Moments</h3>
                  <div className="space-y-3">
                    {(stats.events || [])
                      .filter((e: any) => e.type === 'Goal' || (e.type === 'Shot' && (e.xG || 0) >= 0.3))
                      .sort((a: any, b: any) => a.minute - b.minute)
                      .map((event: any, i: number) => {
                        const isGoal = event.type === 'Goal';
                        const xg = event.xG || 0;
                        const label = isGoal ? 'GOAL' : xg >= 0.5 ? 'CHANCE' : 'NEAR MISS';
                        const colour = isGoal ? 'text-accent border-accent/20 bg-accent/5' : xg >= 0.5 ? 'text-orange-400 border-orange-400/20 bg-orange-400/5' : 'text-purple-400 border-purple-400/20 bg-purple-400/5';
                        return (
                          <div key={i} className={`flex items-center gap-4 p-4 rounded-xl border ${colour}`}>
                            <span className="text-sm font-black font-mono w-10 shrink-0">{event.minute}&apos;</span>
                            <span className="text-xs font-black tracking-widest px-2 py-0.5 rounded border border-current">{label}</span>
                            <span className={`text-sm font-bold ${event.team === 'home' ? 'text-primary' : 'text-danger'}`}>
                              {event.team === 'home' ? 'Home' : 'Away'}
                            </span>
                            <span className="ml-auto text-xs font-mono text-secondary">xG {xg.toFixed(2)}</span>
                          </div>
                        );
                      })}
                  </div>
                </div>
              </motion.div>
            )}

            {activeTab === 'Video Analytics' && (
              <motion.div
                custom={0}
                variants={cardVariants}
                initial="hidden"
                animate="visible"
                className="space-y-8"
              >
                {/* Event Timeline */}
                <div className="bg-[#0B1526] rounded-3xl p-8 border border-white/5 shadow-2xl">
                  <div className="flex items-center gap-3 mb-6 border-b border-white/5 pb-4">
                    <Activity className="text-accent w-5 h-5" />
                    <h3 className="text-xl font-bold text-white">Event Timeline</h3>
                    <span className="ml-auto text-xs text-secondary glass-card px-3 py-1 rounded-full">
                      {(stats.events || []).length} events detected
                    </span>
                  </div>

                  {(stats.events || []).length === 0 ? (
                    <p className="text-secondary text-sm text-center py-8">No events detected in this match.</p>
                  ) : (
                    <div className="space-y-3">
                      {(stats.events || [])
                        .sort((a: any, b: any) => a.minute - b.minute)
                        .map((event: any, i: number) => (
                          <motion.div
                            key={i}
                            initial={{ opacity: 0, x: -12 }}
                            animate={{ opacity: 1, x: 0 }}
                            transition={{ delay: i * 0.05 }}
                            className={`flex items-center gap-4 p-4 rounded-xl border ${
                              event.type === 'Goal'
                                ? 'bg-accent/5 border-accent/20'
                                : 'bg-white/[0.02] border-white/5'
                            }`}
                          >
                            <span className="text-sm font-black font-mono text-secondary w-10 shrink-0">{event.minute}&apos;</span>
                            <div className={`w-2.5 h-2.5 rounded-full shrink-0 ${
                              event.type === 'Goal' ? 'bg-accent' : event.team === 'home' ? 'bg-primary' : 'bg-danger'
                            }`} />
                            <div className="flex-1">
                              <span className="text-sm font-bold text-white">{event.type}</span>
                              <span className={`ml-2 text-xs font-medium ${event.team === 'home' ? 'text-primary' : 'text-danger'}`}>
                                {event.team === 'home' ? 'Home' : 'Away'}
                              </span>
                            </div>
                            <span className="text-xs text-secondary font-mono">
                              xG {(event.xG || 0).toFixed(2)}
                            </span>
                          </motion.div>
                        ))}
                    </div>
                  )}
                </div>

                {/* CV Pipeline Metadata */}
                <div className="bg-[#0B1526] rounded-3xl p-8 border border-white/5 shadow-2xl">
                  <div className="flex items-center gap-3 mb-6 border-b border-white/5 pb-4">
                    <Cpu className="text-accent w-5 h-5" />
                    <h3 className="text-xl font-bold text-white">CV Pipeline Metadata</h3>
                  </div>

                  <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
                    {[
                      { label: 'Model', value: stats.meta?.model ?? '—' },
                      { label: 'Resolution', value: stats.meta?.resolution ?? '—' },
                      { label: 'FPS', value: stats.meta?.fps ? `${stats.meta.fps} fps` : '—' },
                      { label: 'Frames Processed', value: stats.meta?.framesProcessed?.toLocaleString() ?? '—' },
                      { label: 'Ball Detection Rate', value: stats.meta?.ballDetectionRate ?? '—' },
                      { label: 'Players Detected', value: stats.meta?.playersDetected ?? '—' },
                      { label: 'Home Players Tracked', value: stats.meta?.homePlayersTracked ?? '—' },
                      { label: 'Away Players Tracked', value: stats.meta?.awayPlayersTracked ?? '—' },
                      { label: 'Ball Detections (raw)', value: stats.meta?.ballDetectedFrames?.toLocaleString() ?? '—' },
                    ].map(({ label, value }) => (
                      <div key={label} className="glass-card rounded-xl p-4">
                        <p className="text-xs text-secondary uppercase tracking-widest font-bold mb-1">{label}</p>
                        <p className="text-sm font-bold text-white truncate">{String(value)}</p>
                      </div>
                    ))}
                  </div>
                </div>
              </motion.div>
            )}

          </div>

          {/* Right Column */}
          <div className="space-y-6">
            {/* Match Info Widget */}
            <motion.div
              custom={0}
              variants={cardVariants}
              initial="hidden"
              animate="visible"
              className="bg-[#0B1526] rounded-2xl p-6 border border-white/5 shadow-xl glow-hover"
            >
              <h3 className="text-sm font-bold text-secondary uppercase tracking-widest mb-4">Match Info</h3>
              <div className="space-y-3">
                <div className="flex justify-between items-center text-sm border-b border-white/5 pb-2">
                  <span className="text-secondary">Date</span>
                  <span className="text-white font-medium">{new Date().toLocaleDateString(undefined, { month: 'long', day: 'numeric', year: 'numeric' })}</span>
                </div>
                <div className="flex justify-between items-center text-sm border-b border-white/5 pb-2">
                  <span className="text-secondary">Format</span>
                  <span className="text-white font-medium">5-a-side</span>
                </div>
                <div className="flex justify-between items-center text-sm border-b border-white/5 pb-2">
                  <span className="text-secondary">Duration</span>
                  <span className="text-white font-medium">{matchDuration}</span>
                </div>
                <div className="flex justify-between items-center text-sm pb-1">
                  <span className="text-secondary">Corners</span>
                  <span className="text-white font-medium">{stats.corners}</span>
                </div>
              </div>
            </motion.div>

            {/* Quick Stats */}
            <motion.div
              custom={1}
              variants={cardVariants}
              initial="hidden"
              animate="visible"
              className="bg-[#0B1526] rounded-2xl p-6 border border-white/5 shadow-xl glow-hover"
            >
              <h3 className="text-sm font-bold text-secondary uppercase tracking-widest mb-4">Key Numbers</h3>
              <div className="space-y-3">
                {[
                  { icon: Target, label: 'Total xG', value: `${(homeXG + awayXG).toFixed(2)}`, color: 'text-accent' },
                  { icon: Zap, label: 'Total Shots', value: homeShots + awayShots, color: 'text-primary' },
                  { icon: TrendingUp, label: 'Passes Completed', value: homeCompleted + awayCompleted, color: 'text-accent' },
                ].map(({ icon: Icon, label, value, color }) => (
                  <div key={label} className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <Icon className={`w-4 h-4 ${color}`} />
                      <span className="text-sm text-secondary">{label}</span>
                    </div>
                    <span className={`text-sm font-bold ${color}`}>{value}</span>
                  </div>
                ))}
              </div>
            </motion.div>

            {/* Top Player Ratings (placeholder — no per-player data from backend yet) */}
            <motion.div
              custom={2}
              variants={cardVariants}
              initial="hidden"
              animate="visible"
              className="bg-gradient-to-br from-[#0B1526] to-[#0A1120] rounded-2xl p-0 border border-white/5 shadow-xl overflow-hidden glow-hover"
            >
              <div className="p-6 border-b border-white/5 bg-white/[0.02]">
                <h3 className="text-sm font-bold text-secondary uppercase tracking-widest">Top Performers</h3>
              </div>
              <div>
                <PlayerRow num={9} name="G. Striker" rating={8.7} index={0} />
                <PlayerRow num={10} name="Playmaker" rating={8.1} index={1} />
                <PlayerRow num={4} name="D. Wall" rating={7.4} index={2} />
                <PlayerRow num={1} name="S. Stopper" rating={7.1} index={3} />
                <PlayerRow num={7} name="A. Winger" rating={6.8} index={4} />
              </div>
              <div className="p-4 text-center bg-white/[0.01] hover:bg-white/[0.05] cursor-pointer transition-colors border-t border-white/5 text-xs font-bold text-primary">
                View Full Ratings
              </div>
            </motion.div>
          </div>

        </div>
      </div>
    </div>
  );
}
