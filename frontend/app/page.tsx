"use client";

import Link from 'next/link';
import { ArrowRight, Video, BarChart3, FileText, Zap } from 'lucide-react';
import { motion } from 'framer-motion';
import { PitchWatermark, SoccerBall, StadiumLights, PlayerSilhouette } from '@/components/FootballHero';

const features = [
  {
    icon: Video,
    title: 'Upload',
    description: 'Drop your MP4 match recording. YOLOv11m begins tracking every player and ball frame-by-frame.',
    color: 'primary',
    stat: '15fps sampling',
  },
  {
    icon: BarChart3,
    title: 'Analyze',
    description: 'xG model, possession maps, zone pressure, player distance — the full Sofascore-style report.',
    color: 'accent',
    stat: 'Full match coverage',
  },
  {
    icon: FileText,
    title: 'Report',
    description: 'A highlights reel + downloadable PDF with match narrative, heatmaps, and comparative stats.',
    color: 'primary',
    stat: '2–3 min reel',
  },
];

const statPills = [
  { label: 'xG', value: '2.41', color: 'text-primary' },
  { label: 'Possession', value: '62%', color: 'text-accent' },
  { label: 'Shots', value: '14', color: 'text-white' },
  { label: 'Distance', value: '18.4km', color: 'text-purple-400' },
  { label: 'Pass Acc', value: '79%', color: 'text-orange-400' },
  { label: 'Goals', value: '3–1', color: 'text-danger' },
];

const containerVariants = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.12, delayChildren: 0.15 } },
};

const itemVariants = {
  hidden: { opacity: 0, y: 28 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.55, ease: 'easeOut' } },
};

export default function Home() {
  return (
    <div className="relative overflow-hidden">

      {/* ── Background: pitch watermark + stadium lights ── */}
      <div className="absolute inset-0 -z-10 overflow-hidden flex items-center justify-center">
        {/* Stadium lights */}
        <StadiumLights className="absolute inset-0 w-full h-full opacity-60" />

        {/* Pitch lines (centered, faded) */}
        <PitchWatermark className="absolute w-[120%] max-w-[1200px] opacity-40 top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2" />

        {/* Ambient orbs */}
        <div className="absolute top-1/4 left-1/4 w-[600px] h-[600px] bg-primary/10 rounded-full blur-[160px]" />
        <div className="absolute bottom-1/4 right-1/4 w-[400px] h-[400px] bg-accent/10 rounded-full blur-[140px]" />
      </div>

      {/* ── Hero Section ── */}
      <section className="min-h-[calc(100vh-4rem)] flex flex-col items-center justify-center px-6 py-24 text-center relative">

        {/* Player silhouette — left decoration */}
        <PlayerSilhouette className="hidden lg:block absolute left-[8%] top-1/2 -translate-y-1/2 w-28 opacity-60" />
        {/* Mirror on right */}
        <PlayerSilhouette className="hidden lg:block absolute right-[8%] top-1/2 -translate-y-1/2 w-28 opacity-60 scale-x-[-1]" />

        <motion.div
          variants={containerVariants}
          initial="hidden"
          animate="visible"
          className="max-w-4xl mx-auto flex flex-col items-center"
        >
          {/* Badge */}
          <motion.div
            variants={itemVariants}
            className="inline-flex items-center space-x-2 px-4 py-1.5 bg-white/[0.04] border border-white/10 rounded-full mb-8"
          >
            <div className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
            <span className="text-xs font-bold text-secondary tracking-widest uppercase">Five-a-Side Analytics Engine</span>
          </motion.div>

          {/* Soccer ball hero icon */}
          <motion.div variants={itemVariants} className="mb-6 relative">
            <div className="absolute inset-0 blur-2xl bg-primary/20 rounded-full scale-150" />
            <SoccerBall size={72} className="relative" />
          </motion.div>

          {/* Heading */}
          <motion.h1
            variants={itemVariants}
            className="text-6xl sm:text-7xl lg:text-8xl font-black tracking-tighter leading-[0.9]"
          >
            Pitch<span className="text-gradient-primary">lens</span>
          </motion.h1>

          {/* Subheading */}
          <motion.p
            variants={itemVariants}
            className="mt-6 text-lg sm:text-xl text-secondary max-w-xl font-light leading-relaxed"
          >
            Upload your match footage. Get Sofascore-grade analytics — xG, heatmaps, possession, highlights — powered by YOLOv11m.
          </motion.p>

          {/* Scrolling stats ticker */}
          <motion.div variants={itemVariants} className="mt-8 w-full max-w-lg overflow-hidden">
            <div className="flex gap-3 justify-center flex-wrap">
              {statPills.map(({ label, value, color }, i) => (
                <motion.div
                  key={label}
                  initial={{ opacity: 0, scale: 0.8 }}
                  animate={{ opacity: 1, scale: 1 }}
                  transition={{ delay: 0.4 + i * 0.08 }}
                  className="glass-card rounded-full px-4 py-1.5 flex items-center gap-2"
                >
                  <span className="text-xs text-secondary font-medium">{label}</span>
                  <span className={`text-sm font-black ${color}`}>{value}</span>
                </motion.div>
              ))}
            </div>
          </motion.div>

          {/* CTA */}
          <motion.div variants={itemVariants} className="flex flex-col sm:flex-row gap-4 mt-10">
            <Link
              href="/upload"
              className="group relative px-8 py-4 bg-gradient-to-r from-primary to-blue-500 text-white rounded-full font-bold transition-all duration-300 shadow-glow-primary hover:shadow-glow-primary-lg hover:scale-[1.03] flex items-center justify-center space-x-2"
            >
              <span>Analyze a Match</span>
              <ArrowRight className="w-4 h-4 transition-transform group-hover:translate-x-1" />
            </Link>
            <Link
              href="/dashboard"
              className="px-8 py-4 glass-card rounded-full font-medium transition-all duration-300 hover:bg-white/[0.08] hover:border-white/20 text-secondary hover:text-white"
            >
              View Demo
            </Link>
          </motion.div>
        </motion.div>
      </section>

      {/* ── Pitch diagram section ── */}
      <section className="pb-16 px-6">
        <motion.div
          initial={{ opacity: 0, y: 40 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: '-80px' }}
          transition={{ duration: 0.7 }}
          className="max-w-5xl mx-auto glass-card rounded-3xl p-8 relative overflow-hidden"
        >
          <div className="absolute inset-0 bg-gradient-to-br from-primary/5 via-transparent to-accent/5" />
          <div className="relative">
            <div className="flex items-center justify-between mb-4">
              <div>
                <p className="text-xs text-secondary uppercase tracking-widest font-bold mb-1">Live Pitch View</p>
                <h2 className="text-xl font-black text-white">Real-world coordinate tracking</h2>
              </div>
              <div className="flex items-center gap-2">
                <motion.div className="w-2 h-2 rounded-full bg-accent"
                  animate={{ opacity: [1, 0.3, 1] }} transition={{ duration: 1.2, repeat: Infinity }} />
                <span className="text-accent text-xs font-bold">LIVE</span>
              </div>
            </div>

            {/* Pitch with animated dots */}
            <div className="relative">
              <PitchWatermark className="w-full opacity-100" />
              {/* Overlay player dots */}
              {[
                { x: '15%', y: '50%', team: 'home' }, { x: '22%', y: '32%', team: 'home' },
                { x: '24%', y: '68%', team: 'home' }, { x: '35%', y: '42%', team: 'home' },
                { x: '36%', y: '60%', team: 'home' },
                { x: '65%', y: '50%', team: 'away' }, { x: '72%', y: '30%', team: 'away' },
                { x: '74%', y: '70%', team: 'away' }, { x: '80%', y: '40%', team: 'away' },
                { x: '82%', y: '58%', team: 'away' },
              ].map((dot, i) => (
                <motion.div
                  key={i}
                  className="absolute -translate-x-1/2 -translate-y-1/2"
                  style={{ left: dot.x, top: dot.y }}
                  animate={{ scale: [1, 1.15, 1] }}
                  transition={{ duration: 2, repeat: Infinity, delay: i * 0.2 }}
                >
                  <div className={`w-3 h-3 rounded-full ${dot.team === 'home' ? 'bg-primary' : 'bg-danger'} shadow-lg`} />
                  <div className={`absolute inset-0 rounded-full ${dot.team === 'home' ? 'bg-primary/40' : 'bg-danger/40'} blur-sm scale-150`} />
                </motion.div>
              ))}
              {/* Ball */}
              <motion.div
                className="absolute -translate-x-1/2 -translate-y-1/2"
                animate={{ left: ['50%', '55%', '48%', '52%', '50%'], top: ['50%', '44%', '52%', '46%', '50%'] }}
                transition={{ duration: 4, repeat: Infinity, ease: 'easeInOut' }}
              >
                <div className="w-3 h-3 rounded-full bg-white shadow-lg shadow-white/30" />
              </motion.div>
            </div>

            <div className="flex items-center gap-6 mt-4">
              <div className="flex items-center gap-2">
                <div className="w-3 h-3 rounded-full bg-primary" />
                <span className="text-xs text-secondary">Home</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="w-3 h-3 rounded-full bg-danger" />
                <span className="text-xs text-secondary">Away</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="w-3 h-3 rounded-full bg-white" />
                <span className="text-xs text-secondary">Ball</span>
              </div>
              <div className="ml-auto flex items-center gap-2">
                <Zap className="w-4 h-4 text-accent" />
                <span className="text-xs text-secondary">42 × 25m real-world coordinates</span>
              </div>
            </div>
          </div>
        </motion.div>
      </section>

      {/* ── Features Section ── */}
      <section className="pb-32 px-6">
        <motion.div
          initial={{ opacity: 0 }}
          whileInView={{ opacity: 1 }}
          viewport={{ once: true, margin: '-100px' }}
          transition={{ duration: 0.6 }}
          className="max-w-5xl mx-auto"
        >
          <div className="text-center mb-12">
            <p className="text-xs text-secondary uppercase tracking-widest font-bold mb-3">How it works</p>
            <h2 className="text-3xl font-black text-white">Three steps. Match intelligence.</h2>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            {features.map((feature, i) => (
              <motion.div
                key={feature.title}
                initial={{ opacity: 0, y: 30 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true }}
                transition={{ duration: 0.5, delay: i * 0.12 }}
                className="group relative glass-card rounded-2xl p-8 glow-hover overflow-hidden"
              >
                <div className={`absolute top-0 left-0 right-0 h-[2px] bg-gradient-to-r ${
                  feature.color === 'accent' ? 'from-accent/60 to-accent/0' : 'from-primary/60 to-primary/0'
                }`} />

                <span className="text-xs font-black text-white/10 absolute top-6 right-6">0{i + 1}</span>

                <div className={`w-12 h-12 rounded-xl ${
                  feature.color === 'accent' ? 'bg-accent/10' : 'bg-primary/10'
                } flex items-center justify-center mb-6 transition-transform group-hover:scale-110`}>
                  <feature.icon className={`w-6 h-6 ${
                    feature.color === 'accent' ? 'text-accent' : 'text-primary'
                  }`} />
                </div>

                <h3 className="text-lg font-bold text-white mb-2">{feature.title}</h3>
                <p className="text-sm text-secondary leading-relaxed mb-4">{feature.description}</p>

                {/* Stat badge */}
                <div className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold border ${
                  feature.color === 'accent'
                    ? 'border-accent/20 bg-accent/5 text-accent'
                    : 'border-primary/20 bg-primary/5 text-primary'
                }`}>
                  <Zap className="w-3 h-3" />
                  {feature.stat}
                </div>
              </motion.div>
            ))}
          </div>
        </motion.div>
      </section>
    </div>
  );
}
