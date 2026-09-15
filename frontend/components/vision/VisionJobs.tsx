"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { VisionJob, visionJson, clockTime } from "@/lib/review/vision";
export function VisionJobs() {
  const [jobs, setJobs] = useState<VisionJob[]>([]);
  useEffect(() => {
    const read = () =>
      visionJson<VisionJob[]>("jobs")
        .then(setJobs)
        .catch(() => {});
    read();
    const timer = setInterval(read, 5000);
    return () => clearInterval(timer);
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
