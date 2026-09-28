"use client";
import { useState } from "react";
import { motion } from "framer-motion";
import { Info, Target, Crosshair } from "lucide-react";
import type { Analysis, CountStat, TeamStats } from "@/lib/review/analysis";
import { describeEvent } from "@/lib/review/analysis";
import { clockTime } from "@/lib/review/vision";
import { AveragePositions, Heatmap, ShotMap } from "@/components/vision/PitchGraphics";

const card = "rounded-2xl border border-white/10 bg-[#11162a]/80 backdrop-blur p-5";

type Value = { value: number | null; note?: string };

function count(c: CountStat | null | undefined): Value {
  if (!c) return { value: null };
  return { value: c.value, note: c.pending ? `${c.confirmed} ✓ · ${c.pending} to review` : c.value ? "reviewed" : undefined };
}

function Row({
  label,
  a,
  b,
  colours,
  format = (v) => String(v),
  hint,
}: {
  label: string;
  a: Value;
  b: Value;
  colours: [string, string];
  format?: (v: number) => string;
  hint?: string;
}) {
  const known = a.value !== null && b.value !== null;
  const total = known ? (a.value as number) + (b.value as number) : 0;
  const share = (v: number | null) => (known && total > 0 ? (v as number) / total : 0);
  const leader = known && a.value !== b.value ? ((a.value as number) > (b.value as number) ? 0 : 1) : null;
  const pill = (v: Value, side: 0 | 1) => (
    <span className="flex flex-col items-center min-w-[4.5rem]">
      <span
        className="px-2.5 py-0.5 rounded-full text-sm font-bold tabular-nums"
        style={leader === side ? { background: colours[side], color: "#0b0f1a" } : undefined}
      >
        {v.value === null ? "—" : format(v.value)}
      </span>
      {v.note && <span className="text-[10px] text-pitch-muted mt-0.5">{v.note}</span>}
    </span>
  );
  return (
    <div className="py-2.5" data-stat={label}>
      <div className="flex items-start justify-between gap-3">
        {pill(a, 0)}
        <span className="text-sm text-pitch-muted text-center flex items-center gap-1.5 pt-0.5">
          {label}
          {hint && (
            <span title={hint} className="cursor-help opacity-70">
              <Info size={13} />
            </span>
          )}
        </span>
        {pill(b, 1)}
      </div>
      {known && total > 0 && (
        <div className="flex gap-1.5 mt-1.5 h-1.5">
          <div className="flex-1 flex justify-end rounded-full bg-white/5 overflow-hidden">
            <motion.div className="h-full rounded-full" style={{ background: colours[0] }} initial={{ width: 0 }} whileInView={{ width: `${share(a.value) * 100}%` }} viewport={{ once: true }} transition={{ duration: 0.8 }} />
          </div>
          <div className="flex-1 rounded-full bg-white/5 overflow-hidden">
            <motion.div className="h-full rounded-full" style={{ background: colours[1] }} initial={{ width: 0 }} whileInView={{ width: `${share(b.value) * 100}%` }} viewport={{ once: true }} transition={{ duration: 0.8 }} />
          </div>
        </div>
      )}
    </div>
  );
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="text-[11px] uppercase tracking-[0.18em] text-pitch-muted mt-4 mb-1">{title}</h3>
      <div className="divide-y divide-white/5">{children}</div>
    </div>
  );
}

export function MatchReport({
  analysis,
  names,
  colours,
  duration,
  start,
  onSeek,
  onCalibrate,
  onReview,
}: {
  analysis: Analysis;
  names: string[];
  colours: [string, string];
  duration: number;
  start: number;
  onSeek: (t: number) => void;
  onCalibrate: () => void;
  onReview: () => void;
}) {
  const [heatTeam, setHeatTeam] = useState<0 | 1>(0);
  const [A, B] = analysis.stats.teams as [TeamStats, TeamStats];
  const cov = analysis.stats.coverage;
  // Reviewer-confirmed goals count with or without a pitch setup.
  const goalsKnown = A.goals !== null && B.goals !== null;
  const candidates = (A.goals?.candidates ?? 0) + (B.goals?.candidates ?? 0);
  const reviewedGoals = (A.goals?.value ?? 0) + (B.goals?.value ?? 0);
  const possessionShown = cov.possessionShown;
  const range = cov.possessionInterval;
  const pct = (v: number) => `${Math.round(v)}%`;
  const events = analysis.events.filter((e) => e.status !== "rejected" && ["shot", "goal", "goal-candidate", "interception", "tackle", "pass"].includes(e.type));
  const keyEvents = events.filter((e) => e.type !== "pass");
  return (
    <div className="space-y-6">
      <motion.section
        initial={{ opacity: 0, y: 14 }}
        animate={{ opacity: 1, y: 0 }}
        className="relative overflow-hidden rounded-3xl border border-white/10"
        style={{ background: `linear-gradient(115deg, ${colours[0]}33 0%, #0e1326 38%, #0e1326 62%, ${colours[1]}33 100%)` }}
      >
        <div className="relative grid grid-cols-[1fr_auto_1fr] items-center gap-4 px-5 py-8 sm:px-10">
          <div className="flex flex-col items-center gap-2">
            <span className="w-12 h-12 rounded-full border-4 border-white/20" style={{ background: colours[0] }} />
            <span className="font-bold text-lg text-center break-words">{names[0]}</span>
          </div>
          <div className="flex flex-col items-center gap-2 text-center">
            <span className="text-[11px] uppercase tracking-[0.2em] text-pitch-muted">Score</span>
            <div className="text-5xl font-black tabular-nums">
              {analysis.enteredScore ? (
                <>
                  <span style={{ color: colours[0] }}>{analysis.enteredScore[0]}</span>
                  <span className="text-pitch-muted text-3xl mx-2">–</span>
                  <span style={{ color: colours[1] }}>{analysis.enteredScore[1]}</span>
                </>
              ) : goalsKnown && reviewedGoals > 0 ? (
                <>
                  <span style={{ color: colours[0] }}>{A.goals?.value}</span>
                  <span className="text-pitch-muted text-3xl mx-2">–</span>
                  <span style={{ color: colours[1] }}>{B.goals?.value}</span>
                </>
              ) : (
                <span className="text-pitch-muted text-3xl">– : –</span>
              )}
            </div>
            <span className="text-xs text-pitch-muted max-w-[16rem]">
              {analysis.enteredScore
                ? `Score entered by you · ${reviewedGoals} of ${analysis.enteredScore[0] + analysis.enteredScore[1]} goals located in the video`
                : reviewedGoals > 0
                ? "Goals confirmed by review"
                : !analysis.calibrated
                  ? "Enter the score, or set up the pitch to find shots and possible goals"
                  : candidates
                    ? `${candidates} possible goal${candidates === 1 ? "" : "s"} to confirm`
                    : "No goal confirmed yet. Add goals you saw in the review."}
            </span>
            <button className="text-xs underline text-pitch-green" onClick={onReview}>
              {analysis.enteredScore ? "Review moments" : "Enter the score and review moments"} ({analysis.review.pending} waiting)
            </button>
          </div>
          <div className="flex flex-col items-center gap-2">
            <span className="w-12 h-12 rounded-full border-4 border-white/20" style={{ background: colours[1] }} />
            <span className="font-bold text-lg text-center break-words">{names[1]}</span>
          </div>
        </div>
        <p className="relative border-t border-white/10 px-5 py-2.5 text-xs text-pitch-muted flex flex-wrap gap-x-4 gap-y-1 justify-center">
          <span>Ball state known {cov.ballStatePercent}% of the time</span>
          <span>Possession followed {cov.possessionPercent}% of play</span>
          {cov.deadBallSeconds > 0 && <span>Ball out of play {clockTime(cov.deadBallSeconds)}</span>}
          <span>Pitch mapped {cov.calibratedPercent}%</span>
          <span>{analysis.stats.tracks.players} player tracks</span>
        </p>
      </motion.section>

      {!analysis.calibrated && (
        <section className="rounded-2xl border border-pitch-green/40 bg-pitch-green/5 p-5 flex flex-wrap gap-4 items-center justify-between">
          <div>
            <p className="font-semibold">Unlock shots, heatmaps and positions</p>
            <p className="text-sm text-pitch-muted">Click a few landmarks on one frame so Pitchlens knows where the pitch is. It takes about two minutes.</p>
          </div>
          <button className="pitch-button-primary" onClick={onCalibrate}>
            <Crosshair size={16} /> Set up the pitch
          </button>
        </section>
      )}

      <div className="grid lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)] gap-6">
        <section className={card}>
          <div className="flex items-center justify-between">
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
          <Group title="Possession">
            <Row
              label="Ball possession (time)"
              a={{ value: possessionShown ? A.possession : null, note: possessionShown && range ? `95%: ${Math.round(range[0])}–${Math.round(range[1])}%` : undefined }}
              b={{ value: possessionShown ? B.possession : null }}
              colours={colours}
              format={pct}
              hint={
                possessionShown
                  ? `From winning the ball to losing it, passes in flight included, over the ${cov.possessionPercent}% of in-play time the ball could be followed.`
                  : `Withheld: the ball could only be followed for ${cov.possessionPercent}% of in-play time (60% needed for a fair split).`
              }
            />
            <Row
              label="Possession by passes"
              a={{ value: A.passShare ?? null }}
              b={{ value: B.passShare ?? null }}
              colours={colours}
              format={pct}
              hint="Each team's share of all detected passes. Many stats sites define possession this way; it can differ from the time-based figure above."
            />
            <Row label="Possessions" a={{ value: A.possessions }} b={{ value: B.possessions }} colours={colours} />
            <Row label="Average possession" a={{ value: A.averagePossession }} b={{ value: B.averagePossession }} colours={colours} format={(v) => `${v.toFixed(1)}s`} />
            <Row
              label="Field tilt"
              a={{ value: A.fieldTilt }}
              b={{ value: B.fieldTilt }}
              colours={colours}
              format={pct}
              hint="Share of all ball control in an attacking third that was this team's: who pinned whom back."
            />
          </Group>
          <Group title="Passing">
            <Row label="Passes" a={count(A.passes)} b={count(B.passes)} colours={colours} hint="Ball moved from one player to another (to a teammate or intercepted). Unseen transfers get lower confidence." />
            <Row label="Accurate passes" a={count(A.passesComplete)} b={count(B.passesComplete)} colours={colours} />
            <Row
              label="Pass accuracy"
              a={{ value: A.passAccuracy, note: A.passAccuracy === null && A.passes.value ? "needs 20+ passes" : undefined }}
              b={{ value: B.passAccuracy, note: B.passAccuracy === null && B.passes.value ? "needs 20+ passes" : undefined }}
              colours={colours}
              format={pct}
            />
          </Group>
          <Group title="Attacking">
            {analysis.calibrated && cov.calibratedPercent < 60 && (
              <p className="text-[11px] text-amber-200/90 pt-1">
                The pitch is mapped for {cov.calibratedPercent}% of the match, so shots cover that part only.
              </p>
            )}
            <Row label="Shots" a={count(A.shots)} b={count(B.shots)} colours={colours} hint="Fast ball released towards the goal the team attacks. Needs the pitch set up." />
            <Row
              label="Shots on target"
              a={count(A.shotsOnTarget)}
              b={count(B.shotsOnTarget)}
              colours={colours}
              hint="Counted when the outcome shows it: a goal, or the keeper collecting a ball heading between the posts. Ball height cannot be seen from one camera."
            />
            <Row
              label="Possible goals"
              a={{ value: A.goals ? A.goals.candidates : null }}
              b={{ value: B.goals ? B.goals.candidates : null }}
              colours={colours}
              hint="The ball appeared to cross the goal line between the posts. A person must confirm each goal."
            />
          </Group>
          <Group title="Defending">
            <Row label="Interceptions" a={count(A.interceptions)} b={count(B.interceptions)} colours={colours} />
            <Row label="Tackles / balls won" a={count(A.tackles)} b={count(B.tackles)} colours={colours} />
          </Group>
          <p className="text-xs text-pitch-muted mt-4">
            — means not measured (not zero). Counts cover the parts of the match where the ball could be followed; &quot;to review&quot; items are automatic and unconfirmed.
          </p>
        </section>

        <div className="space-y-6">
          {analysis.calibrated && (
            <section className={card}>
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-xs uppercase tracking-widest text-pitch-muted flex items-center gap-2">
                  <Target size={14} /> Shot map
                </h2>
                <span className="text-[11px] text-pitch-muted">
                  {names[0]} → · ← {names[1]}
                </span>
              </div>
              {analysis.stats.shotMap?.length ? (
                <ShotMap analysis={analysis} colours={colours} names={names} onSeek={onSeek} />
              ) : (
                <p className="text-sm text-pitch-muted py-6 text-center">No shots detected. Add any you saw in the review.</p>
              )}
            </section>
          )}
          <section className={card}>
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-xs uppercase tracking-widest text-pitch-muted">Attack momentum</h2>
              <span className="text-[11px] text-pitch-muted">Per minute · control near the opponent&apos;s goal counts most</span>
            </div>
            <svg viewBox={`0 0 ${analysis.stats.momentum.length * 10} 80`} className="w-full h-28" preserveAspectRatio="none">
              <line x1="0" y1="40" x2={analysis.stats.momentum.length * 10} y2="40" stroke="rgba(255,255,255,0.15)" strokeWidth="0.6" />
              {analysis.stats.momentum.map((v, i) => {
                // Scale to the largest swing so a quiet match still reads.
                const peak = Math.max(0.05, ...analysis.stats.momentum.map((m) => Math.abs(m ?? 0)));
                const h = v === null ? 0 : Math.max(1.5, (Math.abs(v) / peak) * 36);
                const up = (v ?? 0) >= 0;
                return (
                  <g key={i} onClick={() => onSeek(start + i * 60)} className="cursor-pointer">
                    <rect x={i * 10} y="0" width="10" height="80" fill="transparent" />
                    {v === null ? (
                      <rect x={i * 10 + 2} y="39" width="6" height="2" fill="rgba(255,255,255,0.15)" />
                    ) : (
                      <motion.rect x={i * 10 + 1.5} width="7" rx="1.5" fill={up ? colours[0] : colours[1]} initial={{ height: 0, y: 40 }} whileInView={{ height: h, y: up ? 40 - h : 40 }} viewport={{ once: true }} transition={{ delay: i * 0.01 }} />
                    )}
                  </g>
                );
              })}
            </svg>
          </section>
          {analysis.calibrated && analysis.stats.heatmaps && analysis.template && (
            <section className={card}>
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-xs uppercase tracking-widest text-pitch-muted">Heatmap</h2>
                <div className="flex gap-1 text-xs" role="tablist" aria-label="Heatmap team">
                  {([0, 1] as const).map((t) => (
                    <button key={t} role="tab" aria-selected={heatTeam === t} onClick={() => setHeatTeam(t)} className={`px-2.5 py-1 rounded-full ${heatTeam === t ? "text-[#0b0f1a] font-bold" : "text-pitch-muted"}`} style={heatTeam === t ? { background: colours[t] } : undefined}>
                      {names[t]}
                    </button>
                  ))}
                </div>
              </div>
              <Heatmap template={analysis.template} grid={analysis.stats.heatmaps[String(heatTeam)]} colour={colours[heatTeam]} label={`${names[heatTeam]} heatmap (attacking right)`} />
              <p className="text-[11px] text-pitch-muted mt-2 text-center">Attacking to the right · positions from the pitch-mapped part of the match</p>
            </section>
          )}
        </div>
      </div>

      {analysis.calibrated && analysis.stats.averagePositions?.length ? (
        <section className={card}>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-xs uppercase tracking-widest text-pitch-muted">Average positions</h2>
            <span className="text-[11px] text-pitch-muted">Numbers are track IDs, not shirt numbers · {names[0]} → · ← {names[1]}</span>
          </div>
          <AveragePositions analysis={analysis} colours={colours} />
        </section>
      ) : null}

      <section className={card}>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-xs uppercase tracking-widest text-pitch-muted">Match timeline</h2>
          <span className="text-xs text-pitch-muted">{keyEvents.length} key moments · click to watch</span>
        </div>
        <div className="relative h-14">
          <div className="absolute left-0 right-0 top-1/2 h-[3px] -translate-y-1/2 rounded-full bg-white/10" />
          {keyEvents.map((e) => (
            <button
              key={e.id}
              onClick={() => onSeek(Math.max(0, e.t - 2))}
              title={`${clockTime(e.t)} · ${e.team !== null ? names[e.team] : ""} · ${describeEvent(e)}${e.status === "confirmed" ? " · confirmed" : " · to review"}`}
              className="absolute -translate-x-1/2 w-5 h-5 rounded-full ring-2 ring-[#11162a]"
              style={{
                left: `${Math.min(100, Math.max(0, ((e.t - start) / Math.max(1, duration)) * 100))}%`,
                top: e.team === 0 ? 0 : "auto",
                bottom: e.team === 0 ? "auto" : 0,
                background: e.team !== null ? colours[e.team] : "#94a3b8",
                opacity: e.status === "confirmed" ? 1 : 0.55,
                outline: e.type === "goal" || e.type === "goal-candidate" ? "2px solid #facc15" : undefined,
              }}
            />
          ))}
        </div>
        <div className="flex justify-between text-[11px] text-pitch-muted mt-2">
          <span>{clockTime(start)}</span>
          <span>{clockTime(start + duration)}</span>
        </div>
      </section>
    </div>
  );
}
