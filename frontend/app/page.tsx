'use client';
import { useState, useRef } from 'react';
import Link from 'next/link';
import { motion, useScroll, useTransform } from 'framer-motion';
import { ArrowRight, Activity, Zap, BarChart3, Shield, ChevronDown } from 'lucide-react';
import { Navbar } from '@/components/ui/Navbar';
import { AuthModal } from '@/components/auth/AuthModal';
import { useAuthContext } from '@/components/auth/AuthProvider';

const FEATURES = [
  {
    icon: Zap,
    title: 'YOLOv8 Detection',
    description: 'Sub-8ms per frame detection of players, ball, and goals with 94%+ accuracy on HD video.',
  },
  {
    icon: Activity,
    title: 'Expected Goals (xG)',
    description: 'Logistic regression models calibrated for five-a-side geometry compute shot quality in real time.',
  },
  {
    icon: BarChart3,
    title: 'Voronoi Space Control',
    description: 'Temporal tessellations reveal dominance maps—who owned the pitch, metre by metre.',
  },
  {
    icon: Shield,
    title: 'Possession Chains',
    description: 'ByteTrack-powered trajectory analysis surfaces pass networks and pressure indices.',
  },
];

const STATS = [
  { value: '< 60s', label: 'Processing Time' },
  { value: '0.78', label: 'xG Correlation' },
  { value: '98%', label: 'Tracking Accuracy' },
  { value: '42×25m', label: 'Pitch Model' },
];

export default function HomePage() {
  const { user } = useAuthContext();
  const [authOpen, setAuthOpen] = useState(false);
  const heroRef = useRef<HTMLDivElement>(null);
  const { scrollYProgress } = useScroll({ target: heroRef, offset: ['start start', 'end start'] });
  const y = useTransform(scrollYProgress, [0, 1], ['0%', '30%']);
  const opacity = useTransform(scrollYProgress, [0, 0.8], [1, 0]);

  return (
    <>
      <Navbar />
      <main className="min-h-screen">
        {/* Hero */}
        <section ref={heroRef} className="relative min-h-screen flex items-center justify-center overflow-hidden">
          {/* Parallax pitch lines */}
          <motion.div style={{ y, opacity }} className="absolute inset-0 pointer-events-none">
            <svg
              className="absolute inset-0 w-full h-full opacity-5"
              viewBox="0 0 1200 800"
              preserveAspectRatio="xMidYMid slice"
            >
              {/* Pitch outline */}
              <rect x="100" y="100" width="1000" height="600" fill="none" stroke="#2ECC71" strokeWidth="2"/>
              {/* Centre circle */}
              <circle cx="600" cy="400" r="80" fill="none" stroke="#2ECC71" strokeWidth="2"/>
              <circle cx="600" cy="400" r="4" fill="#2ECC71"/>
              {/* Centre line */}
              <line x1="600" y1="100" x2="600" y2="700" stroke="#2ECC71" strokeWidth="2"/>
              {/* Goal areas */}
              <rect x="100" y="320" width="60" height="160" fill="none" stroke="#2ECC71" strokeWidth="2"/>
              <rect x="1040" y="320" width="60" height="160" fill="none" stroke="#2ECC71" strokeWidth="2"/>
              {/* Goals */}
              <rect x="80" y="360" width="20" height="80" fill="none" stroke="#2ECC71" strokeWidth="2"/>
              <rect x="1100" y="360" width="20" height="80" fill="none" stroke="#2ECC71" strokeWidth="2"/>
            </svg>
            <div className="absolute inset-0 bg-hero-mesh" />
          </motion.div>

          <div className="relative z-10 text-center max-w-5xl mx-auto px-4">
            <motion.div
              initial={{ opacity: 0, y: 30 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1] }}
            >
              <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full
                bg-pitch-green/10 border border-pitch-green/30 text-pitch-green text-xs
                font-medium mb-8">
                <span className="w-1.5 h-1.5 rounded-full bg-pitch-green animate-pulse" />
                Five-a-Side Analytics · Powered by YOLOv8 + Roboflow
              </div>

              <h1 className="text-5xl sm:text-7xl font-bold text-pitch-white leading-[1.05] tracking-tight mb-6">
                Unveil the{' '}
                <span className="text-transparent bg-clip-text bg-gradient-to-r from-pitch-green to-pitch-indigo-glow">
                  Geometry
                </span>
                <br />
                of Your Game.
              </h1>

              <p className="text-pitch-muted text-lg sm:text-xl max-w-2xl mx-auto leading-relaxed mb-10">
                Upload your five-a-side match footage. Receive professional-grade analytics—xG maps,
                Voronoi dominance, possession chains—in under 60 seconds. For every pitch, everywhere.
              </p>

              <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
                {user ? (
                  <Link href="/upload" className="pitch-button-primary text-base px-7 py-3.5">
                    Upload a Match <ArrowRight size={18} />
                  </Link>
                ) : (
                  <button onClick={() => setAuthOpen(true)} className="pitch-button-primary text-base px-7 py-3.5">
                    Get Started Free <ArrowRight size={18} />
                  </button>
                )}
                <Link href="#how-it-works" className="pitch-button-secondary text-base px-7 py-3.5">
                  How It Works
                </Link>
              </div>
            </motion.div>

            {/* Stats strip */}
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.4, duration: 0.6 }}
              className="mt-20 grid grid-cols-2 sm:grid-cols-4 gap-6 max-w-2xl mx-auto"
            >
              {STATS.map(({ value, label }) => (
                <div key={label} className="text-center">
                  <div className="text-2xl sm:text-3xl font-bold text-pitch-white font-mono">{value}</div>
                  <div className="text-pitch-muted text-xs mt-1">{label}</div>
                </div>
              ))}
            </motion.div>
          </div>

          <a href="#how-it-works" className="absolute bottom-8 left-1/2 -translate-x-1/2 text-pitch-muted hover:text-pitch-white transition-colors">
            <ChevronDown size={24} className="animate-bounce" />
          </a>
        </section>

        {/* How It Works */}
        <section id="how-it-works" className="py-24 px-4 bg-pitch-indigo-deep/20">
          <div className="max-w-5xl mx-auto">
            <div className="text-center mb-16">
              <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full
                bg-pitch-green/10 border border-pitch-green/30 text-pitch-green text-xs
                font-medium mb-6">
                Simple · Fast · Professional
              </div>
              <h2 className="text-4xl font-bold text-pitch-white mb-4">How It Works</h2>
              <p className="text-pitch-muted text-lg max-w-xl mx-auto">
                From raw footage to professional analytics in three steps — no hardware, no setup.
              </p>
            </div>

            <div className="grid md:grid-cols-3 gap-8 relative">
              {/* Connector line (desktop) */}
              <div className="hidden md:block absolute top-16 left-[calc(16.67%+1rem)] right-[calc(16.67%+1rem)] h-px bg-gradient-to-r from-pitch-green/20 via-pitch-indigo-glow/40 to-pitch-green/20" />

              {[
                {
                  step: '01',
                  icon: (
                    <svg viewBox="0 0 40 40" className="w-6 h-6 text-pitch-green" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                      <rect x="6" y="10" width="28" height="20" rx="3"/>
                      <circle cx="20" cy="20" r="5"/>
                      <path d="M6 15 L4 15 L4 25 L6 25"/>
                      <path d="M34 15 L36 15 L36 25 L34 25"/>
                      <rect x="17" y="8" width="6" height="3" rx="1"/>
                    </svg>
                  ),
                  title: 'Record Your Match',
                  description: 'Film with any smartphone from the touchline or an elevated angle. No special equipment needed — a 10-minute clip is enough.',
                  badge: 'Any device',
                },
                {
                  step: '02',
                  icon: (
                    <svg viewBox="0 0 40 40" className="w-6 h-6 text-pitch-green" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M20 8 L20 26"/>
                      <path d="M14 20 L20 26 L26 20"/>
                      <rect x="8" y="28" width="24" height="5" rx="2"/>
                      <path d="M28 14 C32 14 34 16 34 20"/>
                      <circle cx="34" cy="10" r="3"/>
                    </svg>
                  ),
                  title: 'Upload or Paste a Link',
                  description: 'Drag your video file directly, or paste a Google Drive share link. No large uploads needed — we fetch it server-side.',
                  badge: 'File or Google Drive',
                },
                {
                  step: '03',
                  icon: (
                    <svg viewBox="0 0 40 40" className="w-6 h-6 text-pitch-green" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                      <rect x="6" y="8" width="28" height="24" rx="3"/>
                      <path d="M12 18 L18 24 L28 14"/>
                      <path d="M12 28 L28 28"/>
                      <path d="M12 32 L20 32"/>
                    </svg>
                  ),
                  title: 'Get Professional Analytics',
                  description: 'YOLOv8 detects every player and the ball. Receive xG maps, possession heatmaps, pass networks, and a downloadable PDF report — in under 60 seconds.',
                  badge: 'Results in < 60s',
                },
              ].map(({ step, icon, title, description, badge }, i) => (
                <motion.div
                  key={step}
                  initial={{ opacity: 0, y: 24 }}
                  whileInView={{ opacity: 1, y: 0 }}
                  viewport={{ once: true }}
                  transition={{ delay: i * 0.15, duration: 0.55, ease: [0.16, 1, 0.3, 1] }}
                  className="relative flex flex-col items-center text-center group"
                >
                  {/* Step circle */}
                  <div className="relative z-10 w-14 h-14 rounded-2xl bg-pitch-indigo-deep border border-pitch-green/30
                    flex items-center justify-center mb-6 group-hover:border-pitch-green/60
                    group-hover:bg-pitch-green/10 transition-all duration-300 shadow-lg">
                    {icon}
                    <span className="absolute -top-2 -right-2 w-5 h-5 rounded-full bg-pitch-green
                      text-pitch-black text-[10px] font-black flex items-center justify-center">
                      {i + 1}
                    </span>
                  </div>

                  <div className="glass-card p-6 w-full hover:border-pitch-green/20 transition-all duration-300">
                    <div className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full
                      bg-pitch-green/10 text-pitch-green text-[11px] font-medium mb-3">
                      {badge}
                    </div>
                    <h3 className="text-lg font-semibold text-pitch-white mb-2">{title}</h3>
                    <p className="text-pitch-muted text-sm leading-relaxed">{description}</p>
                  </div>
                </motion.div>
              ))}
            </div>

            <div className="mt-12 text-center">
              <Link href="/upload" className="pitch-button-primary text-base px-8 py-3.5">
                Analyse Your Match Now <ArrowRight size={18} />
              </Link>
            </div>
          </div>
        </section>

        {/* Features */}
        <section id="features" className="py-24 px-4 max-w-7xl mx-auto">
          <div className="text-center mb-16">
            <h2 className="text-4xl font-bold text-pitch-white mb-4">
              A Von Neumann Machine for the Pitch
            </h2>
            <p className="text-pitch-muted text-lg max-w-2xl mx-auto">
              Every pass, pivot, and pressure processed through a battle-tested pipeline—
              from raw footage to boardroom-grade insight.
            </p>
          </div>

          <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-6">
            {FEATURES.map(({ icon: Icon, title, description }, i) => (
              <motion.div
                key={title}
                initial={{ opacity: 0, y: 20 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true }}
                transition={{ delay: i * 0.1, duration: 0.5 }}
                className="glass-card p-6 hover:border-pitch-green/30 transition-all group"
              >
                <div className="w-10 h-10 bg-pitch-green/10 border border-pitch-green/20 rounded-xl
                  flex items-center justify-center mb-4 group-hover:bg-pitch-green/20 transition-colors">
                  <Icon size={20} className="text-pitch-green" />
                </div>
                <h3 className="font-semibold text-pitch-white mb-2">{title}</h3>
                <p className="text-pitch-muted text-sm leading-relaxed">{description}</p>
              </motion.div>
            ))}
          </div>
        </section>

        {/* Pipeline */}
        <section className="py-24 px-4 bg-pitch-indigo-deep/30">
          <div className="max-w-5xl mx-auto">
            <div className="text-center mb-16">
              <h2 className="text-4xl font-bold text-pitch-white mb-4">The Pipeline</h2>
              <p className="text-pitch-muted">Five steps from raw footage to revelation.</p>
            </div>
            <div className="space-y-4">
              {[
                { n: '01', title: 'Ingest', desc: 'Upload MP4 via drag-and-drop. Firebase Storage encrypts and stores with 30-day auto-expiry.' },
                { n: '02', title: 'Orchestrate', desc: 'Cloud Function triggers, mints signed URLs, and queues the Python AI engine with rate-limiting.' },
                { n: '03', title: 'Compute', desc: 'YOLOv8 detects, ByteTrack tracks, SigLIP clusters teams. Homography warps to standardized 42×25m grid.' },
                { n: '04', title: 'Analyse', desc: 'xG via logistic regression. Voronoi tessellations. Gaussian heatmaps. Pass network graphs. All atomic.' },
                { n: '05', title: 'Reveal', desc: 'Real-time Firestore streams feed interactive dashboards and one-click PDF reports with narrative prose.' },
              ].map(({ n, title, desc }, i) => (
                <motion.div
                  key={n}
                  initial={{ opacity: 0, x: -20 }}
                  whileInView={{ opacity: 1, x: 0 }}
                  viewport={{ once: true }}
                  transition={{ delay: i * 0.08 }}
                  className="flex gap-6 glass-card p-5"
                >
                  <span className="text-3xl font-black text-pitch-indigo-glow/40 font-mono shrink-0">{n}</span>
                  <div>
                    <h3 className="font-semibold text-pitch-white mb-1">{title}</h3>
                    <p className="text-pitch-muted text-sm">{desc}</p>
                  </div>
                </motion.div>
              ))}
            </div>
          </div>
        </section>

        {/* CTA */}
        <section className="py-24 px-4 text-center">
          <motion.div
            initial={{ opacity: 0, scale: 0.95 }}
            whileInView={{ opacity: 1, scale: 1 }}
            viewport={{ once: true }}
            className="max-w-2xl mx-auto glass-card p-12"
          >
            <h2 className="text-4xl font-bold text-pitch-white mb-4">Ready to see the field differently?</h2>
            <p className="text-pitch-muted mb-8">
              Free tier: 3 matches/month. Upgrade to Analyst tier for unlimited matches and club integrations.
            </p>
            {user ? (
              <Link href="/upload" className="pitch-button-primary text-base px-8 py-3.5">
                Upload Your First Match <ArrowRight size={18} />
              </Link>
            ) : (
              <button onClick={() => setAuthOpen(true)} className="pitch-button-primary text-base px-8 py-3.5">
                Start for Free <ArrowRight size={18} />
              </button>
            )}
          </motion.div>
        </section>

        <footer className="border-t border-pitch-indigo-soft/20 py-8 px-4 text-center text-pitch-muted text-sm">
          <p>© 2024 Pitchlens · Built with Next.js, Firebase, and Roboflow</p>
          <p className="mt-1 text-xs opacity-60">
            "Every pass, pivot, and pressure reveals the hidden mathematics of the game."
          </p>
        </footer>
      </main>

      <AuthModal isOpen={authOpen} onClose={() => setAuthOpen(false)} />
    </>
  );
}
