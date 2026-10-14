"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import { VisionJob, VisionError, visionJson, clockTime, matchDateLabel } from "@/lib/review/vision";

/** Which part of the file a job analysed: a test section, or the whole match. */
function span(j: VisionJob) {
  const duration = j.video?.duration;
  if (j.maxSeconds) {
    const start = j.startSeconds ?? 0;
    const end = duration !== undefined ? Math.min(start + j.maxSeconds, duration) : start + j.maxSeconds;
    return `Test ${clockTime(start)}–${clockTime(end)}`;
  }
  return duration !== undefined ? `Full match · ${clockTime(duration)}` : "Full match";
}

/** Analyses of the same uploaded video: re-runs share (hard-link) its file, so it
 *  stays on the server until every one of them is deleted. */
const videoKey = (j: VisionJob) => j.rootJobId ?? j.sourceJobId ?? j.id;
const busyStatus = (j: VisionJob) => ["uploading", "processing"].includes(j.status);

/** Which saved venues deleting this match also deletes (the worker removes venues saved from it). */
async function venuesFrom(jobId: string): Promise<string[] | null> {
  try {
    const venues = await visionJson<{ name?: string; sourceJob?: string | null }[]>("venues");
    return venues.filter((v) => v.sourceJob === jobId).map((v) => v.name || "Unnamed venue");
  } catch {
    return null; // unknown: say so in general terms
  }
}

function deleteMessage(title: string, venues: string[] | null) {
  const quoted = (venues ?? []).map((v) => `"${v}"`);
  const venueNote =
    venues === null
      ? " Any venue saved from this match is deleted too."
      : quoted.length
        ? ` This also deletes the saved venue${quoted.length === 1 ? "" : "s"} ${quoted.length === 1 ? quoted[0] : `${quoted.slice(0, -1).join(", ")} and ${quoted.at(-1)}`}; later matches will no longer set up automatically.`
        : "";
  return `Delete "${title}" and its video, report, reviews and pitch setup?${venueNote} This cannot be undone.`;
}

/** Lists this browser's analyses. onCount reports how many once the first read has finished. */
export function VisionJobs({ onCount }: { onCount?: (count: number) => void }) {
  const [jobs, setJobs] = useState<VisionJob[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [deleting, setDeleting] = useState<string | null>(null);
  /** Deleting one analysis of a video that others still use: asked inline, with the choice to delete them all. */
  const [asking, setAsking] = useState<{ job: VisionJob; others: VisionJob[]; venues: string[] | null } | null>(null);
  useEffect(() => {
    if (loaded) onCount?.(jobs.length);
  }, [loaded, jobs.length, onCount]);
  async function remove(job: VisionJob) {
    setDeleting(job.id);
    setAsking(null);
    const venues = await venuesFrom(job.id);
    const others = jobs.filter((j) => j.id !== job.id && videoKey(j) === videoKey(job));
    if (others.length) {
      setAsking({ job, others, venues });
      setDeleting(null);
      return;
    }
    if (!window.confirm(deleteMessage(job.title, venues))) {
      setDeleting(null);
      return;
    }
    await deleteJobs([job]);
  }
  async function deleteJobs(list: VisionJob[]) {
    setAsking(null);
    setError("");
    for (const job of list) {
      setDeleting(job.id);
      try {
        await visionJson(`jobs/${job.id}`, { method: "DELETE" });
        setJobs((items) => items.filter((item) => item.id !== job.id));
        try { localStorage.removeItem(`vision-names-${job.id}`); } catch {}
      } catch (e) {
        setError(e instanceof Error ? e.message : "Deletion failed. Try again.");
        break;
      }
    }
    setDeleting(null);
  }
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;
    let inFlight = false;
    // Poll only while something is still running and the tab is visible:
    // every poll is a serverless invocation plus a worker directory scan.
    const schedule = (ms: number) => {
      clearTimeout(timer);
      if (!stopped && document.visibilityState !== "hidden")
        timer = setTimeout(read, ms);
    };
    const read = () => {
      if (inFlight || stopped) return;
      inFlight = true;
      clearTimeout(timer);
      visionJson<VisionJob[]>("jobs")
        .then((next) => {
          if (stopped) return;
          setJobs(next);
          if (next.some((j) => ["uploading", "processing"].includes(j.status)))
            schedule(5000);
        })
        .catch((e) => {
          // A refusal (400, 401, 403...) does not change by asking again.
          if (e instanceof VisionError && e.status < 500 && e.status !== 408 && e.status !== 429) return;
          // A worker that is restarting comes back: keep checking, more slowly.
          schedule(30_000);
        })
        .finally(() => {
          inFlight = false;
          if (!stopped) setLoaded(true);
        });
    };
    const visible = () => {
      if (document.visibilityState === "visible") read();
      else clearTimeout(timer);
    };
    read();
    document.addEventListener("visibilitychange", visible);
    return () => {
      stopped = true;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", visible);
    };
  }, []);
  if (!jobs.length) return null;
  return (
    <section className="space-y-4">
      <h2 className="text-xl font-semibold">Computer vision analyses</h2>
      {error && <p role="alert" className="text-red-300">{error}</p>}
      <div className="grid md:grid-cols-2 gap-4">
        {jobs.map((j, i) => (
          <motion.div
            key={j.id}
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: Math.min(i, 10) * 0.05 }}
            whileHover={{ y: -3 }}
          >
          <Link
            href={`/vision/${j.id}`}
            className="glass-card p-5 space-y-2 block transition-colors hover:border-pitch-green/40"
          >
            <p className="text-pitch-green text-xs uppercase">
              {j.status === "completed" ? "Vision report" : j.status}
              {j.sourceJobId ? " · Re-analysis" : ""}
            </p>
            {/* The owner's names for the teams and the day it was played, once set; the upload date until then. */}
            <h3 className="font-semibold break-words">
              {j.title}
              {j.teamNames ? ` · ${j.teamNames[0]} v ${j.teamNames[1]}` : ""}
            </h3>
            <p className="text-sm text-pitch-muted" data-testid="vision-job-span">
              {span(j)}
              {matchDateLabel(j.matchDate)
                ? ` · ${matchDateLabel(j.matchDate)}`
                : j.createdAt
                  ? ` · ${new Date(j.createdAt * 1000).toLocaleDateString()}`
                  : ""}
            </p>
            <p className="text-sm text-pitch-muted">
              {j.stage}
              {j.status === "processing"
                ? ` · ${j.progress}%`
                : j.status === "uploading" && j.expectedBytes
                  ? ` · ${Math.floor(((j.receivedBytes ?? 0) / j.expectedBytes) * 100)}%`
                  : ""}
            </p>
            {j.status === "uploading" && !!j.expectedBytes && (
              <p className="text-xs text-pitch-muted">Sent from the page that started it</p>
            )}
          </Link>
          {!busyStatus(j) && (
            <button className="pitch-button-secondary min-h-11 mt-3" disabled={deleting !== null} onClick={() => remove(j)}>
              {deleting === j.id ? "Deleting…" : "Delete analysis and footage"}
            </button>
          )}
          {asking?.job.id === j.id && (
            <div role="alertdialog" aria-label="Delete analysis" className="glass-card border-red-400/40 p-4 mt-3 space-y-3 text-sm" data-testid="delete-shared-video">
              {(() => {
                const kept = asking.others.filter((o) => !busyStatus(o));
                const running = asking.others.filter(busyStatus);
                const quoted = (asking.venues ?? []).map((v) => `"${v}"`);
                return (
                  <>
                    <p>
                      Delete &quot;{j.title}&quot; with its report, reviews and pitch setup?
                      {quoted.length ? ` This also deletes the saved venue${quoted.length === 1 ? "" : "s"} ${quoted.join(", ")}.` : ""}
                    </p>
                    <p className="text-pitch-muted">
                      The video stays on the server while {asking.others.length} other analys{asking.others.length === 1 ? "is" : "es"} of
                      it remain{asking.others.length === 1 ? "s" : ""} ({asking.others.map(span).join(", ")}).
                      {running.length ? ` Still running, so not deletable yet: ${running.map(span).join(", ")}.` : ""}
                    </p>
                    <div className="flex flex-wrap gap-2">
                      <button className="pitch-button-secondary min-h-11" onClick={() => deleteJobs([j])}>
                        Delete this analysis only
                      </button>
                      <button className="pitch-button-secondary min-h-11 border-red-400/50" onClick={() => deleteJobs([j, ...kept])}>
                        Delete all analyses of this video
                      </button>
                      <button className="pitch-button-secondary min-h-11" onClick={() => setAsking(null)}>
                        Keep
                      </button>
                    </div>
                  </>
                );
              })()}
            </div>
          )}
          </motion.div>
        ))}
      </div>
    </section>
  );
}
