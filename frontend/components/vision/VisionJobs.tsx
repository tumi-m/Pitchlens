"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import { VisionJob, visionJson, clockTime } from "@/lib/review/vision";
export function VisionJobs() {
  const [jobs, setJobs] = useState<VisionJob[]>([]);
  const [error, setError] = useState("");
  const [deleting, setDeleting] = useState<string | null>(null);
  async function remove(job: VisionJob) {
    if (!window.confirm(`Delete "${job.title}" and its video, report, reviews and derived venue setup? This cannot be undone.`)) return;
    setDeleting(job.id);
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
            </p>
            <h3 className="font-semibold break-words">{j.title}</h3>
            <p className="text-sm text-pitch-muted">
              {j.video ? `${clockTime(j.video.duration)} · ` : ""}
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
