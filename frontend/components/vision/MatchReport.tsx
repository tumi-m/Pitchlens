"use client";
import { useState } from "react";
import { motion } from "framer-motion";
import { Info, Target, Crosshair } from "lucide-react";
import type { Analysis, AnalysisEvent, CountStat, TeamStats } from "@/lib/review/analysis";
import { describeEvent, isConfirmedGoal, sampleNeeds, MIN_CHECKED } from "@/lib/review/analysis";
import { clockTime } from "@/lib/review/vision";
import type { VisionResult } from "@/lib/review/vision";
import { AveragePositions, Heatmap, ShotMap } from "@/components/vision/PitchGraphics";
import { DISTANCE_HINT } from "@/components/vision/MatchCentre";
import { shares } from "@/lib/review/visionStats";

const card = "rounded-2xl border border-white/10 bg-[#11162a]/80 backdrop-blur p-5";

/** partial: still being reviewed with no estimate, so the value is a lower bound ("3+"). */
type Value = { value: number | null; note?: string; approx?: boolean; partial?: boolean };
/** Share of in-play time the ball must be followed before possession is shown (the worker's default). */
const POSSESSION_MIN_COVERAGE = 0.6;
/** Observed control in a minute before its momentum bar is drawn (the worker's default). */
const MOMENTUM_MIN_CONTROL = 5;
/** Metres per kit, or "—" when the engine did not write a usable number (no step was kept: unmeasured, not 0 m). */
function metres(load: PlayerLoad | undefined, team: 0 | 1): Value {
  const v = load?.metres?.[team];
  return { value: typeof v === "number" && Number.isFinite(v) && load?.steps !== 0 ? v : null };
}

type PlayerLoad = NonNullable<VisionResult["metrics"]["playerLoad"]>;

/**
 * A counted stat. `sampled` marks the types estimated from a random sample (passes,
 * accurate passes, won balls, tackles): their unchecked moments are never gone
 * through one by one, so the note asks for a sample, not for "N to review".
 */
function count(c: CountStat | null | undefined, sampled?: { pooled: number; min: number }): Value {
  if (!c) return { value: null };
  if (!c.pending) return { value: c.confirmed > 0 ? c.confirmed : null, note: c.confirmed ? "confirmed in reviewed clips" : "none confirmed" };
  if (c.estimate !== undefined && c.estimateRange) {
    // Unchecked detections counted at the rate the reviewer found them right.
    return { value: c.estimate, approx: true, note: `${c.estimateRange[0]}–${c.estimateRange[1]} · ${c.confirmed} confirmed` };
  }
  // Moments still unchecked and no estimate yet: what is confirmed is a lower bound
  // ("3+"), not a total to set against the other team or an estimate.
  const next = sampled
    ? sampled.pooled <= sampled.min
      ? "check them all for the exact count"
      : `check ${sampled.min} at random to estimate`
    : `${c.pending} to review`;
  return { value: c.confirmed > 0 ? c.confirmed : null, partial: true, note: `${c.confirmed} confirmed so far · ${next}` };
}

/** Possible goals still to check; null when they were not looked for (no pitch setup). */
function possibleGoals(team: TeamStats, open: number): Value {
  if (!team.goals || team.goals.candidates === null || team.goals.candidates === undefined) return { value: null };
  return { value: open, note: team.goals.value ? `${team.goals.value} confirmed` : undefined };
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
  // A partial count is a lower bound: no leader and no bar against the other side
  // (an exact count next to an estimate stays comparable).
  const known = a.value !== null && b.value !== null && !a.partial && !b.partial;
  const total = known ? (a.value as number) + (b.value as number) : 0;
  const share = (v: number | null) => (known && total > 0 ? (v as number) / total : 0);
  const leader = known && a.value !== b.value ? ((a.value as number) > (b.value as number) ? 0 : 1) : null;
  const pill = (v: Value, side: 0 | 1) => (
    <span className="flex flex-col items-center min-w-[4.5rem]">
      <span
        className="px-2.5 py-0.5 rounded-full text-sm font-bold tabular-nums"
        style={leader === side ? { background: colours[side], color: "#0b0f1a" } : undefined}
      >
        {v.value === null ? "—" : `${v.approx ? "≈" : ""}${format(v.value)}${v.partial ? "+" : ""}`}
      </span>
      {v.note && <span className="text-xs text-pitch-muted mt-0.5 text-center">{v.note}</span>}
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
              // 44 px to tap; the negative margin keeps the row as compact as the 13 px icon.
              className="inline-flex items-center justify-center min-h-11 min-w-11 -my-3 -mr-3 -ml-2 rounded-full opacity-70 hover:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-pitch-green"
            >
              <Info size={13} />
            </button>
          )}
        </span>
        {pill(b, 1)}
      </div>
      {note && <p className="text-xs text-amber-200/90 text-center mt-1">{note}</p>}
      {open && hint && <p className="text-xs text-pitch-muted text-center mt-1.5">{hint}</p>}
      {known && total > 0 && (
        <div className="flex gap-1.5 mt-1.5 h-1.5" data-testid="stat-bar">
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

/** Why shots were not counted (stats.coverage.shotsBasis), in words; null when they were. */
function shotsMissing(basis: Analysis["stats"]["coverage"]["shotsBasis"], ballTracked: boolean): string | null {
  if (basis === "no-ball")
    return ballTracked
      ? "The ball was not found in this footage, so shots were not looked for; add any you saw in the review."
      : "Shots are not looked for in a player-load job; add any you saw in the review.";
  if (basis === "no-direction") return "Shots need to know which way each team attacks. Set the direction in the review, or add shots you saw.";
  if (basis === "no-pitch") return "Set up the pitch to find shots, or add shots you saw in the review.";
  return null;
}

/** Possible goals per team still waiting for a decision (one already confirmed
 *  within 3 s for the same team is the same goal). */
function pendingGoals(events: AnalysisEvent[]): [number, number] {
  const confirmed = events.filter(isConfirmedGoal);
  const open = (team: number) =>
    events.filter(
      (e) =>
        e.type === "goal-candidate" &&
        e.status === "proposed" &&
        e.team === team &&
        !confirmed.some((g) => g.team === team && Math.abs(g.t - e.t) <= 3),
    ).length;
  return [open(0), open(1)];
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
  ballTracked = true,
  afterHero,
}: {
  analysis: Analysis;
  names: string[];
  colours: [string, string];
  duration: number;
  start: number;
  onSeek: (t: number) => void;
  onCalibrate: () => void;
  /** Open the review: at the score box, on the random sample, or on the key moments. */
  onReview: (target?: "score" | "sample" | "key") => void;
  /** False once the footage is deleted: pitch setup is clicked on the video. */
  videoAvailable?: boolean;
  /** Per-kit running distance, measured for every job with or without the ball model (absent on results before 2.6). */
  playerLoad?: PlayerLoad;
  /** False for a player-load job: the ball model was not run, so nothing about the ball was measured. */
  ballTracked?: boolean;
  /** Shown directly under the score card (the next steps). */
  afterHero?: React.ReactNode;
}) {
  const [heatTeam, setHeatTeam] = useState<0 | 1>(0);
  // The timeline shows key moments; unreviewed won balls (often over a hundred) on request.
  const [allMoments, setAllMoments] = useState(false);
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
  const reviewedGoals = (A.goals?.value ?? 0) + (B.goals?.value ?? 0);
  // A team's 0 is only a measured 0 once its possible goals have been checked.
  const pending = pendingGoals(analysis.events);
  const pendingTotal = pending[0] + pending[1];
  // Unmeasured kits (no usable step) are null, shown as "—", never 0 m.
  const kitMetres = [metres(playerLoad, 0).value, metres(playerLoad, 1).value];
  const metre = (v: number | null) => (v === null ? "—" : v.toFixed(1));
  const possessionShown = cov.possessionShown;
  const range = cov.possessionMissingBounds;
  const pct = (v: number) => `${Math.round(v)}%`;
  // Two-sided shares as whole percents that add up to 100.
  const [possA, possB] = shares(possessionShown ? A.possession : null, possessionShown ? B.possession : null);
  const [passShareA, passShareB] = shares(A.passShare ?? null, B.passShare ?? null);
  const [tiltA, tiltB] = shares(A.fieldTilt, B.fieldTilt);
  const needs = sampleNeeds(analysis);
  // Unchecked moments of a type over both teams (older workers do not send pendingByType).
  const pooled = (type: string, key: "passes" | "interceptions" | "tackles") => ({
    pooled: analysis.review.pendingByType?.[type] ?? (A[key]?.pending ?? 0) + (B[key]?.pending ?? 0),
    min: minChecked,
  });
  const events = analysis.events.filter((e) => e.status !== "rejected" && ["shot", "goal", "goal-candidate", "interception", "tackle", "pass"].includes(e.type));
  const wonBall = (e: AnalysisEvent) => e.type === "interception" || e.type === "tackle";
  // Goals, possible goals, shots and confirmed won balls; unreviewed won balls only on request.
  const keyEvents = events.filter((e) => e.type !== "pass" && (allMoments || !wonBall(e) || e.status === "confirmed"));
  const hiddenWon = events.filter((e) => wonBall(e) && e.status !== "confirmed").length;
  const scoreShown = !!analysis.enteredScore || (goalsKnown && reviewedGoals > 0);
  // Older analyses have no shotsBasis: unmeasured shots there meant no direction.
  const shotsNote =
    cov.shotsBasis !== undefined
      ? shotsMissing(cov.shotsBasis, ballTracked)
      : A.shots === null
        ? shotsMissing(analysis.calibrated ? "no-direction" : "no-pitch", ballTracked)
        : null;
  // Player-load job without a score: what was measured (distance) leads instead of "– : –".
  const distanceHero = !ballTracked && !scoreShown && kitMetres.some((v) => v !== null);
  // Accurate passes are estimated from checked passes the detector called complete:
  // enough checked passes do not yet mean enough of those.
  const accurateOpen = [A, B].some((t) => t.passesComplete && t.passesComplete.pending > 0 && t.passesComplete.estimate === undefined);
  const accurateChecked = A.passesComplete?.checked ?? 0;
  const advice = needs
    .map((t) => {
      const n = t.pending;
      if (!n) return "";
      const found = `${n} ${n === 1 ? t.one : t.many} found`;
      // Too few to sample: checking them all gives the exact count.
      if (t.few) return `${found}: check ${n === 1 ? "it" : `all ${n}`} for the exact count`;
      const accurate = t.type === "pass" && accurateOpen ? `accurate passes ${Math.min(accurateChecked, t.min)}/${t.min}` : "";
      if (t.checked >= t.min)
        return accurate
          ? `${found}: ${accurate}. Accurate passes need ${t.min} checked passes the detector called complete (${accurateChecked} so far): check a few more random passes`
          : `${found}: enough checked to estimate the rest`;
      return `${found}: check ${t.checked ? `${t.min - t.checked} more` : t.min} at random to estimate the total, no need to go through them all${accurate ? ` (${accurate})` : ""}`;
    })
    .filter(Boolean);
  return (
    <div className="space-y-6">
      <motion.section
        initial={{ opacity: 0, y: 14 }}
        animate={{ opacity: 1, y: 0 }}
        className="relative overflow-hidden rounded-3xl border border-white/10"
        style={{ background: `linear-gradient(115deg, ${colours[0]}33 0%, #0e1326 38%, #0e1326 62%, ${colours[1]}33 100%)` }}
        data-testid="score-card"
      >
        {/* On a phone the centre holds only the score, so team names get room; the
            score note, the review action and the sample advice take a row of their own. */}
        <div className="relative grid grid-cols-[minmax(0,1fr)_minmax(0,6rem)_minmax(0,1fr)] sm:grid-cols-[1fr_auto_1fr] items-center gap-x-1 gap-y-4 px-2 py-6 sm:gap-x-4 sm:px-10 sm:py-8">
          <div className="flex flex-col items-center gap-2 min-w-0">
            <span className="w-10 h-10 sm:w-12 sm:h-12 rounded-full border-4 border-white/20" style={{ background: colours[0] }} />
            <span className="font-bold text-sm sm:text-lg leading-snug text-center break-words max-w-full" data-testid="hero-name">{names[0]}</span>
          </div>
          <div className="flex flex-col items-center gap-2 text-center min-w-0">
            <span className="text-[11px] uppercase tracking-[0.2em] text-pitch-muted">{distanceHero ? "Distance" : "Score"}</span>
            <div
              className={`${distanceHero ? "text-3xl sm:text-5xl flex flex-wrap justify-center items-baseline" : "text-4xl sm:text-5xl whitespace-nowrap"} font-black tabular-nums`}
              data-testid="report-hero"
            >
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
              ) : distanceHero ? (
                <>
                  <span style={{ color: colours[0] }}>{metre(kitMetres[0])}</span>
                  <span className="text-pitch-muted text-3xl mx-2">–</span>
                  <span style={{ color: colours[1] }}>{metre(kitMetres[1])}</span>
                </>
              ) : (
                <span className="text-pitch-muted text-3xl">– : –</span>
              )}
            </div>
          </div>
          <div className="flex flex-col items-center gap-2 min-w-0">
            <span className="w-10 h-10 sm:w-12 sm:h-12 rounded-full border-4 border-white/20" style={{ background: colours[1] }} />
            <span className="font-bold text-sm sm:text-lg leading-snug text-center break-words max-w-full" data-testid="hero-name">{names[1]}</span>
          </div>
          <div className="col-span-3 flex flex-col items-center gap-2 text-center">
            <span className="text-xs text-pitch-muted max-w-md">
              {analysis.enteredScore
                ? `Score entered by you · ${reviewedGoals} of ${analysis.enteredScore[0] + analysis.enteredScore[1]} goals located in the video`
                : reviewedGoals > 0
                ? pendingTotal
                  ? `${reviewedGoals} confirmed · ${pendingTotal} possible goal${pendingTotal === 1 ? "" : "s"} still to check`
                  : "Goals confirmed by review"
                : distanceHero
                  ? "metres, camera pan removed · not named players · enter the score in the review"
                  : !ballTracked
                    ? "The ball was not tracked: enter the score in the review"
                    : !analysis.calibrated
                      ? "Enter the score, or set up the pitch to find shots and possible goals"
                      : pendingTotal
                        ? `${pendingTotal} possible goal${pendingTotal === 1 ? "" : "s"} to confirm`
                        : "No goal confirmed yet. Add goals you saw in the review."}
            </span>
            <button className="pitch-button-primary min-h-11" onClick={() => onReview("score")}>
              {analysis.enteredScore ? "Review" : "Enter the score and review"}
            </button>
            <span className="text-xs text-pitch-muted">
              {analysis.review.pendingKey !== undefined
                ? `${analysis.review.pendingKey} ${analysis.review.pendingKey === 1 ? "shot or goal" : "shots and goals"} to check`
                : `${analysis.review.pending} waiting`}
            </span>
            {advice.length > 0 && (
              <span className="text-xs text-pitch-muted max-w-md" data-testid="sample-advice">
                {advice.join(". ")}.
              </span>
            )}
          </div>
        </div>
        <p className="relative border-t border-white/10 px-5 py-2.5 text-xs text-pitch-muted flex flex-wrap items-center gap-x-4 gap-y-1 justify-center">
          {/* Stoppage time is not shown: against labelled matches the detector found
              only a few percent of real stoppages, and most of what it flagged was live play. */}
          {ballTracked ? (
            <>
              <span title="Retained detector and inferred positions; this is coverage, not verified accuracy.">Ball position coverage {cov.ballStatePercent}%</span>
              <span title="Breaks in play are only partly detected, so the true share of live play followed can be higher.">
                Possession followed at least {cov.possessionPercent}% of play
              </span>
            </>
          ) : (
            <span>Ball not tracked (player-load job)</span>
          )}
          {cov.calibratedPercent > 0 ? (
            <span>Pitch mapped {cov.calibratedPercent}%</span>
          ) : videoAvailable ? (
            <button className="underline text-pitch-green min-h-11 py-3 px-1" onClick={onCalibrate}>
              Pitch not set up · set it up
            </button>
          ) : (
            <span>Pitch not set up · {noFootage}</span>
          )}
          <span title="Pieces of player tracks after joining; one player usually appears as several.">{analysis.stats.tracks.players} track segments</span>
        </p>
      </motion.section>

      {afterHero}

      {ballTracked && (
        <section className="rounded-2xl border border-amber-300/20 bg-amber-300/5 p-4 text-sm" role="status">
          <strong>Assisted review</strong> · Automatic moments are candidates until checked. Check a random sample of
          passes (and each shot) and the counts become estimates marked ≈, with a range, scaled by how often the
          detections you checked were right. Moments the system never found are not included; add them in the review.
        </section>
      )}

      {!analysis.calibrated && (
        <section className="rounded-2xl border border-pitch-green/40 bg-pitch-green/5 p-5 flex flex-wrap gap-4 items-center justify-between">
          <div>
            <p className="font-semibold">{ballTracked ? "Unlock shots, heatmaps and positions" : "Unlock heatmaps and positions"}</p>
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
          {ballTracked ? (
          <>
          <Group title="Possession">
            <Row
              label="Observed control share"
              a={{ value: possA, note: possessionShown && range ? `unseen-play bounds: ${Math.round(range[0])}–${Math.round(range[1])}%` : undefined }}
              b={{ value: possB }}
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
              a={{ value: passShareA }}
              b={{ value: passShareB }}
              colours={colours}
              format={pct}
              hint="Each team's share of all detected passes. Many stats sites define possession this way; it can differ from the time-based figure above."
            />
            {/* The worker withholds the counts below 80% of play followed, a stricter gate than the share; hidden with the share too (older workers sent a 0). */}
            <Row
              label="Possessions"
              a={{ value: possessionShown ? A.possessions : null }}
              b={{ value: possessionShown ? B.possessions : null }}
              colours={colours}
              note={possessionShown && A.possessions === null && B.possessions === null ? "Shown once 80% of play is followed" : undefined}
            />
            <Row label="Average possession" a={{ value: possessionShown ? A.averagePossession : null }} b={{ value: possessionShown ? B.averagePossession : null }} colours={colours} format={(v) => `${v.toFixed(1)}s`} />
            <Row
              label="Field tilt"
              a={{ value: tiltA }}
              b={{ value: tiltB }}
              colours={colours}
              format={pct}
              hint="Share of all ball control in an attacking third that was this team's: who pinned whom back."
            />
          </Group>
          <Group title="Passing">
            <Row label="Passes" a={count(A.passes, pooled("pass", "passes"))} b={count(B.passes, pooled("pass", "passes"))} colours={colours} hint="Ball moved from one player to another (to a teammate or intercepted). Unseen transfers get lower confidence." />
            <Row label="Accurate passes" a={count(A.passesComplete, pooled("pass", "passes"))} b={count(B.passesComplete, pooled("pass", "passes"))} colours={colours} />
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
              <p className="text-xs text-amber-200/90 pt-1">
                The pitch is mapped for {cov.calibratedPercent}% of the match, so shots cover that part only.
              </p>
            )}
            <Row label="Shots" a={count(A.shots)} b={count(B.shots)} colours={colours} hint="Fast ball released towards the goal the team attacks. Needs the pitch set up, or add the shots you saw in review." />
            {shotsNote && (
              <p className="text-xs text-pitch-muted pb-1" data-testid="shots-note">
                {shotsNote}
              </p>
            )}
            <Row
              label="Shots on target"
              a={count(A.shotsOnTarget)}
              b={count(B.shotsOnTarget)}
              colours={colours}
              hint="Counted when the outcome shows it: a goal, or the keeper collecting a ball heading between the posts. Ball height cannot be seen from one camera."
            />
            <Row
              label="Possible goals"
              a={possibleGoals(A, pending[0])}
              b={possibleGoals(B, pending[1])}
              colours={colours}
              hint="Still to check: the ball appeared to cross the goal line between the posts. A person must confirm each goal."
            />
          </Group>
          <Group title="Defending">
            <Row
              label="Interceptions & recoveries"
              a={count(A.interceptions, pooled("interception", "interceptions"))}
              b={count(B.interceptions, pooled("interception", "interceptions"))}
              colours={colours}
              hint="Ball taken from the other team: an intercepted pass or a loose ball; without the pitch set up this includes tackles."
            />
            <Row
              label="Tackles"
              a={count(A.tackles, pooled("tackle", "tackles"))}
              b={count(B.tackles, pooled("tackle", "tackles"))}
              colours={colours}
              hint="Ball won within 2 m and 0.8 s of the opponent losing it; needs the pitch set up."
              // Unmeasured, not zero: without metres a tackle cannot be told from another won ball.
              note={!A.tackles && !B.tackles && !analysis.calibrated ? "Set up the pitch to tell tackles from other won balls." : undefined}
            />
          </Group>
          </>
          ) : (
            <p className="text-sm text-pitch-muted pt-3">
              Possession, passes, shots, interceptions and tackles need the ball model, which this job did not run.
            </p>
          )}
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
              ) : shotsNote ? (
                <p className="text-sm text-pitch-muted py-6 text-center" data-testid="shot-map-empty">
                  {shotsNote}
                </p>
              ) : (A.shots?.value ?? 0) + (B.shots?.value ?? 0) > 0 ? (
                <p className="text-sm text-pitch-muted py-6 text-center" data-testid="shot-map-empty">
                  The shots counted here have no position on the pitch to draw.
                </p>
              ) : (
                <p className="text-sm text-pitch-muted py-6 text-center" data-testid="shot-map-empty">No shots detected. Add any you saw in the review.</p>
              )}
            </section>
          )}
          {/* Momentum is built from ball control: nothing to draw without the ball model. */}
          {ballTracked && (
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
          )}
          {analysis.calibrated && analysis.stats.heatmaps && analysis.template && (
            <section className={card}>
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-xs uppercase tracking-widest text-pitch-muted">Heatmap</h2>
                <div className="flex gap-1 text-xs" role="tablist" aria-label="Heatmap team">
                  {([0, 1] as const).map((t) => (
                    <button key={t} role="tab" aria-selected={heatTeam === t} onClick={() => setHeatTeam(t)} className={`px-2.5 py-2.5 min-h-10 rounded-full ${heatTeam === t ? "text-[#0b0f1a] font-bold" : "text-pitch-muted"}`} style={heatTeam === t ? { background: colours[t] } : undefined}>
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
        <div className="flex flex-wrap items-center justify-between gap-2 mb-4">
          <h2 className="text-xs uppercase tracking-widest text-pitch-muted">Match timeline</h2>
          <span className="text-xs text-pitch-muted">{keyEvents.length} moment{keyEvents.length === 1 ? "" : "s"} on the timeline · tap to watch</span>
        </div>
        {/* On a phone a list with rows big enough to tap; from md up the strip. */}
        {keyEvents.length > 0 && (
          <ul className="md:hidden divide-y divide-white/5 max-h-[26rem] overflow-auto" data-testid="timeline-list">
            {keyEvents.map((e) => (
              <li key={e.id}>
                <button className="flex w-full items-center gap-3 min-h-11 py-2 text-left text-sm" onClick={() => onSeek(Math.max(0, e.t - 2))}>
                  <span className="font-mono text-pitch-green tabular-nums">{clockTime(e.t)}</span>
                  <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: e.team !== null ? colours[e.team] : "#94a3b8" }} />
                  <span className="flex-1 min-w-0 truncate">
                    {describeEvent(e)}
                    {e.team !== null ? ` · ${names[e.team]}` : ""}
                  </span>
                  <span className="text-xs text-pitch-muted shrink-0">{e.status === "confirmed" ? "confirmed" : "to review"}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="hidden md:block" data-testid="timeline-strip">
          <div className="relative h-14">
            <div className="absolute left-0 right-0 top-1/2 h-[3px] -translate-y-1/2 rounded-full bg-white/10" />
            {keyEvents.map((e) => (
              <button
                key={e.id}
                onClick={() => onSeek(Math.max(0, e.t - 2))}
                title={`${clockTime(e.t)} · ${e.team !== null ? names[e.team] : ""} · ${describeEvent(e)}${e.status === "confirmed" ? " · confirmed" : " · to review"}`}
                aria-label={`${clockTime(e.t)} · ${describeEvent(e)}${e.status === "confirmed" ? " · confirmed" : " · to review"}`}
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
        </div>
        {hiddenWon > 0 && (
          <button className="pitch-button-secondary min-h-11 mt-3 text-xs" aria-pressed={allMoments} onClick={() => setAllMoments((v) => !v)}>
            {allMoments ? "Key moments only" : `Show all moments (+${hiddenWon} won ball${hiddenWon === 1 ? "" : "s"} to review)`}
          </button>
        )}
      </section>
    </div>
  );
}
