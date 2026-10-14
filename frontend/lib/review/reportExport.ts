import type { Analysis } from "./analysis";
import type { VisionResult } from "./vision";

/**
 * The reviewed match as a coach would keep or share it: stats, coverage, events
 * and the entered score. Per-frame positions, heatmaps and other geometry stay
 * out. Unmeasured values stay null (never 0), and goals are the analysis's own
 * count, which means confirmed in review.
 */
export function buildReportExport(input: {
  jobId: string;
  title: string;
  names: string[];
  result: VisionResult;
  analysis: Analysis;
}) {
  const { jobId, title, names, result, analysis } = input;
  return {
    format: "pitchlens-report",
    version: 1,
    exportedAt: new Date().toISOString(),
    jobId,
    title,
    analysedStart: result.analysedStart ?? 0,
    analysedDuration: result.analysedDuration,
    calibrated: analysis.calibrated,
    enteredScore: analysis.enteredScore ?? null,
    teams: [0, 1].map((i) => ({
      name: names[i] ?? null,
      colour: result.teams[i]?.colour ?? null,
      stats: analysis.stats.teams[i] ?? null,
    })),
    coverage: analysis.stats.coverage,
    review: analysis.review,
    events: analysis.events.map((e) => ({
      t: e.t,
      team: e.team,
      type: e.type,
      status: e.status,
      // Events the reviewer did not add come from the automatic detection.
      source: e.source ?? "automatic",
      outcome: e.outcome ?? null,
    })),
    limitations: result.limitations,
  };
}
