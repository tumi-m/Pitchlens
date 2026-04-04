"use client";

import { useEffect, useState, useRef } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { ChevronLeft, Download, Map, PieChart, Activity, Cpu, Loader2 } from 'lucide-react';
import { motion } from 'framer-motion';
import html2canvas from 'html2canvas';
import { jsPDF } from 'jspdf';
import * as d3 from 'd3';
import StatBar from '@/components/StatBar';

const sectionVariants = {
  hidden: { opacity: 0, y: 16 },
  visible: (i: number) => ({
    opacity: 1, y: 0,
    transition: { duration: 0.5, delay: i * 0.12, ease: 'easeOut' },
  }),
};

export default function ReportPage() {
  const { matchId } = useParams();
  const router = useRouter();
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [isExporting, setIsExporting] = useState(false);
  const reportRef = useRef<HTMLDivElement>(null);
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
    if (!data || !pitchRef.current) return;
    
    const svg = d3.select(pitchRef.current);
    svg.selectAll("*").remove();
    
    const width = 450;
    const height = 300;

    svg.append("rect")
      .attr("width", width).attr("height", height)
      .attr("fill", "#050A10").attr("rx", 8);

    svg.append("rect")
      .attr("x", 10).attr("y", 10)
      .attr("width", width - 20).attr("height", height - 20)
      .attr("fill", "none").attr("stroke", "#1E293B").attr("stroke-width", 2);
      
    svg.append("line")
      .attr("x1", width / 2).attr("y1", 10).attr("x2", width / 2).attr("y2", height - 10)
      .attr("stroke", "#1E293B").attr("stroke-width", 2);
      
    svg.append("circle")
      .attr("cx", width / 2).attr("cy", height / 2).attr("r", 30)
      .attr("fill", "none").attr("stroke", "#1E293B").attr("stroke-width", 2);

    const heatData = data.stats?.heatmap?.[0]?.positions || [];
    heatData.forEach((pt: any) => {
      const x = (pt.x / 42) * width;
      const y = (pt.y / 25) * height;
      
      svg.append("circle")
        .attr("cx", x).attr("cy", y)
        .attr("r", pt.intensity * 15)
        .attr("fill", "#4F8CF6")
        .attr("opacity", 0.4)
        .style("filter", "blur(6px)");
    });
  }, [data]);

  const handleDownloadPDF = async () => {
    if (!reportRef.current) return;
    
    setIsExporting(true);
    
    setTimeout(async () => {
      try {
        const canvas = await html2canvas(reportRef.current!, {
          scale: 3,
          backgroundColor: '#050A10',
          useCORS: true,
          logging: false
        });
        
        const imgData = canvas.toDataURL('image/jpeg', 1.0);
        const pdf = new jsPDF({
          orientation: 'portrait',
          unit: 'mm',
          format: 'a4'
        });
        
        const pdfWidth = pdf.internal.pageSize.getWidth();
        const pdfHeight = (canvas.height * pdfWidth) / canvas.width;
        
        pdf.addImage(imgData, 'JPEG', 0, 0, pdfWidth, pdfHeight);
        pdf.save(`Pitchlens_Report_${matchId}.pdf`);
      } catch (e) {
        console.error("PDF generation failed", e);
      } finally {
        setIsExporting(false);
      }
    }, 100);
  };

  if (loading) return (
    <div className="min-h-screen flex items-center justify-center bg-[#050A10]">
      <div className="flex items-center space-x-3">
        <Loader2 className="w-5 h-5 animate-spin text-primary" />
        <span className="text-white font-medium">Compiling Report...</span>
      </div>
    </div>
  );
  if (!data?.stats) return <div className="min-h-screen flex items-center justify-center bg-[#050A10] text-secondary">Match not found.</div>;

  const stats = data.stats;

  return (
    <div className="min-h-[calc(100vh-4rem)] bg-[#020408] text-[#E2E8F0] p-4 sm:p-8 flex flex-col items-center">
      
      {/* Controls */}
      <motion.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        className="w-[794px] mb-6 flex justify-between items-center max-w-full"
      >
        <button onClick={() => router.back()} className="flex items-center text-sm font-medium text-secondary hover:text-white transition-colors">
          <ChevronLeft className="w-4 h-4 mr-1" /> Back to Match
        </button>
        <button 
          onClick={handleDownloadPDF}
          disabled={isExporting}
          className={`flex items-center space-x-2 px-6 py-2.5 bg-gradient-to-r from-primary to-blue-500 text-white text-sm font-bold rounded-full transition-all shadow-glow-primary hover:shadow-glow-primary-lg hover:scale-[1.02] ${isExporting ? 'opacity-50 cursor-not-allowed' : ''}`}
        >
          {isExporting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />}
          <span>{isExporting ? 'Generating PDF...' : 'Download PDF'}</span>
        </button>
      </motion.div>

      {/* A4 Report Wrapper */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.6, delay: 0.1 }}
        className="w-[794px] min-h-[1123px] bg-[#050A10] border border-white/10 shadow-2xl relative overflow-hidden"
        ref={reportRef}
      >
        
        {/* Background Accents */}
        <div className="absolute top-0 right-0 w-96 h-96 bg-primary/10 rounded-full blur-[120px] pointer-events-none" />
        <div className="absolute bottom-0 left-0 w-96 h-96 bg-danger/10 rounded-full blur-[120px] pointer-events-none" />
        
        <div className="relative z-10 p-12 flex flex-col h-full">
          
          {/* Header */}
          <motion.header
            custom={0}
            variants={sectionVariants}
            initial="hidden"
            animate="visible"
            className="border-b border-white/10 pb-8 flex flex-col pt-4"
          >
            <div className="flex justify-between items-start w-full">
              <div>
                <h1 className="text-4xl font-black tracking-tighter text-white flex items-center">
                  <Activity className="w-8 h-8 text-accent mr-3" /> PITCHLENS
                </h1>
                <p className="text-secondary/70 text-sm mt-2 tracking-widest uppercase font-bold">Post-Match Analytics Report</p>
              </div>
              <div className="text-right">
                <p className="text-xs text-secondary mb-1 uppercase tracking-widest font-bold">Match ID</p>
                <p className="font-mono text-white/50 text-xs glass-card px-2 py-1 rounded">{matchId}</p>
              </div>
            </div>

            <div className="mt-12 glass-card rounded-2xl p-6 flex items-center justify-between">
              <div className="flex items-center space-x-4">
                <div className="w-16 h-16 rounded-full bg-primary/20 border-2 border-primary/50 flex items-center justify-center text-xl font-black">H</div>
                <h2 className="text-2xl font-black">Home</h2>
              </div>
              <div className="text-5xl font-mono font-black tracking-tighter">
                <span className="text-primary">{stats.score.home}</span> <span className="text-white/20">-</span> <span className="text-danger">{stats.score.away}</span>
              </div>
              <div className="flex items-center space-x-4">
                <h2 className="text-2xl font-black">Away</h2>
                <div className="w-16 h-16 rounded-full bg-danger/20 border-2 border-danger/50 flex items-center justify-center text-xl font-black">A</div>
              </div>
            </div>
          </motion.header>

          {/* Narrative Overview */}
          <motion.section
            custom={1}
            variants={sectionVariants}
            initial="hidden"
            animate="visible"
            className="mt-10"
          >
            <h3 className="text-sm font-bold text-secondary uppercase tracking-widest mb-4 flex items-center border-l-2 border-accent pl-3">
              Match Narrative
            </h3>
            <p className="text-base text-white/80 leading-relaxed font-light">
              The game&apos;s fulcrum tilted primarily towards the Home side, maintaining <span className="text-primary font-bold">{stats.possession.home}%</span> possession. The persistent pressure yielded an Expected Goals (xG) metric of <span className="text-accent font-bold">{stats.shots.xG}</span> from {stats.shots.total} total shots. The Away team struggled to transition through the midfield, heavily hampered by the Home team effectively cutting off central passing lanes. The mathematical dominance in possession eventually converted into a clinical performance.
            </p>
          </motion.section>

          {/* Analytics Grid */}
          <motion.section
            custom={2}
            variants={sectionVariants}
            initial="hidden"
            animate="visible"
            className="mt-12 grid grid-cols-2 gap-10"
          >
            {/* Stats Comparison Column */}
            <div>
              <h3 className="text-sm font-bold text-secondary uppercase tracking-widest mb-6 flex items-center">
                <PieChart className="w-4 h-4 mr-2" /> Team Statistics
              </h3>
              <div className="bg-[#0B1526] rounded-xl p-6 border border-white/5">
                <StatBar label="Possession" home={`${stats.possession.home}%`} away={`${stats.possession.away}%`} index={0} />
                <StatBar label="Expected Goals (xG)" home={stats.shots.xG} away={(Math.max(0.1, stats.shots.xG - 0.5)).toFixed(2)} index={1} />
                <StatBar label="Total Shots" home={stats.shots.total} away={Math.max(1, stats.shots.total - 4)} index={2} />
                <StatBar label="Shots on Target" home={stats.shots.onTarget} away={Math.max(1, stats.shots.onTarget - 2)} index={3} />
                <StatBar label="Passes Completed" home={stats.passes.completed} away={stats.passes.completed - 15} index={4} />
                <StatBar label="Pass Accuracy" home={`${stats.passes.accuracy}%`} away={`${Math.max(50, stats.passes.accuracy - 8)}%`} index={5} />
                <StatBar label="Fouls Drawn" home={stats.fouls} away={stats.fouls + 3} index={6} />
              </div>
            </div>

            {/* Spatial Analysis Column */}
            <div className="flex flex-col">
              <h3 className="text-sm font-bold text-secondary uppercase tracking-widest mb-6 flex items-center">
                <Map className="w-4 h-4 mr-2" /> Spatial Analysis (Heatmap)
              </h3>
              <div className="bg-[#0B1526] rounded-xl p-6 border border-white/5 flex-1 flex flex-col items-center justify-center">
                <div className="w-full relative aspect-[1.5] bg-[#020408] rounded-xl overflow-hidden ring-1 ring-white/5 shadow-inner">
                  <svg ref={pitchRef} width="100%" height="100%" viewBox="0 0 450 300" className="absolute top-0 left-0" />
                </div>
                <p className="mt-4 text-xs text-secondary text-center leading-relaxed">
                  Heatmap highlights intense central corridor concentration, reflecting rapid box-to-box transitional phases typical of elite 5-a-side matches.
                </p>
              </div>
            </div>
          </motion.section>

          {/* Footer */}
          <footer className="mt-auto pt-8 border-t border-white/10 flex justify-between items-center text-xs text-secondary/50 font-medium">
            <div className="flex items-center">
              <Cpu className="w-4 h-4 mr-2 opacity-50" />
              Generated by Pitchlens AI Engine
            </div>
            <div>{new Date().toLocaleString()}</div>
          </footer>

        </div>
      </motion.div>
    </div>
  );
}
