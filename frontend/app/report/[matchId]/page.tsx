'use client';
import { useRef, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { motion } from 'framer-motion';
import { Download, Share2, Loader2, ChevronLeft, Check } from 'lucide-react';
import { Navbar } from '@/components/ui/Navbar';
import { useMatch } from '@/lib/hooks/useMatch';
import { ShotsBars, PossessionDonut, MomentumLine } from '@/components/charts/StatsCharts';
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
      // Dynamically import heavy PDF libs
      const [{ default: jsPDF }, { default: html2canvas }] = await Promise.all([
        import('jspdf'),
        import('html2canvas'),
      ]);

      const el = reportRef.current!;
      const canvas = await html2canvas(el, {
        backgroundColor: '#0A0A0F',
        scale: 2,
        useCORS: true,
      });

      const pdf = new jsPDF({ orientation: 'portrait', unit: 'mm', format: 'a4' });
      const imgData = canvas.toDataURL('image/png');
      const pdfWidth = pdf.internal.pageSize.getWidth();
      const pdfHeight = (canvas.height * pdfWidth) / canvas.width;
      pdf.addImage(imgData, 'PNG', 0, 0, pdfWidth, pdfHeight);
      pdf.save(`pitchlens-${match.title.replace(/\s+/g, '_')}.pdf`);
      toast.success('Report exported!');
    } catch (err) {
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
      toast.success('Link copied to clipboard');
      setTimeout(() => setCopied(false), 2500);
    } catch {
      toast.error('Failed to copy link');
    }
  };

  const matchDate = match.createdAt?.toDate ? format(match.createdAt.toDate(), 'EEEE, d MMMM yyyy') : '—';

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
          <div ref={reportRef}>
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              className="glass-card p-8 sm:p-12 space-y-10"
            >
              {/* Header */}
              <div className="border-b border-pitch-indigo-soft/30 pb-8">
                <div className="flex items-start justify-between gap-4 flex-wrap">
                  <div>
                    <div className="flex items-center gap-2 mb-3">
                      <span className="text-xl">⚽</span>
                      <span className="text-pitch-muted text-sm font-medium uppercase tracking-widest">Pitchlens Match Report</span>
                    </div>
                    <h1 className="text-3xl font-black text-pitch-white">{match.title}</h1>
                    <p className="text-pitch-muted mt-1">{matchDate}</p>
                  </div>
                  <div className="text-right">
                    <div className="text-5xl font-black text-pitch-white">
                      {stats.score.home} <span className="text-pitch-indigo-glow">—</span> {stats.score.away}
                    </div>
                    <p className="text-pitch-muted text-sm mt-1">
                      {match.homeTeamName} vs {match.awayTeamName}
                    </p>
                  </div>
                </div>
              </div>

              {/* Narrative */}
              <section>
                <h2 className="text-xs font-semibold text-pitch-muted uppercase tracking-widest mb-4">Match Narrative</h2>
                <blockquote className="border-l-2 border-pitch-green/50 pl-5 text-pitch-white/80 italic text-base leading-relaxed">
                  "{stats.narrative}"
                </blockquote>
              </section>

              {/* Key Stats Grid */}
              <section>
                <h2 className="text-xs font-semibold text-pitch-muted uppercase tracking-widest mb-4">Key Statistics</h2>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                  {[
                    { label: 'Possession', home: `${stats.possession.home}%`, away: `${stats.possession.away}%` },
                    { label: 'Shots', home: stats.shots.home.total, away: stats.shots.away.total },
                    { label: 'xG', home: stats.shots.home.xG.toFixed(2), away: stats.shots.away.xG.toFixed(2) },
                    { label: 'Pass Acc.', home: `${stats.passes.home.accuracy}%`, away: `${stats.passes.away.accuracy}%` },
                    { label: 'On Target', home: stats.shots.home.onTarget, away: stats.shots.away.onTarget },
                    { label: 'Fouls', home: stats.fouls.home, away: stats.fouls.away },
                    { label: 'Corners', home: stats.corners.home, away: stats.corners.away },
                    { label: 'Pressure Idx', home: stats.pressureIndex?.home?.toFixed(1) ?? '—', away: stats.pressureIndex?.away?.toFixed(1) ?? '—' },
                  ].map(({ label, home, away }) => (
                    <div key={label} className="bg-pitch-indigo-deep/40 rounded-xl p-4 text-center">
                      <p className="text-pitch-muted text-xs mb-2 uppercase tracking-wider">{label}</p>
                      <div className="flex justify-center items-center gap-2">
                        <span className="text-red-400 font-bold">{home}</span>
                        <span className="text-pitch-muted text-xs">–</span>
                        <span className="text-blue-400 font-bold">{away}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </section>

              {/* Charts */}
              <section className="grid sm:grid-cols-2 gap-8">
                <div>
                  <h2 className="text-xs font-semibold text-pitch-muted uppercase tracking-widest mb-4">Possession Distribution</h2>
                  <PossessionDonut stats={stats} homeTeamName={match.homeTeamName} awayTeamName={match.awayTeamName} />
                </div>
                <div>
                  <h2 className="text-xs font-semibold text-pitch-muted uppercase tracking-widest mb-4">Attacking Output</h2>
                  <ShotsBars stats={stats} homeTeamName={match.homeTeamName} awayTeamName={match.awayTeamName} />
                </div>
              </section>

              <section>
                <h2 className="text-xs font-semibold text-pitch-muted uppercase tracking-widest mb-4">Momentum Timeline</h2>
                <MomentumLine stats={stats} homeTeamName={match.homeTeamName} awayTeamName={match.awayTeamName} />
              </section>

              {/* Key Events */}
              <section>
                <h2 className="text-xs font-semibold text-pitch-muted uppercase tracking-widest mb-4">Key Events</h2>
                <div className="space-y-2">
                  {stats.events
                    .filter((e) => ['goal', 'shot_on_target', 'corner', 'foul'].includes(e.type))
                    .sort((a, b) => a.timestamp - b.timestamp)
                    .slice(0, 15)
                    .map((e, i) => (
                      <div key={i} className="flex items-center gap-3 py-2 border-b border-pitch-indigo-soft/10">
                        <span className="text-pitch-muted text-xs font-mono w-10 text-right shrink-0">
                          {formatTimestamp(e.timestamp)}
                        </span>
                        <span className="text-pitch-white text-sm flex-1">
                          <span className="font-medium capitalize">{e.type.replace('_', ' ')}</span>
                          {' · '}
                          <span className="text-pitch-muted">{e.teamSide === 'home' ? match.homeTeamName : match.awayTeamName}</span>
                          {e.xG ? <span className="ml-2 text-pitch-indigo-glow text-xs">xG {e.xG.toFixed(2)}</span> : null}
                        </span>
                      </div>
                    ))}
                </div>
              </section>

              {/* Footer */}
              <div className="border-t border-pitch-indigo-soft/20 pt-6 flex items-center justify-between text-pitch-muted text-xs">
                <span>Generated by Pitchlens · pitchlens.app</span>
                <span className="italic">"Unveil the Geometry of Your Game"</span>
              </div>
            </motion.div>
          </div>
        </div>
      </main>
    </>
  );
}
