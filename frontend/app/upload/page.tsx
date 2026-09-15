"use client";
import { useEffect, useRef, useState } from "react";
import { VisionUpload } from "@/components/vision/VisionUpload";
import { useDropzone } from "react-dropzone";
import { useRouter } from "next/navigation";
import { Upload, Film, ArrowRight, Loader2 } from "lucide-react";
import { Navbar } from "@/components/ui/Navbar";
import { useAuthContext } from "@/components/auth/AuthProvider";
import { saveMatchLocally } from "@/lib/firebase/firestore";
import { saveVideo, deleteVideo } from "@/lib/review/videoStore";
import { processVideo } from "@/lib/utils/videoProcessor";
import { formatFileSize } from "@/lib/utils/analytics";

export default function UploadPage() {
  const router = useRouter();
  const [manual, setManual] = useState(false);
  const { user } = useAuthContext();
  const [file, setFile] = useState<File | null>(null);
  const [home, setHome] = useState("Home Team");
  const [away, setAway] = useState("Away Team");
  const [inference, setInference] = useState(false);
  const [configured, setConfigured] = useState(false);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [stage, setStage] = useState("");
  const [error, setError] = useState("");
  const active = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/infer", { signal: controller.signal })
      .then((r) => r.json())
      .then((d) => setConfigured(d.configured === true))
      .catch(() => {});
    return () => {
      controller.abort();
      active.current?.abort();
    };
  }, []);
  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    accept: {
      "video/mp4": [".mp4"],
      "video/webm": [".webm"],
      "video/quicktime": [".mov"],
    },
    maxSize: 500 * 1024 * 1024,
    maxFiles: 1,
    disabled: busy,
    onDrop: (files) => {
      if (files[0]) {
        setFile(files[0]);
        setError("");
      }
    },
    onDropRejected: () =>
      setError(
        "Choose one MP4, WebM or MOV video, up to 500 MB. Browser codec support varies.",
      ),
  });
  async function analyse() {
    if (!file || active.current) return;
    const controller = new AbortController();
    active.current = controller;
    const id = `local_${crypto.randomUUID()}`;
    setBusy(true);
    setError("");
    try {
      const review = await processVideo(file, {
        inference,
        signal: controller.signal,
        onStage: setStage,
        onProgress: setProgress,
      });
      controller.signal.throwIfAborted();
      setStage("Saving video on this device");
      setProgress(95);
      await saveVideo(id, file);
      controller.signal.throwIfAborted();
      saveMatchLocally(id, {
        userId: user?.uid ?? "guest",
        title: `${home.trim()} vs ${away.trim()}`,
        homeTeamName: home.trim(),
        awayTeamName: away.trim(),
        homeTeamColor: "#e85b5b",
        awayTeamColor: "#61a4f7",
        videoUrls: [],
        duration: review.duration,
        status: "completed",
        processingProgress: 100,
        review,
      });
      setProgress(100);
      router.push(`/dashboard/${id}`);
    } catch (err) {
      await deleteVideo(id).catch(() => {});
      setError(
        err instanceof Error && err.name === "AbortError"
          ? "Processing cancelled. Your file is still selected."
          : err instanceof Error
            ? err.message
            : "Unable to prepare video.",
      );
      setBusy(false);
    } finally {
      active.current = null;
    }
  }
  if (!manual) return <VisionUpload onManual={() => setManual(true)} />;
  return (
    <>
      <Navbar />
      <main className="min-h-screen pt-28 pb-16 px-4">
        <div className="max-w-3xl mx-auto space-y-8">
          <div>
            <p className="text-pitch-green text-xs font-semibold uppercase tracking-widest mb-3">
              The review room
            </p>
            <h1 className="text-4xl font-bold mb-3">Start with the footage.</h1>
            <p className="text-pitch-muted leading-relaxed">
              Watch your match, tag the moments that matter, and build a report
              from what actually happened.
            </p>
          </div>
          <div
            {...getRootProps()}
            className={`rounded-2xl border-2 border-dashed p-12 text-center cursor-pointer ${isDragActive ? "border-pitch-green bg-pitch-green/10" : "border-pitch-indigo-soft/50"}`}
          >
            <input {...getInputProps()} aria-label="Match video" />
            {file ? (
              <Film className="mx-auto mb-4 text-pitch-green" size={36} />
            ) : (
              <Upload className="mx-auto mb-4 text-pitch-green" size={36} />
            )}
            <p className="font-semibold break-all">
              {file ? file.name : "Drop your match video here"}
            </p>
            <p className="text-sm text-pitch-muted mt-2">
              {file
                ? `${formatFileSize(file.size)} · Click to replace`
                : "Or click to choose · MP4, WebM, MOV · Up to 500 MB"}
            </p>
          </div>
          <div className="grid sm:grid-cols-2 gap-5">
            {(
              [
                ["Home team", home, setHome],
                ["Away team", away, setAway],
              ] as const
            ).map(([label, value, setter]) => (
              <label key={label} className="text-sm space-y-2">
                <span>{label}</span>
                <input
                  value={value}
                  onChange={(e) => setter(e.target.value)}
                  maxLength={80}
                  disabled={busy}
                  className="pitch-input w-full"
                />
              </label>
            ))}
          </div>
          <div className="glass-card p-5 space-y-3">
            <label className="flex gap-3 items-start">
              <input
                type="checkbox"
                checked={inference}
                onChange={(e) => setInference(e.target.checked)}
                disabled={!configured || busy}
                className="mt-1"
              />
              <span>
                <span className="font-medium">Add AI frame inspection</span>
                <span className="block text-pitch-muted text-sm mt-1">
                  {configured
                    ? "Sends six sample frames to Roboflow to detect players and the ball. This does not calculate match statistics."
                    : "Unavailable on this installation. You can still review and tag the full video."}
                </span>
              </span>
            </label>
          </div>
          {error && (
            <p
              role="alert"
              className="rounded-xl bg-red-500/10 border border-red-500/30 p-4 text-red-300"
            >
              {error}
            </p>
          )}
          {busy ? (
            <div className="space-y-4" aria-live="polite">
              <div className="flex justify-between text-sm">
                <span className="flex gap-2">
                  <Loader2 size={16} className="animate-spin" />
                  {stage}
                </span>
                <span>{progress}%</span>
              </div>
              <progress
                value={progress}
                max={100}
                className="w-full accent-green-500"
              />
              <button
                className="pitch-button-secondary"
                onClick={() => active.current?.abort()}
              >
                Cancel
              </button>
            </div>
          ) : (
            <button
              onClick={analyse}
              disabled={!file || !home.trim() || !away.trim()}
              className="pitch-button-primary w-full py-4"
            >
              Open review room <ArrowRight size={18} />
            </button>
          )}
          <p className="text-sm text-pitch-muted">
            Video and notes are saved in this browser on this device. Export
            your review for a backup. To use footage from Drive, download the
            file first.
          </p>
        </div>
      </main>
    </>
  );
}
