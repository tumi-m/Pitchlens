"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { VisionJob, visionJson, clockTime } from "@/lib/review/vision";
export function VisionJobs() {
  const [jobs, setJobs] = useState<VisionJob[]>([]);
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
      <div className="grid md:grid-cols-2 gap-4">
        {jobs.map((j) => (
          <Link
            key={j.id}
            href={`/vision/${j.id}`}
            className="glass-card p-5 space-y-2"
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
        ))}
      </div>
    </section>
  );
}
