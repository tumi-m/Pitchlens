import type { Analysis, CountStat } from "./analysis";
import { shares } from "./visionStats";

const clock = (seconds: number) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;

/** Share of in-play time the ball must be followed before possession is shown (the worker's default). */
const POSSESSION_MIN_COVERAGE = 0.6;

/** A count as the report shows it: unmeasured or nothing confirmed is "—", never 0;
 *  an estimate keeps its "≈" and range; a partial review is a lower bound. */
function count(c: CountStat | null | undefined): string {
  if (!c) return "—";
  if (!c.pending) return c.confirmed > 0 ? String(c.confirmed) : "—";
  if (c.estimate !== undefined && c.estimateRange) return `≈${c.estimate} (${c.estimateRange[0]}–${c.estimateRange[1]})`;
  return c.confirmed > 0 ? `${c.confirmed}+ confirmed` : "—";
}

const metres = (v: number | null | undefined) => (typeof v === "number" && Number.isFinite(v) ? `${v.toFixed(1)} m` : "—");

/**
 * The report as plain text a team can paste into a group chat: the same numbers
 * as the page and nothing more. Unmeasured stays "—", estimates keep "≈" and a
 * range, the score says who set it, possession only when the page shows it, and
 * nothing that points at the private match (no link, job id or owner key).
 */
export function reportSummary(input: {
  title: string;
  matchDate?: string;
  names: string[];
  analysis: Analysis;
  analysedDuration: number;
  /** [start, end] seconds of a test section; absent for the full match. */
  diagnosticSpan?: [number, number] | null;
  /** False for a player-load job: the ball model did not run. */
  ballTracked?: boolean;
  /** Per-kit running distance (absent on results before 2.6). */
  playerLoad?: { metres: [number | null, number | null]; steps?: number } | null;
}): string {
  const { title, names, analysis } = input;
  const ballTracked = input.ballTracked !== false;
  const [A, B] = analysis.stats.teams;
  const cov = analysis.stats.coverage;
  const lines: string[] = [title.trim() || "Match report"];
  if (input.matchDate) lines.push(input.matchDate);
  // Same rule as the report: a score only when the team entered it or goals were confirmed.
  const goals = [A.goals?.value ?? 0, B.goals?.value ?? 0];
  const reviewedGoals = A.goals !== null && B.goals !== null && goals[0] + goals[1] > 0;
  const score = analysis.enteredScore ?? (reviewedGoals ? goals : null);
  lines.push(score ? `${names[0]} ${score[0]}–${score[1]} ${names[1]}` : `${names[0]} v ${names[1]}`);
  lines.push(analysis.enteredScore ? "Score entered by the team" : score ? "Goals confirmed in review" : "Score not entered");
  if (input.diagnosticSpan)
    lines.push(`Test section ${clock(input.diagnosticSpan[0])}–${clock(input.diagnosticSpan[1])}, not the full match`);
  else lines.push(`${clock(input.analysedDuration)} analysed`);
  lines.push("");
  const row = (label: string, a: string, b: string, note?: string) =>
    lines.push(`${label}: ${a} – ${b}${note ? ` (${note})` : ""}`);
  const load = input.playerLoad;
  if (load && load.steps !== 0) row("Distance", metres(load.metres?.[0]), metres(load.metres?.[1]), "per kit, camera pan removed");
  else if (!ballTracked) row("Distance", "—", "—");
  if (!ballTracked) {
    lines.push("Ball not tracked (player-load job): possession, passes and shots were not measured.");
  } else {
    const minCoverage = (analysis.params?.possessionMinCoverage ?? POSSESSION_MIN_COVERAGE) * 100;
    if (cov.possessionShown) {
      const [pa, pb] = shares(A.possession, B.possession);
      const bounds = cov.possessionMissingBounds;
      row(
        "Possession",
        pa === null ? "—" : `${pa}%`,
        pb === null ? "—" : `${pb}%`,
        `ball followed at least ${Math.floor(cov.possessionPercent)}% of play${bounds ? `; unseen-play bounds ${Math.round(bounds[0])}–${Math.round(bounds[1])}%` : ""}`,
      );
    } else
      row(
        "Possession",
        "—",
        "—",
        cov.possessionPercent < minCoverage
          ? `ball followed only ${Math.floor(cov.possessionPercent)}% of play`
          : "no team possession established",
      );
    row("Passes", count(A.passes), count(B.passes));
    row("Accurate passes", count(A.passesComplete), count(B.passesComplete));
    const shotsWhy =
      cov.shotsBasis === "no-ball"
        ? "ball not found in this footage"
        : cov.shotsBasis === "no-direction"
          ? "attacking direction not set"
          : cov.shotsBasis === "no-pitch" || (cov.shotsBasis === undefined && A.shots === null)
            ? "pitch not set up"
            : undefined;
    row("Shots", count(A.shots), count(B.shots), A.shots === null ? shotsWhy : undefined);
    row("Won balls", count(A.interceptions), count(B.interceptions));
    row("Tackles", count(A.tackles), count(B.tackles), A.tackles === null && !analysis.calibrated ? "pitch not set up" : undefined);
  }
  lines.push("");
  lines.push("— not measured or none confirmed · ≈ estimate with a 95% range from moments a person checked · N+ confirmed so far");
  lines.push("Pitchlens match report");
  return lines.join("\n");
}
