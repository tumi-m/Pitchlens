"use client";
import { useEffect, useRef, useState } from "react";
import { animate, motion, useInView } from "framer-motion";
import { ArrowLeftRight, Send, Info } from "lucide-react";
import type { VisionResult } from "@/lib/review/vision";
import { clockTime } from "@/lib/review/vision";
import type { MatchStats } from "@/lib/review/visionStats";

type Props = {
  result: VisionResult;
  stats: MatchStats;
  names: string[];
  colours: [string, string];
  onSeek: (t: number) => void;
};

/** Number that counts up the first time it scrolls into view. */
function CountUp({
  value,
  decimals = 0,
  format,
}: {
  value: number;
  decimals?: number;
  format?: (v: number) => string;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const seen = useInView(ref, { once: true });
  const [shown, setShown] = useState(0);
  useEffect(() => {
    if (!seen) return;
    const control = animate(0, value, {
      duration: 1.1,
      ease: [0.16, 1, 0.3, 1],
      onUpdate: setShown,
    });
    return () => control.stop();
  }, [seen, value]);
  return (
    <span ref={ref} className="tabular-nums">
      {format ? format(shown) : shown.toFixed(decimals)}
    </span>
  );
}

function Shirt({ colour, size = 56 }: { colour: string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden>
      <path
        d="M22 6 L12 10 L2 22 L11 30 L16 25 L16 58 L48 58 L48 25 L53 30 L62 22 L52 10 L42 6 C40 11 36 14 32 14 C28 14 24 11 22 6 Z"
        fill={colour}
        stroke="rgba(255,255,255,0.35)"
        strokeWidth="1.5"
      />
      <path d="M22 6 C24 11 28 14 32 14 C36 14 40 11 42 6" fill="none" stroke="rgba(0,0,0,0.25)" strokeWidth="2" />
    </svg>
  );
}

function scoreColour(score: number) {
  if (score >= 7) return "#22c55e";
  if (score >= 5) return "#f59e0b";
  return "#ef4444";
}

function StatRow({
  label,
  values,
  colours,
  format = (v) => String(v),
  hint,
}: {
  label: string;
  values: [number | null, number | null];
  colours: [string, string];
  format?: (v: number) => string;
  hint?: string;
}) {
  const [a, b] = values;
  const known = a !== null && b !== null;
  const total = known ? a + b : 0;
  const shareA = known && total > 0 ? a / total : 0;
  const shareB = known && total > 0 ? b / total : 0;
  const leader = known && a !== b ? (a > b ? 0 : 1) : null;
  const pill = (v: number | null, side: 0 | 1) => (
    <span
      className="min-w-[3.5rem] text-center px-2.5 py-1 rounded-full text-sm font-bold"
      style={
        leader === side
          ? { background: colours[side], color: "#0b0f1a" }
          : undefined
      }
    >
      {v === null ? "—" : format(v)}
    </span>
  );
  return (
    <div className="py-3">
      <div className="flex items-center justify-between gap-3">
        {pill(a, 0)}
        <span className="text-sm text-pitch-muted text-center flex items-center gap-1.5">
          {label}
          {hint && (
            <span title={hint} className="cursor-help opacity-70">
              <Info size={13} />
            </span>
          )}
        </span>
        {pill(b, 1)}
      </div>
      <div className="flex gap-1.5 mt-2 h-1.5">
        <div className="flex-1 flex justify-end rounded-full bg-white/5 overflow-hidden">
          <motion.div
            className="h-full rounded-full"
            style={{ background: colours[0] }}
            initial={{ width: 0 }}
            whileInView={{ width: `${shareA * 100}%` }}
            viewport={{ once: true }}
            transition={{ duration: 0.9, ease: [0.16, 1, 0.3, 1] }}
          />
        </div>
        <div className="flex-1 rounded-full bg-white/5 overflow-hidden">
          <motion.div
            className="h-full rounded-full"
            style={{ background: colours[1] }}
            initial={{ width: 0 }}
            whileInView={{ width: `${shareB * 100}%` }}
            viewport={{ once: true }}
            transition={{ duration: 0.9, ease: [0.16, 1, 0.3, 1] }}
          />
        </div>
      </div>
    </div>
  );
}

function Ring({ value, label, colour }: { value: number; label: string; colour: string }) {
  const r = 34;
  const c = 2 * Math.PI * r;
  return (
    <div className="flex flex-col items-center gap-2">
      <div className="relative w-24 h-24">
        <svg viewBox="0 0 80 80" className="w-full h-full -rotate-90">
          <circle cx="40" cy="40" r={r} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="7" />
          <motion.circle
            cx="40"
            cy="40"
            r={r}
            fill="none"
            stroke={colour}
            strokeWidth="7"
            strokeLinecap="round"
            strokeDasharray={c}
            initial={{ strokeDashoffset: c }}
            whileInView={{ strokeDashoffset: c * (1 - Math.min(100, value) / 100) }}
            viewport={{ once: true }}
            transition={{ duration: 1.2, ease: [0.16, 1, 0.3, 1] }}
          />
        </svg>
        <span className="absolute inset-0 flex items-center justify-center text-lg font-bold">
          <CountUp value={value} decimals={value < 10 ? 1 : 0} format={(v) => `${v.toFixed(value < 10 ? 1 : 0)}%`} />
        </span>
      </div>
      <span className="text-xs text-pitch-muted text-center">{label}</span>
    </div>
  );
}

const card = "rounded-2xl border border-white/10 bg-[#11162a]/80 backdrop-blur p-5";

export function MatchCentre({ result, stats, names, colours, onSeek }: Props) {
  const [hover, setHover] = useState<number | null>(null);
  const duration = result.analysedDuration || 1;
  // A share built on a sliver of the match is noise: withhold it below 20%.
  const shareKnown =
    stats.controlCoverage >= 20 &&
    stats.controlShare[0] !== null &&
    stats.controlShare[1] !== null;
  const shareValues: [number | null, number | null] = shareKnown
    ? stats.controlShare
    : [null, null];
  const lowEvidence = stats.controlCoverage < 50;
  const events = result.metrics.events;
  return (
    <div className="space-y-6">
      {/* Scoreboard */}
      <motion.section
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
        className="relative overflow-hidden rounded-3xl border border-white/10"
        style={{
          background: `linear-gradient(115deg, ${colours[0]}33 0%, #0e1326 38%, #0e1326 62%, ${colours[1]}33 100%)`,
        }}
      >
        <svg className="absolute inset-0 w-full h-full opacity-[0.07]" viewBox="0 0 400 200" preserveAspectRatio="none" aria-hidden>
          <rect x="6" y="6" width="388" height="188" fill="none" stroke="#fff" strokeWidth="1.5" />
          <line x1="200" y1="6" x2="200" y2="194" stroke="#fff" strokeWidth="1.5" />
          <circle cx="200" cy="100" r="30" fill="none" stroke="#fff" strokeWidth="1.5" />
          <rect x="6" y="60" width="40" height="80" fill="none" stroke="#fff" strokeWidth="1.5" />
          <rect x="354" y="60" width="40" height="80" fill="none" stroke="#fff" strokeWidth="1.5" />
        </svg>
        <div className="relative grid grid-cols-[1fr_auto_1fr] items-center gap-4 px-5 py-8 sm:px-10">
          <motion.div
            initial={{ opacity: 0, x: -24 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: 0.15, duration: 0.6 }}
            className="flex flex-col items-center gap-2"
          >
            <Shirt colour={colours[0]} size={64} />
            <span className="font-bold text-lg text-center break-words">{names[0]}</span>
            <span className="text-xs text-pitch-muted">~{stats.avgPlayers[0]} players tracked</span>
          </motion.div>
          <div className="flex flex-col items-center gap-2 text-center">
            <span className="text-[11px] uppercase tracking-[0.2em] text-pitch-muted">Ball control</span>
            <div className="text-4xl sm:text-5xl font-black tabular-nums flex items-baseline gap-2">
              {shareKnown ? (
                <>
                  <span style={{ color: colours[0] }}>
                    <CountUp value={stats.controlShare[0] ?? 0} format={(v) => `${Math.round(v)}`} />
                  </span>
                  <span className="text-pitch-muted text-3xl">–</span>
                  <span style={{ color: colours[1] }}>
                    <CountUp value={stats.controlShare[1] ?? 0} format={(v) => `${Math.round(v)}`} />
                  </span>
                </>
              ) : (
                <span className="text-pitch-muted">– : –</span>
              )}
            </div>
            <span className="text-xs text-pitch-muted">
              {shareKnown ? "% of observed control" : "Not enough ball evidence"}
            </span>
            <div className="flex items-center gap-2 mt-2 text-xs text-pitch-muted">
              <span>{clockTime(result.analysedDuration)} analysed</span>
              <span>·</span>
              <span
                className="px-2 py-0.5 rounded-md font-bold text-[#0b0f1a]"
                style={{ background: scoreColour(stats.evidenceScore) }}
                title="Evidence score: 30% player coverage, 40% ball coverage, 30% ball-control coverage, out of 10"
              >
                {stats.evidenceScore.toFixed(1)}
              </span>
              <span>evidence</span>
            </div>
          </div>
          <motion.div
            initial={{ opacity: 0, x: 24 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: 0.15, duration: 0.6 }}
            className="flex flex-col items-center gap-2"
          >
            <Shirt colour={colours[1]} size={64} />
            <span className="font-bold text-lg text-center break-words">{names[1]}</span>
            <span className="text-xs text-pitch-muted">~{stats.avgPlayers[1]} players tracked</span>
          </motion.div>
        </div>
        {lowEvidence && (
          <p className="relative border-t border-white/10 px-5 py-3 text-xs text-amber-200/90 bg-amber-400/5">
            The ball was tracked near a player for only {stats.controlCoverage}% of the video, so control and pass figures describe that part only.
            Film at 1080p from one fixed, high camera for complete stats.
          </p>
        )}
      </motion.section>

      <div className="grid lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] gap-6">
        {/* Head-to-head stats */}
        <section className={card}>
          <div className="flex items-center justify-between mb-2">
            <span className="flex items-center gap-2 text-sm font-semibold">
              <span className="w-2.5 h-2.5 rounded-full" style={{ background: colours[0] }} />
              {names[0]}
            </span>
            <h2 className="text-xs uppercase tracking-widest text-pitch-muted">Match stats</h2>
            <span className="flex items-center gap-2 text-sm font-semibold">
              {names[1]}
              <span className="w-2.5 h-2.5 rounded-full" style={{ background: colours[1] }} />
            </span>
          </div>
          <div className="divide-y divide-white/5">
            <StatRow
              label="Ball control"
              values={shareValues}
              colours={colours}
              format={(v) => `${Math.round(v)}%`}
              hint="Share of the time the ball was stably next to a player of each kit, among the time it was observed."
            />
            <StatRow
              label="Control time"
              values={stats.controlSeconds}
              colours={colours}
              format={(v) => clockTime(v)}
            />
            <StatRow
              label="Pass candidates"
              values={stats.passCandidates}
              colours={colours}
              hint="Ball moved between two players of the same kit while visible. Unreviewed."
            />
            <StatRow
              label="Turnovers won"
              values={stats.turnoversWon}
              colours={colours}
              hint="Ball moved from the other kit to this kit while visible. Unreviewed."
            />
            <StatRow
              label="Players tracked (avg)"
              values={stats.avgPlayers}
              colours={colours}
              format={(v) => v.toFixed(1)}
            />
            <StatRow label="Players tracked (peak)" values={stats.peakPlayers} colours={colours} />
          </div>
        </section>

        <div className="space-y-6">
          {/* Momentum */}
          <section className={card}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-xs uppercase tracking-widest text-pitch-muted">Control momentum</h2>
              <span className="text-xs text-pitch-muted">
                {hover !== null ? `Minute ${hover + 1}` : "Per minute · click to watch"}
              </span>
            </div>
            <svg viewBox={`0 0 ${stats.minutes * 10} 80`} className="w-full h-32" preserveAspectRatio="none">
              <line x1="0" y1="40" x2={stats.minutes * 10} y2="40" stroke="rgba(255,255,255,0.15)" strokeWidth="0.6" />
              {stats.momentum.map((v, i) => {
                const h = v === null ? 0 : Math.max(1.5, Math.abs(v) * 36);
                const up = (v ?? 0) >= 0;
                return (
                  <g key={i} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} onClick={() => onSeek(i * 60)} className="cursor-pointer">
                    <rect x={i * 10} y="0" width="10" height="80" fill="transparent" />
                    {v === null ? (
                      <rect x={i * 10 + 2} y="39" width="6" height="2" fill="rgba(255,255,255,0.15)" />
                    ) : (
                      <motion.rect
                        x={i * 10 + 1.5}
                        width="7"
                        rx="1.5"
                        fill={up ? colours[0] : colours[1]}
                        opacity={hover === null || hover === i ? 1 : 0.45}
                        initial={{ height: 0, y: 40 }}
                        whileInView={{ height: h, y: up ? 40 - h : 40 }}
                        viewport={{ once: true }}
                        transition={{ delay: i * 0.012, duration: 0.5 }}
                      />
                    )}
                  </g>
                );
              })}
            </svg>
            <div className="flex justify-between text-[11px] text-pitch-muted mt-1">
              <span style={{ color: colours[0] }}>▲ {names[0]}</span>
              <span>Grey = ball not observed</span>
              <span style={{ color: colours[1] }}>▼ {names[1]}</span>
            </div>
          </section>

          {/* Detection quality */}
          <section className={card}>
            <h2 className="text-xs uppercase tracking-widest text-pitch-muted mb-4">Tracking quality</h2>
            <div className="grid grid-cols-3 gap-2">
              <Ring value={stats.playerCoverage} label="Frames with players" colour="#22c55e" />
              <Ring value={stats.ballCoverage} label="Frames with the ball" colour="#38bdf8" />
              <Ring value={stats.controlCoverage} label="Time with ball control" colour="#a78bfa" />
            </div>
          </section>
        </div>
      </div>

      {/* Timeline */}
      <section className={card}>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-xs uppercase tracking-widest text-pitch-muted">Match timeline</h2>
          <span className="text-xs text-pitch-muted">{events.length} automatic moments · click to watch</span>
        </div>
        <div className="relative h-14">
          <div className="absolute left-0 right-0 top-1/2 h-[3px] -translate-y-1/2 rounded-full bg-white/10" />
          <motion.div
            className="absolute left-0 top-1/2 h-[3px] -translate-y-1/2 rounded-full bg-gradient-to-r from-pitch-green/70 to-pitch-green/20"
            initial={{ width: 0 }}
            whileInView={{ width: "100%" }}
            viewport={{ once: true }}
            transition={{ duration: 1.2 }}
          />
          {events.map((e, i) => {
            const Icon = e.type === "pass-candidate" ? Send : ArrowLeftRight;
            const above = e.team === 0;
            return (
              <motion.button
                key={e.id}
                onClick={() => onSeek(Math.max(0, e.t - 2))}
                title={`${clockTime(e.t)} · ${names[e.team]} · ${e.type === "pass-candidate" ? "pass candidate" : "turnover won"}`}
                className="absolute -translate-x-1/2 w-6 h-6 rounded-full flex items-center justify-center shadow-lg ring-2 ring-[#11162a]"
                style={{ left: `${(e.t / duration) * 100}%`, top: above ? 0 : "auto", bottom: above ? "auto" : 0, background: colours[e.team] }}
                initial={{ scale: 0 }}
                whileInView={{ scale: 1 }}
                viewport={{ once: true }}
                transition={{ delay: 0.3 + Math.min(i, 40) * 0.02, type: "spring", stiffness: 400, damping: 18 }}
                whileHover={{ scale: 1.25 }}
              >
                <Icon size={12} className="text-[#0b0f1a]" />
              </motion.button>
            );
          })}
        </div>
        <div className="flex justify-between text-[11px] text-pitch-muted mt-2">
          <span>0:00</span>
          <span>{clockTime(result.analysedDuration / 2)}</span>
          <span>{clockTime(result.analysedDuration)}</span>
        </div>
        {!events.length && (
          <p className="text-sm text-pitch-muted mt-3">
            No passes or turnovers could be confirmed: the ball has to stay visible through the whole transfer.
          </p>
        )}
      </section>
    </div>
  );
}
