"use client";

import { useEffect, useState, useRef } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { AreaChart, Area, LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, ReferenceLine } from 'recharts';
import { ChevronLeft, Download, Activity, Users, Map } from 'lucide-react';
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

    svg.append("rect")
      .attr("width", width)
      .attr("height", height)
      .attr("fill", "#091428")
      .attr("rx", 8);

    svg.append("rect")
      .attr("x", 10).attr("y", 10)
      .attr("width", width - 20).attr("height", height - 20)
      .attr("fill", "none").attr("stroke", "#1E293B").attr("stroke-width", 2);
      
    svg.append("line")
      .attr("x1", width / 2).attr("y1", 10).attr("x2", width / 2).attr("y2", height - 10)
      .attr("stroke", "#1E293B").attr("stroke-width", 2);
      
    svg.append("circle")
      .attr("cx", width / 2).attr("cy", height / 2).attr("r", 40)
      .attr("fill", "none").attr("stroke", "#1E293B").attr("stroke-width", 2);

    const heatData = data.stats?.heatmap?.[0]?.positions || [];
    heatData.forEach((pt: any) => {
      const x = (pt.x / 42) * width;
      const y = (pt.y / 25) * height;
      
      svg.append("circle")
        .attr("cx", x).attr("cy", y)
        .attr("r", pt.intensity * 20)
        .attr("fill", "#4F8CF6")
        .attr("opacity", 0.4)
        .style("filter", "blur(8px)");
    });
  }, [data, activeTab]);

  if (loading) return <SkeletonDashboard />;
  if (!data?.stats) return <div className="min-h-screen flex items-center justify-center bg-background text-secondary">Match not found or not processed.</div>;

  const stats = data.stats;
  
  const duration = 40;
  const momentumData = Array.from({length: duration}, (_, i) => ({
    minute: i + 1,
    value: Math.sin(i / 4) * 60 + (Math.random() * 40 - 20)
  }));
  
  let xGHome = 0;
  let xGAway = 0;
  const xGFlowData = Array.from({length: duration}, (_, i) => {
    if (Math.random() > 0.8) xGHome += Math.random() * 0.4;
    if (Math.random() > 0.8) xGAway += Math.random() * 0.4;
    return {
      minute: i + 1,
      Home: parseFloat(xGHome.toFixed(2)),
      Away: parseFloat(xGAway.toFixed(2))
    };
  });

  const tabs = ['Overview', 'Pitch', 'Lineups', 'Video Analytics'];

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
                <button 
                  onClick={() => router.push(`/report/${matchId}`)}
                  className="mt-8 flex items-center space-x-2 px-6 py-2.5 glass-card hover:bg-white/10 text-white text-sm font-bold rounded-full transition-all glow-hover"
                >
                  <Download className="w-4 h-4" />
                  <span>Download PDF Report</span>
                </button>
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
                activeTab === tab 
                  ? 'text-white' 
                  : 'text-secondary hover:text-white/80'
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
                      <p className="text-sm text-secondary">Pressure relative to possession and pitch zones.</p>
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
                          formatter={(value: number) => [Math.abs(value).toFixed(1), value > 0 ? "Home Pressure" : "Away Pressure"]}
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
                  <h3 className="text-xl font-bold text-white mb-8 border-b border-white/5 pb-4">Team Statistics</h3>
                  
                  <StatBar label="Possession" home={`${stats.possession.home}%`} away={`${stats.possession.away}%`} index={0} />
                  <StatBar label="Expected Goals (xG)" home={stats.shots.xG} away={(Math.max(0.1, stats.shots.xG - 0.5)).toFixed(2)} index={1} />
                  <StatBar label="Total Shots" home={stats.shots.total} away={Math.max(1, stats.shots.total - 4)} index={2} />
                  <StatBar label="Shots on Target" home={stats.shots.onTarget} away={Math.max(1, stats.shots.onTarget - 2)} index={3} />
                  <StatBar label="Passes Completed" home={stats.passes.completed} away={stats.passes.completed - 15} index={4} />
                  <StatBar label="Pass Accuracy" home={`${stats.passes.accuracy}%`} away={`${Math.max(50, stats.passes.accuracy - 8)}%`} index={5} />
                  <StatBar label="Fouls" home={stats.fouls} away={stats.fouls + 3} index={6} />
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
                      <p className="text-sm text-secondary">Cumulative probability of scoring over time.</p>
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
                  <div className="flex p-1 bg-surfaceHover rounded-lg">
                    <span className="px-4 py-1.5 bg-[#0B1526] text-white text-xs font-bold rounded-md shadow-sm">Heatmap</span>
                    <span className="px-4 py-1.5 text-secondary text-xs font-bold rounded-md hover:text-white cursor-pointer transition-colors">Voronoi</span>
                  </div>
                </div>
                
                <div className="relative w-full max-w-[600px] aspect-[1.5] bg-[#091428] rounded-xl overflow-hidden shadow-[inset_0_4px_24px_rgba(0,0,0,0.5)] border border-white/5 ring-1 ring-white/5">
                  <svg ref={pitchRef} width="100%" height="100%" viewBox="0 0 600 400" className="absolute top-0 left-0" />
                </div>
                
                <p className="mt-8 text-sm text-secondary/80 text-center max-w-lg leading-relaxed glass-card py-4 px-6 rounded-xl">
                  Visualising high-density areas of ball traffic. Strong central pivot control demonstrated by the Home Team.
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

          </div>

          {/* Right Column / Sidebars */}
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
                   <span className="text-white font-medium">{new Date().toLocaleDateString(undefined, { month: 'long', day: 'numeric', year: 'numeric'})}</span>
                 </div>
                 <div className="flex justify-between items-center text-sm border-b border-white/5 pb-2">
                   <span className="text-secondary">Format</span>
                   <span className="text-white font-medium">5-a-side</span>
                 </div>
                 <div className="flex justify-between items-center text-sm pb-1">
                   <span className="text-secondary">Duration</span>
                   <span className="text-white font-medium">40 Mins</span>
                 </div>
               </div>
             </motion.div>

             {/* Top Player Ratings */}
             <motion.div
               custom={1}
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
