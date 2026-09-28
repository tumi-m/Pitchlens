"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { AlertTriangle, Check, Cpu, Film, Palette, ScanLine, BarChart3 } from "lucide-react";
import type { VisionJob } from "@/lib/review/vision";
import { clockTime } from "@/lib/review/vision";

const STEPS = [
  { key: "upload", label: "Video received", icon: Film },
  { key: "kits", label: "Learning kit colours", icon: Palette },
  { key: "detect", label: "Tracking players & ball", icon: ScanLine },
  { key: "measure", label: "Building your stats", icon: BarChart3 },
];

// Things worth knowing while you wait — about filming and the game, not made-up numbers.
const TIPS = [
  "Film from high up behind a goal or the halfway line: the ball stays visible for longer.",
  "1080p makes the ball roughly nine times more pixels than 360p — that is what fills possession stats.",
  "A fixed camera beats a panning one: every camera cut resets the tracking.",
  "Bright, contrasting kits make team detection far more reliable.",
  "Possession share describes style, not quality: many teams win with less of the ball.",
  "Passes are counted only when the ball stays visible from one player to the next.",
  "In five-a-side the ball is often hidden by legs — that is why tracking coverage is shown.",
  "You can close this tab. The analysis keeps running and appears in Your matches.",
];

function stepIndex(job: VisionJob) {
  const stage = job.stage.toLowerCase();
  if (job.status === "uploading" || stage.includes("download") || stage.includes("receiv")) return 0;
  if (stage.includes("measur") || job.progress >= 95) return 3;
  // "tracking kits" contains "kit": test the kit-learning stage by its own words.
  if (stage.includes("learning kit")) return 1;
  if (stage.includes("detect") || stage.includes("track") || job.progress > 5) return 2;
  return 0;
}

/** Animated five-a-side pitch: players drift, the ball is passed around, a scan beam sweeps. */
export function PitchAnimation({ progress }: { progress: number }) {
  const reduce = useReducedMotion();
  const home = [
    [60, 100], [120, 60], [120, 140], [180, 80], [200, 125],
  ];
  const away = [
    [340, 100], [280, 60], [280, 140], [230, 95], [250, 150],
  ];
  const passRoute = [home[1], home[3], away[3], home[4], home[2], home[3]];
  return (
    <div className="relative overflow-hidden rounded-2xl border border-white/10 bg-gradient-to-b from-[#123a2c] to-[#0d2a21]">
      <svg viewBox="0 0 400 200" className="w-full h-auto block" aria-hidden>
        {/* Mowing stripes */}
        {Array.from({ length: 8 }).map((_, i) => (
          <rect key={i} x={i * 50} y="0" width="25" height="200" fill="rgba(255,255,255,0.025)" />
        ))}
        <g fill="none" stroke="rgba(255,255,255,0.35)" strokeWidth="1.5">
          <rect x="10" y="10" width="380" height="180" rx="6" />
          <line x1="200" y1="10" x2="200" y2="190" />
          <circle cx="200" cy="100" r="28" />
          <path d="M10 60 h40 v80 h-40 M390 60 h-40 v80 h40" />
        </g>
        {/* Players */}
        {[...home.map((p) => [...p, 0]), ...away.map((p) => [...p, 1])].map(([x, y, team], i) => (
          <motion.circle
            key={i}
            r="6"
            fill={team === 0 ? "#f87171" : "#60a5fa"}
            stroke="rgba(0,0,0,0.35)"
            strokeWidth="1.5"
            initial={{ cx: x, cy: y }}
            animate={
              reduce
                ? { cx: x, cy: y }
                : { cx: [x, x + (i % 2 ? 14 : -12), x + (i % 3 ? -6 : 10), x], cy: [y, y + (i % 2 ? -10 : 12), y + 6, y] }
            }
            transition={{ duration: 6 + (i % 4), repeat: Infinity, ease: "easeInOut" }}
          />
        ))}
        {/* Ball passing between players */}
        <motion.circle
          r="3.5"
          fill="#fff"
          animate={
            reduce
              ? { cx: passRoute[0][0], cy: passRoute[0][1] }
              : { cx: passRoute.map((p) => p[0]), cy: passRoute.map((p) => p[1]) }
          }
          transition={{ duration: 7, repeat: Infinity, ease: "easeInOut" }}
        />
        {/* Detection brackets that pulse over players */}
        {!reduce &&
          home.slice(0, 3).map(([x, y], i) => (
            <motion.rect
              key={`box-${i}`}
              x={x - 10}
              y={y - 12}
              width="20"
              height="24"
              rx="3"
              fill="none"
              stroke="#4ade80"
              strokeWidth="1.2"
              animate={{ opacity: [0, 1, 0] }}
              transition={{ duration: 2.4, delay: i * 0.8, repeat: Infinity }}
            />
          ))}
      </svg>
      {/* Scanning beam */}
      {!reduce && (
        <motion.div
          className="absolute inset-y-0 w-24 bg-gradient-to-r from-transparent via-emerald-300/20 to-transparent"
          animate={{ left: ["-20%", "110%"] }}
          transition={{ duration: 3.2, repeat: Infinity, ease: "linear" }}
        />
      )}
      {/* Progress fill along the bottom edge */}
      <motion.div
        className="absolute bottom-0 left-0 h-1 bg-gradient-to-r from-emerald-400 to-sky-400"
        animate={{ width: `${Math.max(2, Math.min(100, progress))}%` }}
        transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1] }}
      />
    </div>
  );
}

export function AnalysisWait({
  job,
  onCancel,
}: {
  job: VisionJob;
  onCancel: () => void;
}) {
  const [tip, setTip] = useState(0);
  const running = ["processing", "uploading"].includes(job.status);
  const failed = ["failed", "interrupted", "cancelled"].includes(job.status);
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setTip((t) => (t + 1) % TIPS.length), 7000);
    return () => clearInterval(timer);
  }, [running]);
  const current = stepIndex(job);
  const gpu = job.stage.startsWith("GPU");
  const stage = job.stage.replace(/^GPU · /, "");

  if (failed)
    return (
      <motion.section
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        className="rounded-2xl border border-red-400/30 bg-red-500/5 p-6 space-y-4"
        role="alert"
      >
        <div className="flex gap-3 items-start">
          <AlertTriangle className="text-red-300 shrink-0 mt-0.5" />
          <div>
            <h2 className="text-lg font-semibold">
              {job.status === "cancelled" ? "Analysis cancelled" : "This analysis didn’t finish"}
            </h2>
            <p className="text-pitch-muted mt-1">{job.stage}</p>
          </div>
        </div>
        <Link href="/upload" className="pitch-button-primary inline-flex">
          Try another analysis
        </Link>
      </motion.section>
    );

  return (
    <section className="rounded-3xl border border-white/10 bg-[#0f1427]/80 p-5 sm:p-7 space-y-6" aria-live="polite">
      <div className="grid lg:grid-cols-[1.3fr_1fr] gap-6 items-center">
        <PitchAnimation progress={job.progress} />
        <div className="space-y-5">
          <div className="flex items-center gap-2 text-xs uppercase tracking-widest text-pitch-muted">
            {gpu && (
              <span className="inline-flex items-center gap-1 rounded-full bg-emerald-400/10 text-emerald-300 px-2 py-0.5 normal-case tracking-normal">
                <Cpu size={12} /> GPU
              </span>
            )}
            Analysing your match
          </div>
          <h2 className="text-xl font-semibold leading-snug">{stage}</h2>
          <div>
            <motion.p
              key={Math.floor(job.progress)}
              initial={{ opacity: 0.4, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              className="text-5xl font-black tabular-nums"
            >
              {Math.floor(job.progress)}
              <span className="text-2xl text-pitch-muted">%</span>
            </motion.p>
            <p className="text-sm text-pitch-muted mt-1">
              {job.processedSeconds !== undefined && `${clockTime(job.processedSeconds)} of footage processed`}
              {job.etaSeconds ? ` · about ${clockTime(job.etaSeconds)} left` : ""}
            </p>
          </div>
          <ol className="space-y-2.5">
            {STEPS.map(({ key, label, icon: Icon }, i) => {
              const done = i < current;
              const active = i === current;
              return (
                <li key={key} className="flex items-center gap-3">
                  <span
                    className={`relative w-8 h-8 rounded-full flex items-center justify-center border ${
                      done
                        ? "bg-emerald-400 border-emerald-400 text-[#0b0f1a]"
                        : active
                          ? "border-emerald-400 text-emerald-300"
                          : "border-white/15 text-pitch-muted"
                    }`}
                  >
                    {done ? <Check size={15} /> : <Icon size={15} />}
                    {active && (
                      <motion.span
                        className="absolute inset-0 rounded-full border-2 border-emerald-400"
                        animate={{ scale: [1, 1.45], opacity: [0.7, 0] }}
                        transition={{ duration: 1.4, repeat: Infinity }}
                      />
                    )}
                  </span>
                  <span className={active ? "text-pitch-white font-medium" : done ? "text-pitch-muted line-through decoration-white/20" : "text-pitch-muted"}>
                    {label}
                  </span>
                </li>
              );
            })}
          </ol>
        </div>
      </div>
      <div className="flex flex-col sm:flex-row sm:items-center gap-4 justify-between border-t border-white/10 pt-5">
        <div className="min-h-[3rem] flex-1">
          <AnimatePresence mode="wait">
            <motion.p
              key={tip}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.35 }}
              className="text-sm text-pitch-muted"
            >
              <span className="text-emerald-300 font-medium">Tip · </span>
              {TIPS[tip]}
            </motion.p>
          </AnimatePresence>
        </div>
        {running && (
          <button className="pitch-button-secondary shrink-0" onClick={onCancel}>
            Cancel analysis
          </button>
        )}
      </div>
    </section>
  );
}
