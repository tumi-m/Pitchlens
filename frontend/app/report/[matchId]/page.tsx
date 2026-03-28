'use client';
import { useRef, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { motion } from 'framer-motion';
import { Download, Share2, Loader2, ChevronLeft, Check } from 'lucide-react';
import { Navbar } from '@/components/ui/Navbar';
import { useMatch } from '@/lib/hooks/useMatch';
import { ShotsBars, PossessionDonut, MomentumLine, PassAccuracyBars } from '@/components/charts/StatsCharts';
import { formatTimestamp } from '@/lib/utils/analytics';
import { format } from 'date-fns';
import toast from 'react-hot-toast';

export default function ReportPage() {
  const { matchId } = useParams<{ matchId: string }>();
  const { match, loading } = useMatch(matchId);
  const reportRef = useRef<HTMLDivElement>(null);
  const [exporting, setExporting] = useState(false);
  const [copied, setCopied] = useState(false);

  if (loading) return (
    <>
      <Navbar />
      <main className="min-h-screen pt-20 flex items-center justify-center">
        <Loader2 className="animate-spin text-pitch-indigo-glow" size={40} />
      </main>
    </>
  );

  if (!match || match.status !== 'completed') return (
    <>
      <Navbar />
      <main className="min-h-screen pt-20 flex flex-col items-center justify-center gap-4">
        <p className="text-pitch-white">Report unavailable — match must be completed first.</p>
        <Link href={`/dashboard/${matchId}`} className="pitch-button-secondary">Back to Dashboard</Link>
      </main>
    </>
  );

  const stats = match.stats!;

  const handleExport = async () => {
    setExporting(true);
    try {
      const [{ default: jsPDF }, { default: html2canvas }] = await Promise.all([
        import('jspdf'),
        import('html2canvas'),
      ]);

      const el = reportRef.current!;
      const canvas = await html2canvas(el, {
        backgroundColor: '#0D0D1A',
        scale: 2,
        useCORS: true,
        logging: false,
      });

      const pdf = new jsPDF({ orientation: 'portrait', unit: 'mm', format: 'a4' });
      const imgData = canvas.toDataURL('image/jpeg', 0.92);
      const pdfWidth = pdf.internal.pageSize.getWidth();
      const pdfHeight = (canvas.height * pdfWidth) / canvas.width;

      // Add pages if content overflows
      const pageHeight = pdf.internal.pageSize.getHeight();
      let yPos = 0;
      while (yPos < pdfHeight) {
        if (yPos > 0) pdf.addPage();
        pdf.addImage(imgData, 'JPEG', 0, -yPos, pdfWidth, pdfHeight);
        yPos += pageHeight;
      }

      pdf.save(`pitchlens-${match.title.replace(/\s+/g, '_')}.pdf`);
      toast.success('Report exported!');
    } catch (err) {
      console.error(err);
      toast.error('Export failed — please try again');
    } finally {
      setExporting(false);
    }
  };

  const handleShare = async () => {
    const url = window.location.href;
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      toast.success('Link copied!');
      setTimeout(() => setCopied(false), 2500);
    } catch {
      toast.error('Failed to copy link');
    }
  };

  let matchDate = '—';
  try {
    if (match.createdAt?.toDate) matchDate = format(match.createdAt.toDate(), 'EEEE, d MMMM yyyy');
    else if (match.createdAt?.seconds) matchDate = format(new Date(match.createdAt.seconds * 1000), 'EEEE, d MMMM yyyy');
    else matchDate = format(new Date(), 'EEEE, d MMMM yyyy');
  } catch { matchDate = format(new Date(), 'EEEE, d MMMM yyyy'); }
  const keyEvents = stats.events
    .filter((e: any) => ['goal', 'shot_on_target', 'corner', 'foul'].includes(e.type))
    .sort((a: any, b: any) => a.timestamp - b.timestamp)
    .slice(0, 18);

  return (
    <>
      <Navbar />
      <main className="min-h-screen pt-20 pb-16 px-4">
        <div className="max-w-4xl mx-auto space-y-6">
          {/* Actions */}
          <div className="flex items-center justify-between">
            <Link href={`/dashboard/${matchId}`} className="flex items-center gap-1 text-pitch-muted hover:text-pitch-white text-sm transition-colors">
              <ChevronLeft size={16} /> Dashboard
            </Link>
            <div className="flex gap-3">
              <button onClick={handleShare} className="pitch-button-secondary gap-2 text-sm">
                {copied ? <Check size={16} className="text-pitch-green" /> : <Share2 size={16} />}
                {copied ? 'Copied!' : 'Share'}
              </button>
              <button onClick={handleExport} disabled={exporting} className="pitch-button-primary gap-2 text-sm">
                {exporting ? <Loader2 size={16} className="animate-spin" /> : <Download size={16} />}
                Export PDF
              </button>
            </div>
          </div>

          {/* Report document */}
          <div ref={reportRef} className="bg-[#0D0D1A]">
            <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} className="space-y-0">

              {/* ── Header Banner ── */}
              <div className="bg-gradient-to-r from-[#1a1a3e] via-[#0f0f2e] to-[#1a1a3e] px-8 py-6 border-b border-white/10">
                <div className="flex items-center justify-between mb-6">
                  <div className="flex items-center gap-2">
                    <span className="text-2xl">⚽</span>
                    <div>
                      <p className="text-white font-black text-lg tracking-tight">PITCHLENS</p>
                      <p className="text-white/40 text-xs uppercase tracking-widest">Match Analysis Report</p>
                    </div>
                  </div>
                  <div className="text-right">
                    <p className="text-white/60 text-xs uppercase tracking-widest">{matchDate}</p>
                    <p className="text-white/40 text-xs mt-0.5">Powered by Roboflow YOLOv8</p>
                  </div>
                </div>

                {/* Score block */}
                <div className="flex items-center justify-center gap-8">
                  <div className="text-center flex-1">
                    <div
                      className="w-14 h-14 rounded-full mx-auto mb-3 border-2 border-white/20 flex items-center justify-center"
                      style={{ backgroundColor: match.homeTeamColor || '#ef4444' }}
                    >
                      <span className="text-white font-black text-lg">{(match.homeTeamName || 'H')[0]}</span>
                    </div>
                    <p className="text-white font-bold text-base">{match.homeTeamName}</p>
                    <p className="text-white/40 text-xs">Home</p>
                  </div>

                  <div className="text-center px-6">
                    <div className="text-7xl font-black text-white leading-none tracking-tighter">
                      {stats.score.home}
                      <span className="text-white/30 mx-2">–</span>
                      {stats.score.away}
                    </div>
                    <p className="text-white/50 text-xs mt-2 uppercase tracking-widest">Full Time</p>
                    {stats.shots.home.xG > 0 || stats.shots.away.xG > 0 ? (
                      <p className="text-white/30 text-xs mt-1">
                        xG: {stats.shots.home.xG.toFixed(2)} – {stats.shots.away.xG.toFixed(2)}
                      </p>
                    ) : null}
                  </div>

                  <div className="text-center flex-1">
                    <div
                      className="w-14 h-14 rounded-full mx-auto mb-3 border-2 border-white/20 flex items-center justify-center"
                      style={{ backgroundColor: match.awayTeamColor || '#3b82f6' }}
                    >
                      <span className="text-white font-black text-lg">{(match.awayTeamName || 'A')[0]}</span>
                    </div>
                    <p className="text-white font-bold text-base">{match.awayTeamName}</p>
                    <p className="text-white/40 text-xs">Away</p>
                  </div>
                </div>
              </div>

              {/* ── Narrative ── */}
              <div className="px-8 py-5 border-b border-white/10">
                <p className="text-white/40 text-xs uppercase tracking-widest mb-2">Match Summary</p>
                <p className="text-white/80 text-sm leading-relaxed italic">"{stats.narrative}"</p>
              </div>

              {/* ── Full Stats Table ── */}
              <div className="px-8 py-5 border-b border-white/10">
                <p className="text-white/40 text-xs uppercase tracking-widest mb-4">Statistics</p>
                <div className="space-y-1">
                  {/* Header */}
                  <div className="grid grid-cols-[1fr,180px,1fr] text-center text-xs font-bold mb-3">
                    <span className="text-left" style={{ color: match.homeTeamColor || '#ef4444' }}>{match.homeTeamName}</span>
                    <span className="text-white/30">Stat</span>
                    <span className="text-right" style={{ color: match.awayTeamColor || '#3b82f6' }}>{match.awayTeamName}</span>
                  </div>

                  {[
                    { label: 'Possession', home: `${stats.possession.home}%`, away: `${stats.possession.away}%`, homeV: stats.possession.home, awayV: stats.possession.away },
                    { label: 'Shots', home: stats.shots.home.total, away: stats.shots.away.total, homeV: stats.shots.home.total, awayV: stats.shots.away.total },
                    { label: 'Shots on Target', home: stats.shots.home.onTarget, away: stats.shots.away.onTarget, homeV: stats.shots.home.onTarget, awayV: stats.shots.away.onTarget },
                    { label: 'Expected Goals (xG)', home: stats.shots.home.xG.toFixed(2), away: stats.shots.away.xG.toFixed(2), homeV: stats.shots.home.xG, awayV: stats.shots.away.xG },
                    { label: 'Passes Completed', home: stats.passes.home.completed, away: stats.passes.away.completed, homeV: stats.passes.home.completed, awayV: stats.passes.away.completed },
                    { label: 'Pass Accuracy', home: `${stats.passes.home.accuracy}%`, away: `${stats.passes.away.accuracy}%`, homeV: stats.passes.home.accuracy, awayV: stats.passes.away.accuracy },
                    { label: 'Corners', home: stats.corners.home, away: stats.corners.away, homeV: stats.corners.home, awayV: stats.corners.away },
                    { label: 'Fouls', home: stats.fouls.home, away: stats.fouls.away, homeV: stats.fouls.away, awayV: stats.fouls.home }, // inverted for bar (lower=better)
                    { label: 'Pressure Index', home: stats.pressureIndex?.home?.toFixed(1) ?? '—', away: stats.pressureIndex?.away?.toFixed(1) ?? '—', homeV: stats.pressureIndex?.home ?? 0, awayV: stats.pressureIndex?.away ?? 0 },
                  ].map(({ label, home, away, homeV, awayV }) => {
                    const total = (homeV as number) + (awayV as number) || 1;
                    const homeW = Math.round(((homeV as number) / total) * 100);
                    const homeWins = (homeV as number) >= (awayV as number);
                    return (
                      <div key={label} className="space-y-0.5 py-1">
                        <div className="grid grid-cols-[1fr,180px,1fr] text-sm items-center">
                          <span className={homeWins ? 'text-white font-bold' : 'text-white/50'}>{home}</span>
                          <span className="text-center text-white/30 text-xs">{label}</span>
                          <span className={`text-right ${!homeWins ? 'text-white font-bold' : 'text-white/50'}`}>{away}</span>
                        </div>
                        <div className="flex h-1 rounded-full overflow-hidden bg-white/5">
                          <div className="rounded-full" style={{ width: `${homeW}%`, backgroundColor: match.homeTeamColor || '#ef4444', opacity: 0.8 }} />
                          <div className="rounded-full flex-1" style={{ backgroundColor: match.awayTeamColor || '#3b82f6', opacity: 0.8 }} />
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* ── Charts Row ── */}
              <div className="grid sm:grid-cols-2 divide-x divide-white/10 border-b border-white/10">
                <div className="px-8 py-5">
                  <p className="text-white/40 text-xs uppercase tracking-widest mb-3">Possession Distribution</p>
                  <PossessionDonut stats={stats} homeTeamName={match.homeTeamName} awayTeamName={match.awayTeamName} />
                </div>
                <div className="px-8 py-5">
                  <p className="text-white/40 text-xs uppercase tracking-widest mb-3">Attacking Output</p>
                  <ShotsBars stats={stats} homeTeamName={match.homeTeamName} awayTeamName={match.awayTeamName} />
                </div>
              </div>

              {/* ── Momentum ── */}
              <div className="px-8 py-5 border-b border-white/10">
                <p className="text-white/40 text-xs uppercase tracking-widest mb-3">Momentum Timeline</p>
                <MomentumLine stats={stats} homeTeamName={match.homeTeamName} awayTeamName={match.awayTeamName} />
              </div>

              {/* ── Pass Accuracy ── */}
              <div className="px-8 py-5 border-b border-white/10">
                <p className="text-white/40 text-xs uppercase tracking-widest mb-3">Pass Accuracy</p>
                <PassAccuracyBars stats={stats} homeTeamName={match.homeTeamName} awayTeamName={match.awayTeamName} />
              </div>

              {/* ── Key Events ── */}
              {keyEvents.length > 0 && (
                <div className="px-8 py-5 border-b border-white/10">
                  <p className="text-white/40 text-xs uppercase tracking-widest mb-3">Key Events</p>
                  <div className="grid grid-cols-2 gap-x-8 gap-y-1">
                    {keyEvents.map((e: any, i: number) => (
                      <div key={i} className="flex items-center gap-2 py-1 border-b border-white/5">
                        <span className="text-white/40 text-xs font-mono w-8 shrink-0 text-right">
                          {formatTimestamp(e.timestamp)}
                        </span>
                        <span className="text-white/70 text-xs">
                          <span className="font-medium text-white/90">{e.type === 'goal' ? '⚽ Goal' : e.type === 'shot_on_target' ? '🎯 Shot on Target' : e.type === 'corner' ? '🚩 Corner' : '🟨 Foul'}</span>
                          {' · '}
                          <span>{e.teamSide === 'home' ? match.homeTeamName : match.awayTeamName}</span>
                          {e.xG ? <span className="ml-1 opacity-60">xG {e.xG.toFixed(2)}</span> : null}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* ── Footer ── */}
              <div className="px-8 py-4 flex items-center justify-between">
                <span className="text-white/20 text-xs">Generated by Pitchlens · pitchlens.app</span>
                <span className="text-white/20 text-xs italic">"Unveil the Geometry of Your Game"</span>
              </div>

            </motion.div>
          </div>
        </div>
      </main>
    </>
  );
}
