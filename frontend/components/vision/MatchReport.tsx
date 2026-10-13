"use client";
import { useState } from "react";
import { motion } from "framer-motion";
import { Info, Target, Crosshair } from "lucide-react";
import type { Analysis, CountStat, TeamStats } from "@/lib/review/analysis";
import { describeEvent } from "@/lib/review/analysis";
import { clockTime, type VisionResult } from "@/lib/review/vision";
import { AveragePositions, Heatmap, ShotMap } from "@/components/vision/PitchGraphics";

const card = "rounded-2xl border border-white/10 bg-[#11162a]/80 backdrop-blur p-5";

type Value = { value: number | null; note?: string; approx?: boolean };

/** Minimum checked detections of a type before the rest are estimated (the worker's default). */
const MIN_CHECKED = 10;
/** Share of in-play time the ball must be followed before possession is shown (the worker's default). */
const POSSESSION_MIN_COVERAGE = 0.6;
/** Observed control in a minute before its momentum bar is drawn (the worker's default). */
const MOMENTUM_MIN_CONTROL = 5;
const DISTANCE_HINT =
  "How far the feet moved after the camera pan is removed, scaled by a 1.8 m player. Steps under 3 pixels, boxes far from that track's usual height, and anything over 11 m/s are dropped. Tracks are not named players. A squad 15 cm off 1.8 m moves this by about 8%.";

/** Metres per kit, or "—" when the engine did not write a usable number (no step was kept: unmeasured, not 0 m). */
function metres(load: PlayerLoad | undefined, team: 0 | 1): Value {
  const v = load?.metres?.[team];
  return { value: typeof v === "number" && Number.isFinite(v) && load?.steps !== 0 ? v : null };
}

type PlayerLoad = NonNullable<VisionResult["metrics"]["playerLoad"]>;

/** Types estimated from a random sample (checks are pooled across both teams). */
const SAMPLED: { type: string; key: "passes" | "interceptions" | "tackles"; one: string; many: string }[] = [
  { type: "pass", key: "passes", one: "pass", many: "passes" },
  { type: "interception", key: "interceptions", one: "interception", many: "interceptions" },
  { type: "tackle", key: "tackles", one: "tackle", many: "tackles" },
];

function count(c: CountStat | null | undefined): Value {
  if (!c) return { value: null };
  if (!c.pending) return { value: c.confirmed > 0 ? c.confirmed : null, note: c.confirmed ? "confirmed in reviewed clips" : "none confirmed" };
  if (c.estimate !== undefined && c.estimateRange) {
    // Unchecked detections counted at the rate the reviewer found them right.
    return { value: c.estimate, approx: true, note: `${c.estimateRange[0]}–${c.estimateRange[1]} · ${c.confirmed} confirmed` };
  }
  // How many more checks an estimate needs is a per-type figure (shown once, in the score panel).
  return { value: c.confirmed > 0 ? c.confirmed : null, note: `${c.confirmed} confirmed · ${c.pending} to review` };
}

function Row({
  label,
  a,
  b,
  colours,
  format = (v) => String(v),
  hint,
  note,
}: {
  label: string;
  a: Value;
  b: Value;
  colours: [string, string];
  format?: (v: number) => string;
  hint?: string;
  /** Always shown under the row (touch screens cannot hover for the hint). */
  note?: string;
}) {
  const [open, setOpen] = useState(false);
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
        {v.value === null ? "—" : `${v.approx ? "≈" : ""}${format(v.value)}`}
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
            // A button, not a hover tooltip: phones and keyboards can open it too.
            <button
              type="button"
              title={hint}
              aria-label={`Why: ${label}`}
              aria-expanded={open}
              onClick={() => setOpen((v) => !v)}
              className="p-2 -m-2 rounded-full opacity-70 hover:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-pitch-green"
            >
              <Info size={13} />
            </button>
          )}
        </span>
        {pill(b, 1)}
      </div>
      {note && <p className="text-[11px] text-amber-200/90 text-center mt-1">{note}</p>}
      {open && hint && <p className="text-[11px] text-pitch-muted text-center mt-1.5">{hint}</p>}
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
  videoAvailable = true,
  playerLoad,
}: {
  analysis: Analysis;
  names: string[];
  colours: [string, string];
  duration: number;
  start: number;
  onSeek: (t: number) => void;
  onCalibrate: () => void;
  onReview: () => void;
  /** False once the footage is deleted: pitch setup is clicked on the video. */
  videoAvailable?: boolean;
  /** Per-kit running distance from the engine (absent on results before 2.6). */
  playerLoad?: PlayerLoad;
}) {
  const [heatTeam, setHeatTeam] = useState<0 | 1>(0);
  const [A, B] = analysis.stats.teams as [TeamStats, TeamStats];
  const cov = analysis.stats.coverage;
  const minChecked = analysis.params?.minCheckedForEstimate ?? MIN_CHECKED;
  const minCoverage = (analysis.params?.possessionMinCoverage ?? POSSESSION_MIN_COVERAGE) * 100;
  const minMomentum = analysis.params?.minMomentumControlSeconds ?? MOMENTUM_MIN_CONTROL;
  // Field position weights momentum only with a pitch setup and known attacking directions.
  const momentumWeighted = analysis.calibrated && !!analysis.directions?.segments?.length;
  const noFootage = "Pitch setup needs the footage, which was deleted after the retention period.";
  // Reviewer-confirmed goals count with or without a pitch setup.
  const goalsKnown = A.goals !== null && B.goals !== null;
  const candidates = (A.goals?.candidates ?? 0) + (B.goals?.candidates ?? 0);
  const reviewedGoals = (A.goals?.value ?? 0) + (B.goals?.value ?? 0);
  const possessionShown = cov.possessionShown;
  const range = cov.possessionMissingBounds;
  const pct = (v: number) => `${Math.round(v)}%`;
  const events = analysis.events.filter((e) => e.status !== "rejected" && ["shot", "goal", "goal-candidate", "interception", "tackle", "pass"].includes(e.type));
  const keyEvents = events.filter((e) => e.type !== "pass");
  return (
    <div className="space-y-6">
      <section className="rounded-2xl border border-amber-300/20 bg-amber-300/5 p-4 text-sm" role="status">
        <strong>Assisted review</strong> · Automatic moments are candidates until checked. Check a random sample of
        passes (and each shot) and the counts become estimates marked ≈, with a range, scaled by how often the
        detections you checked were right. Moments the system never found are not included; add them in the review.
      </section>
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
              {analysis.enteredScore ? "Review" : "Enter the score and review"}
              {analysis.review.pendingKey !== undefined
                ? ` · ${analysis.review.pendingKey} ${analysis.review.pendingKey === 1 ? "shot or goal" : "shots and goals"} to check`
                : ` · ${analysis.review.pending} waiting`}
            </button>
            {(() => {
              // What each sampled type still needs, once per type (checks are pooled across teams).
              const by = analysis.review.pendingByType ?? {};
              const advice = SAMPLED.map(({ type, key, one, many }) => {
                const n = by[type] ?? 0;
                if (!n) return "";
                const checked = A[key]?.checked ?? 0;
                const found = `${n} ${n === 1 ? one : many} found`;
                // Too few to sample: checking them all gives the exact count.
                if (n <= minChecked) return `${found}: check ${n === 1 ? "it" : `all ${n}`} for the exact count`;
                if (checked >= minChecked) return `${found}: enough checked to estimate the rest`;
                return `${found}: check ${checked ? `${minChecked - checked} more` : minChecked} at random to estimate the total, no need to go through them all`;
              }).filter(Boolean);
              return advice.length ? (
                <span className="text-[11px] text-pitch-muted max-w-[18rem]" data-testid="sample-advice">
                  {advice.join(". ")}.
                </span>
              ) : null;
            })()}
          </div>
          <div className="flex flex-col items-center gap-2">
            <span className="w-12 h-12 rounded-full border-4 border-white/20" style={{ background: colours[1] }} />
            <span className="font-bold text-lg text-center break-words">{names[1]}</span>
          </div>
        </div>
        <p className="relative border-t border-white/10 px-5 py-2.5 text-xs text-pitch-muted flex flex-wrap gap-x-4 gap-y-1 justify-center">
          <span title="Retained detector and inferred positions; this is coverage, not verified accuracy.">Ball position coverage {cov.ballStatePercent}%</span>
          <span>Possession followed {cov.possessionPercent}% of play</span>
          {cov.deadBallSeconds > 0 && <span>Ball out of play {clockTime(cov.deadBallSeconds)}</span>}
          {cov.calibratedPercent > 0 ? (
            <span>Pitch mapped {cov.calibratedPercent}%</span>
          ) : videoAvailable ? (
            <button className="underline text-pitch-green" onClick={onCalibrate}>
              Pitch not set up · set it up
            </button>
          ) : (
            <span>Pitch not set up · {noFootage}</span>
          )}
          <span title="Pieces of player tracks after joining; one player usually appears as several.">{analysis.stats.tracks.players} track segments</span>
        </p>
      </motion.section>

      {!analysis.calibrated && (
        <section className="rounded-2xl border border-pitch-green/40 bg-pitch-green/5 p-5 flex flex-wrap gap-4 items-center justify-between">
          <div>
            <p className="font-semibold">Unlock shots, heatmaps and positions</p>
            <p className="text-sm text-pitch-muted">
              {videoAvailable
                ? "Click a few landmarks on one frame so Pitchlens knows where the pitch is. It takes about two minutes."
                : noFootage}
            </p>
          </div>
          {videoAvailable && (
            <button className="pitch-button-primary" onClick={onCalibrate}>
              <Crosshair size={16} /> Set up the pitch
            </button>
          )}
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
          {playerLoad && (
            <Group title="Player load">
              {/* Per kit, never speed: one estimate per team, not named players. */}
              <Row
                label="Distance"
                a={metres(playerLoad, 0)}
                b={metres(playerLoad, 1)}
                colours={colours}
                format={(v) => `${v.toFixed(1)} m`}
                hint={DISTANCE_HINT}
                note="estimate · camera pan removed · not named players"
              />
            </Group>
          )}
          <Group title="Possession">
            <Row
              label="Observed control share"
              a={{ value: possessionShown ? A.possession : null, note: possessionShown && range ? `unseen-play bounds: ${Math.round(range[0])}–${Math.round(range[1])}%` : undefined }}
              b={{ value: possessionShown ? B.possession : null }}
              colours={colours}
              format={pct}
              hint={
                possessionShown
                  ? `From winning the ball to losing it, passes in flight included, over the ${cov.possessionPercent}% of in-play time the ball could be followed. Bounds assign all missing play to either team; detector errors are not included.`
                  : `Withheld: the ball could only be followed for ${cov.possessionPercent}% of in-play time (${pct(minCoverage)} needed to display this estimate).`
              }
              // Why it is withheld, without a tap (rounded down so 59.6% never reads as enough).
              note={
                possessionShown
                  ? undefined
                  : cov.possessionPercent < minCoverage
                    ? `Followed ${Math.floor(cov.possessionPercent)}% of play · ${pct(minCoverage)} needed`
                    : "No team possession was established"
              }
            />
            <Row
              label="Candidate pass share"
              a={{ value: A.passShare ?? null }}
              b={{ value: B.passShare ?? null }}
              colours={colours}
              format={pct}
              hint="Each team's share of all detected passes. Many stats sites define possession this way; it can differ from the time-based figure above."
            />
            {/* Counted over the same followed play as the share: withheld with it (older workers sent a 0). */}
            <Row label="Possessions" a={{ value: possessionShown ? A.possessions : null }} b={{ value: possessionShown ? B.possessions : null }} colours={colours} />
            <Row label="Average possession" a={{ value: possessionShown ? A.averagePossession : null }} b={{ value: possessionShown ? B.averagePossession : null }} colours={colours} format={(v) => `${v.toFixed(1)}s`} />
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
              label="Reviewed pass accuracy"
              a={{ value: A.passAccuracy, note: A.passAccuracy === null && A.passes?.value ? "needs 20+ reviewed passes" : undefined }}
              b={{ value: B.passAccuracy, note: B.passAccuracy === null && B.passes?.value ? "needs 20+ reviewed passes" : undefined }}
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
            <Row label="Shots" a={count(A.shots)} b={count(B.shots)} colours={colours} hint="Fast ball released towards the goal the team attacks. Needs the pitch set up, or add the shots you saw in review." />
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
            — means not measured or nothing confirmed yet, not zero. ≈ marks an estimate from the moments you checked:
            confirmed events plus unchecked detections at the rate you found them right, with a 95% range. Moments the
            system missed are not counted until you add them.
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
              ) : A.shots === null ? (
                <p className="text-sm text-pitch-muted py-6 text-center">
                  Shots need to know which way each team attacks. Set the direction in the review, or add shots you saw.
                </p>
              ) : (
                <p className="text-sm text-pitch-muted py-6 text-center">No shots detected. Add any you saw in the review.</p>
              )}
            </section>
          )}
          <section className={card}>
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-xs uppercase tracking-widest text-pitch-muted">Attack momentum</h2>
              <span className="text-[11px] text-pitch-muted text-right" data-testid="momentum-caption">
                {momentumWeighted
                  ? "Per minute · control near the opponent's goal counts most"
                  : analysis.calibrated
                    ? "Per minute · observed ball-control difference (attacking direction unknown, so field position is not weighted)"
                    : "Per minute · observed ball-control difference (pitch not set up, so field position is not weighted)"}
              </span>
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
            {analysis.stats.momentum.some((v) => v === null) && (
              <p className="text-[11px] text-pitch-muted mt-1">A dash marks a minute with under {minMomentum} s of observed ball control.</p>
            )}
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
          <span className="text-xs text-pitch-muted">{keyEvents.length} moment{keyEvents.length === 1 ? "" : "s"} on the timeline · click to watch</span>
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
