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
  cleanAccessCode,
  ACCESS_CODE_CHARACTERS,
  CONNECTION_LOST,
  connectionLost,
  defaultVisionOptions,
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
  const left = useRef(false);
  const [health, setHealth] = useState<VisionHealth | null>(null);
  const [code, setCode] = useState("");
  const [needsCode, setNeedsCode] = useState(false);
  // Same default as the upload page: player load, the ball model only on request.
  const [ballModel, setBallModel] = useState(false);
  useEffect(() => {
    left.current = false;
    fetch("/api/vision/health", { cache: "no-store" })
      .then((r) => r.json().catch(() => ({})))
      .then((x: VisionHealth) => {
        setHealth(x);
        setCode(visionAccessCode());
        if (x.accessRequired) setNeedsCode(true);
        else if (x.available) setVisionAccessCode("");
      })
      .catch(() => setHealth({ available: false }));
    return () => {
      left.current = true;
      // Stops an unfinished upload; an analysis already started is never cancelled.
      upload.current?.abort();
    };
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
        if (!cleanAccessCode(code)) {
          setNeedsCode(true);
          throw new Error("Enter the access code first.");
        }
        if (!setVisionAccessCode(code)) throw new Error(ACCESS_CODE_CHARACTERS);
      }
      const video = await loadVideo(id);
      if (!video) throw new Error("Reconnect the original video first.");
      const job = await uploadToVision(video, {
        title,
        ...defaultVisionOptions(status, { playerLoadOnly: !ballModel }),
        signal: controller.signal,
        onProgress: setPercent,
      });
      // Left the page meanwhile: the analysis runs on and is listed on the dashboard.
      if (!left.current) router.push(`/vision/${job.id}`);
    } catch (e) {
      if (e instanceof VisionError && e.code === "access") {
        setVisionAccessCode("");
        setCode("");
        setNeedsCode(true);
      }
      setError(
        e instanceof DOMException && e.name === "AbortError"
            ? "Upload cancelled."
            : connectionLost(e)
              ? CONNECTION_LOST
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
          data-access-field
          type="password"
          autoComplete="off"
          placeholder="Access code"
          className="pitch-input w-full max-w-sm"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          disabled={busy}
        />
      )}
      <label className="flex gap-2 text-sm max-w-sm">
        <input type="checkbox" checked={ballModel} disabled={busy} onChange={(e) => setBallModel(e.target.checked)} />
        Run the ball model (possession and passes; a costlier job). Without it, Pitchlens measures how far each kit ran.
      </label>
      <button
        disabled={busy || !health.available}
        onClick={run}
        className="pitch-button-primary"
        title={health.available ? undefined : "The analysis server is not responding"}
      >
        {busy
          ? percent < 100
            ? `Sending video for analysis · ${percent}%`
            : "Starting the analysis…"
          : "Analyse this video automatically"}
      </button>
      {/* From 100% the analysis may already be running: its report page can cancel it. */}
      {busy && percent < 100 && (
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
