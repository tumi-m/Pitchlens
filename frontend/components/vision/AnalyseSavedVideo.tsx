"use client";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { loadVideo } from "@/lib/review/videoStore";
import {
  visionJson,
  VisionError,
  VisionHealth,
  uploadToVision,
  visionAccessCode,
  setVisionAccessCode,
} from "@/lib/review/vision";
export function AnalyseSavedVideo({
  id,
  title,
}: {
  id: string;
  title: string;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [percent, setPercent] = useState(0);
  const [error, setError] = useState("");
  const upload = useRef<AbortController | null>(null);
  const [health, setHealth] = useState<VisionHealth | null>(null);
  const [code, setCode] = useState("");
  const [needsCode, setNeedsCode] = useState(false);
  useEffect(() => {
    fetch("/api/vision/health", { cache: "no-store" })
      .then((r) => r.json().catch(() => ({})))
      .then((x: VisionHealth) => {
        setHealth(x);
        setCode(visionAccessCode());
        setNeedsCode(!!x.accessRequired && !visionAccessCode());
      })
      .catch(() => setHealth({ available: false }));
    return () => upload.current?.abort();
  }, []);
  async function run() {
    setBusy(true);
    setError("");
    setPercent(0);
    const controller = new AbortController();
    upload.current = controller;
    try {
      const status = await visionJson<VisionHealth>("health");
      if (!status.available)
        throw new Error(
          status.hosted
            ? "The analysis server is not responding. Retry in a minute."
            : "Start the local vision worker first.",
        );
      if (status.accessRequired) {
        if (!code.trim()) {
          setNeedsCode(true);
          throw new Error("Enter the access code first.");
        }
        setVisionAccessCode(code.trim());
      }
      const video = await loadVideo(id);
      if (!video) throw new Error("Reconnect the original video first.");
      const job = await uploadToVision(video, {
        title,
        signal: controller.signal,
        onProgress: setPercent,
      });
      router.push(`/vision/${job.id}`);
    } catch (e) {
      if (e instanceof VisionError && e.code === "access") {
        setVisionAccessCode("");
        setCode("");
        setNeedsCode(true);
      }
      setError(
        e instanceof DOMException && e.name === "AbortError"
            ? "Upload cancelled."
            : e instanceof Error
              ? e.message
              : "Unable to start analysis",
      );
      setBusy(false);
    } finally {
      upload.current = null;
    }
  }
  // No analysis server on this deployment: don't offer a button that can only fail.
  if (!health || health.configured === false) return null;
  return (
    <div className="space-y-2">
      {needsCode && (
        <input
          aria-label="Access code"
          type="password"
          autoComplete="off"
          placeholder="Access code"
          className="pitch-input w-full max-w-sm"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          disabled={busy}
        />
      )}
      <button
        disabled={busy || !health.available}
        onClick={run}
        className="pitch-button-primary"
        title={health.available ? undefined : "The analysis server is not responding"}
      >
        {busy
          ? `Sending video for analysis · ${percent}%`
          : "Analyse this video automatically"}
      </button>
      {busy && (
        <button
          onClick={() => upload.current?.abort()}
          className="pitch-button-secondary text-sm"
        >
          Cancel upload
        </button>
      )}
      {error && (
        <p role="alert" className="text-red-300 text-sm mt-2 max-w-sm">
          {error}
        </p>
      )}
    </div>
  );
}
