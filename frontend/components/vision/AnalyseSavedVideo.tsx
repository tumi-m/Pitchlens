"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { loadVideo } from "@/lib/review/videoStore";
import { visionJson, VisionJob } from "@/lib/review/vision";
export function AnalyseSavedVideo({
  id,
  title,
}: {
  id: string;
  title: string;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function run() {
    setBusy(true);
    setError("");
    try {
      const status = await visionJson<{ available: boolean }>("health");
      if (!status.available)
        throw new Error("Start the local vision worker first.");
      const video = await loadVideo(id);
      if (!video) throw new Error("Reconnect the original video first.");
      const job = await visionJson<VisionJob>(
        `jobs?title=${encodeURIComponent(title)}`,
        {
          method: "POST",
          body: video,
          headers: { "Content-Type": video.type || "video/mp4" },
        },
      );
      router.push(`/vision/${job.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to start analysis");
      setBusy(false);
    }
  }
  return (
    <div>
      <button disabled={busy} onClick={run} className="pitch-button-primary">
        {busy
          ? "Sending video to local worker…"
          : "Analyse this video automatically"}
      </button>
      {error && (
        <p role="alert" className="text-red-300 text-sm mt-2 max-w-sm">
          {error}
        </p>
      )}
    </div>
  );
}
