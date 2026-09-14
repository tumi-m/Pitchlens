"use client";
import { useEffect, useRef, useState } from "react";
import { processVideo } from "@/lib/utils/videoProcessor";
import { validateLinkedVideo } from "@/lib/review/portable";
import { saveVideo } from "@/lib/review/videoStore";
import type { VideoReview } from "@/lib/review/types";
export function RelinkVideo({
  matchId,
  review,
  onLinked,
}: {
  matchId: string;
  review: VideoReview;
  onLinked: () => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const controller = useRef<AbortController | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => () => controller.current?.abort(), []);
  async function link(file?: File) {
    if (!file || controller.current) return;
    const run = new AbortController();
    controller.current = run;
    setBusy(true);
    setError("");
    try {
      const candidate = await processVideo(file, { signal: run.signal });
      validateLinkedVideo(review, candidate);
      run.signal.throwIfAborted();
      await saveVideo(matchId, file);
      if (!run.signal.aborted) onLinked();
    } catch (err) {
      if (!run.signal.aborted)
        setError(
          err instanceof Error ? err.message : "Could not reconnect video.",
        );
    } finally {
      controller.current = null;
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }
  return (
    <div className="glass-card p-5 space-y-3">
      <h2 className="font-semibold">Reconnect the original video</h2>
      <p className="text-sm text-pitch-muted break-words">
        Choose {review.fileName}. Your tags and notes are already available.{" "}
        {review.videoFingerprint
          ? "A sample fingerprint, size, duration and resolution will be checked."
          : "This older review has no fingerprint. Size, duration and resolution will be checked; make sure you choose the original clip."}
      </p>
      <input
        ref={input}
        aria-label="Reconnect video file"
        type="file"
        accept="video/mp4,video/webm,video/quicktime,.mp4,.webm,.mov"
        onChange={(e) => void link(e.target.files?.[0])}
        className="sr-only"
        disabled={busy}
      />
      <button
        onClick={() => input.current?.click()}
        disabled={busy}
        className="pitch-button-primary"
      >
        {busy ? "Checking video…" : "Choose original video"}
      </button>
      {error && (
        <p role="alert" className="text-red-300 text-sm">
          {error}
        </p>
      )}
    </div>
  );
}
