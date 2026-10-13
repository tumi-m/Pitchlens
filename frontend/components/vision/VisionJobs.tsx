"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import { VisionJob, visionJson, clockTime } from "@/lib/review/vision";

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
  useEffect(() => {
    if (loaded) onCount?.(jobs.length);
  }, [loaded, jobs.length, onCount]);
  async function remove(job: VisionJob) {
    setDeleting(job.id);
    const venues = await venuesFrom(job.id);
    if (!window.confirm(deleteMessage(job.title, venues))) {
      setDeleting(null);
      return;
    }
    setError("");
    try {
      await visionJson(`jobs/${job.id}`, { method: "DELETE" });
      setJobs((items) => items.filter((item) => item.id !== job.id));
      try { localStorage.removeItem(`vision-names-${job.id}`); } catch {}
    } catch (e) {
      setError(e instanceof Error ? e.message : "Deletion failed. Try again.");
    } finally {
      setDeleting(null);
    }
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
        // A worker that is restarting comes back: keep checking, more slowly.
        .catch(() => schedule(30_000))
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
            <h3 className="font-semibold break-words">{j.title}</h3>
            <p className="text-sm text-pitch-muted" data-testid="vision-job-span">
              {span(j)}
              {j.createdAt ? ` · ${new Date(j.createdAt * 1000).toLocaleDateString()}` : ""}
            </p>
            <p className="text-sm text-pitch-muted">
              {j.stage}
              {j.status === "processing" ? ` · ${j.progress}%` : ""}
            </p>
          </Link>
          {!["uploading", "processing"].includes(j.status) && (
            <button className="text-xs text-pitch-muted underline mt-2" disabled={deleting !== null} onClick={() => remove(j)}>
              {deleting === j.id ? "Deleting…" : "Delete analysis and footage"}
            </button>
          )}
          </motion.div>
        ))}
      </div>
    </section>
  );
}
