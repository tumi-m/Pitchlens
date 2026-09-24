"use client";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Upload, ScanLine, Loader2, KeyRound } from "lucide-react";
import { Navbar } from "@/components/ui/Navbar";
import {
  VisionHealth,
  VisionError,
  uploadToVision,
  analyseYouTube,
  visionAccessCode,
  setVisionAccessCode,
} from "@/lib/review/vision";

export function VisionUpload({ onManual }: { onManual: () => void }) {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [source, setSource] = useState<"file" | "youtube">("file");
  const [link, setLink] = useState("");
  const [rights, setRights] = useState(false);
  const linkOk =
    /^https?:\/\/((www\.|m\.)?youtube\.com\/(watch\?|shorts\/|live\/)|youtu\.be\/)/.test(
      link.trim(),
    );
  const ready = source === "file" ? !!file : linkOk && rights;
  const [title, setTitle] = useState("");
  const [health, setHealth] = useState<VisionHealth | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [percent, setPercent] = useState(0);
  const [phase, setPhase] = useState("");
  const [profile, setProfile] = useState("general");
  const [fps, setFps] = useState("3");
  const [code, setCode] = useState("");
  const [needsCode, setNeedsCode] = useState(false);
  const upload = useRef<AbortController | null>(null);
  useEffect(() => {
    setCode(visionAccessCode());
    // Read the body even on 503: it says whether the worker is unset, down or local.
    fetch("/api/vision/health", { cache: "no-store" })
      .then((r) => r.json().catch(() => ({})))
      .then((x: VisionHealth) => {
        setHealth({ ...x, available: x.available === true });
        // Football-trained detector (players, keepers, referees + tiled ball
        // model) beats the general people detector whenever it is installed.
        if (x.profiles?.includes("broadcast")) setProfile("broadcast");
        setNeedsCode(!!x.accessRequired && !visionAccessCode());
      })
      .catch(() => setHealth({ available: false }));
    return () => upload.current?.abort();
  }, []);
  const available = health?.available === true;
  const hosted = health?.hosted === true;
  // Local setup instructions only make sense to someone running the site themselves.
  const local =
    health?.hosted === false ||
    (typeof window !== "undefined" &&
      ["localhost", "127.0.0.1"].includes(window.location.hostname));
  const retention = health?.retentionHours
    ? health.retentionHours >= 48
      ? `${Math.round(health.retentionHours / 24)} days`
      : `${Math.round(health.retentionHours)} hours`
    : "";
  const profiles = health?.profiles ?? ["general"];
  async function submit() {
    if (!ready) return;
    if (health?.accessRequired) {
      if (!code.trim()) {
        setNeedsCode(true);
        setError("Enter the access code first.");
        return;
      }
      setVisionAccessCode(code.trim());
    }
    const controller = new AbortController();
    upload.current = controller;
    setError("");
    setBusy(true);
    setPercent(0);
    setPhase("Reserving the analysis server");
    try {
      if (source === "youtube") {
        setPhase("Sending the link to the analysis server");
        const job = await analyseYouTube(link.trim(), {
          title: title.trim(),
          profile,
          fps,
        });
        router.push(`/vision/${job.id}`);
        return;
      }
      if (!file) return;
      const job = await uploadToVision(file, {
        title: title.trim() || file.name,
        profile,
        fps,
        signal: controller.signal,
        onProgress: (p) => {
          setPercent(p);
          setPhase(p < 100 ? "Uploading" : "Checking the video");
        },
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
          ? "Upload cancelled. Your file is still selected."
          : e instanceof Error
            ? e.message
            : "Unable to start analysis",
      );
      setBusy(false);
    } finally {
      upload.current = null;
    }
  }
  return (
    <>
      <Navbar />
      <main className="min-h-screen pt-28 pb-16 px-4">
        <div className="max-w-3xl mx-auto space-y-7">
          <div>
            <p className="text-pitch-green text-xs tracking-widest uppercase mb-3">
              Computer vision · {hosted ? "Pitchlens analysis server" : "Local processing"}
            </p>
            <h1 className="text-4xl font-bold mb-4">
              Let the footage do the talking.
            </h1>
            <p className="text-pitch-muted">
              Detect players and the ball across the video, follow track IDs,
              separate kit colours and find possible passes and changes of
              possession automatically.
            </p>
          </div>
          <div className="grid sm:grid-cols-3 gap-3">
            {[
              "Player + ball detection",
              "Kit grouping + tracking",
              "Evidence-linked analytics",
            ].map((x) => (
              <div key={x} className="glass-card p-4 text-sm">
                <ScanLine size={19} className="text-pitch-green mb-3" />
                {x}
              </div>
            ))}
          </div>
          {health && !available && (
            <div
              role="alert"
              className="border border-amber-500/40 bg-amber-500/10 rounded-xl p-4 text-sm space-y-2"
            >
              {local ? (
                <>
                  <p className="font-semibold">
                    Start the local vision worker to analyse a match.
                  </p>
                  <p>
                    This runs on your computer with downloaded model weights. No
                    Roboflow key is needed.
                  </p>
                  <code className="block break-all">
                    python scripts/start_vision.py
                  </code>
                  <p>
                    Run from the backend folder after following docs/VISION.md.
                    Then reload this page.
                  </p>
                </>
              ) : health.configured === false ? (
                <>
                  <p className="font-semibold">
                    Automatic analysis isn&apos;t switched on for this site yet.
                  </p>
                  <p>
                    Manual review works meanwhile. Site owner: see
                    docs/DEPLOY-VISION.md.
                  </p>
                </>
              ) : !health.detail || /^Cannot reach/.test(health.detail) ? (
                <>
                  <p className="font-semibold">
                    The analysis server is not responding.
                  </p>
                  <p>
                    It may be starting up or redeploying. Reload this page in a
                    minute, or use manual review.
                  </p>
                </>
              ) : (
                <>
                  <p className="font-semibold">
                    Automatic analysis is unavailable.
                  </p>
                  <p>{health.detail}</p>
                  <p>Manual review works meanwhile.</p>
                </>
              )}
            </div>
          )}
          <div
            role="tablist"
            aria-label="Video source"
            className="flex gap-1 p-1 rounded-xl border border-pitch-indigo-soft/30 bg-pitch-indigo-deep/40"
          >
            {(
              [
                ["file", "Upload a file"],
                ["youtube", "YouTube link"],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                role="tab"
                aria-selected={source === value}
                disabled={busy}
                onClick={() => {
                  setSource(value);
                  setError("");
                }}
                className={`flex-1 py-2.5 rounded-lg text-sm font-medium ${source === value ? "bg-pitch-indigo-soft/50 text-pitch-white" : "text-pitch-muted"}`}
              >
                {label}
              </button>
            ))}
          </div>
          {source === "file" ? (
          <label className="block glass-card border-2 border-dashed border-pitch-green/40 p-10 text-center cursor-pointer">
            <Upload className="mx-auto text-pitch-green mb-4" />
            <span className="block font-semibold break-all">
              {file ? file.name : "Choose your match video"}
            </span>
            <span className="text-sm text-pitch-muted block mt-2">
              MP4 or MOV (H.264/HEVC) · Up to 500 MB ·{" "}
              {hosted
                ? `Uploaded to the Pitchlens analysis server${retention ? `; footage deleted after ${retention}` : ""}`
                : "Video stays on this computer"}
            </span>
            <input
              aria-label="Video for computer vision"
              type="file"
              accept="video/mp4,video/quicktime,.mp4,.mov"
              disabled={busy}
              className="mt-5 max-w-full text-sm"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (!f) return;
                if (f.size > 500 * 1024 * 1024) {
                  setFile(null);
                  setError("Video exceeds 500 MB");
                  return;
                }
                if (!/\.(mp4|mov)$/i.test(f.name)) {
                  setFile(null);
                  setError("Choose an MP4 or MOV video.");
                  return;
                }
                setFile(f);
                setTitle(f.name.replace(/\.(mp4|mov)$/i, ""));
                setError("");
              }}
            />
          </label>
          ) : (
            <div className="glass-card p-6 space-y-4">
              <label className="block text-sm">
                YouTube video link
                <input
                  aria-label="YouTube video link"
                  type="url"
                  inputMode="url"
                  placeholder="https://www.youtube.com/watch?v=…"
                  className="pitch-input w-full mt-2"
                  value={link}
                  onChange={(e) => setLink(e.target.value)}
                  disabled={busy}
                />
              </label>
              {link.trim() && !linkOk && (
                <p className="text-sm text-red-300">
                  Paste a link to a single YouTube video (youtube.com/watch?v=… or youtu.be/…).
                </p>
              )}
              <p className="text-sm text-pitch-muted">
                The analysis server fetches the best version up to 1080p (under 500 MB,
                up to three hours) — nothing is downloaded to your device. Public or
                unlisted videos only. Best results: one fixed, high camera showing the
                whole pitch, at 1080p.
              </p>
              <label className="flex gap-3 items-start text-sm">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={rights}
                  onChange={(e) => setRights(e.target.checked)}
                  disabled={busy}
                />
                <span>
                  I filmed this video or have the owner&apos;s permission to analyse it.
                </span>
              </label>
            </div>
          )}
          <label className="block text-sm">
            Match title
            <input
              aria-label="Analysis title"
              className="pitch-input w-full mt-2"
              maxLength={200}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              disabled={busy}
            />
          </label>
          {needsCode && (
            <label className="block text-sm">
              <span className="flex items-center gap-2">
                <KeyRound size={15} className="text-pitch-green" /> Access code
              </span>
              <input
                aria-label="Access code"
                type="password"
                autoComplete="off"
                className="pitch-input w-full mt-2"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                disabled={busy}
              />
              <span className="text-xs text-pitch-muted block mt-1">
                Analysis runs on paid servers. Ask the Pitchlens owner for the code.
              </span>
            </label>
          )}
          <p className="text-sm text-pitch-muted">
            {health?.gpu
              ? "Analysis runs on a GPU: expect several minutes for a full match (the first run of the day can take a few extra minutes to start). "
              : "On a CPU server, expect analysis to take about as long as the video or longer at the quick setting (a 4-core test took about 1.6 minutes per minute of footage). "}
            The report shows a live time estimate, and you can leave and come
            back. It shows detection coverage and unknown time. Kit groups need your team
            names; automatic pass candidates are estimates. Score, xG and
            physical speed are not yet measured.
          </p>
          <div className="grid sm:grid-cols-2 gap-4">
            <label className="text-sm">Footage type
              <select className="pitch-input w-full mt-2" value={profile}
                disabled={busy} onChange={(e) => setProfile(e.target.value)}>
                <option value="broadcast" disabled={!profiles.includes("broadcast")}>
                  Football-trained models · recommended
                </option>
                <option value="general">General people detector · indoor baseline</option>
              </select>
            </label>
            <label className="text-sm">Analysis detail
              <select className="pitch-input w-full mt-2" value={fps}
                disabled={busy} onChange={(e) => setFps(e.target.value)}>
                <option value="3">Quick · about 3 frames/sec</option>
                <option value="6">Balanced · about 6 frames/sec</option>
                <option value="10">Detailed · about 10 frames/sec</option>
              </select>
            </label>
          </div>
          <p className="text-xs text-pitch-muted">
            Detailed analysis follows fast movement more closely and takes longer.
            The football-trained models come from Roboflow's football example (trained on broadcast matches); try the general detector if a small indoor venue gives poor results.
          </p>
          {error && (
            <p role="alert" className="text-red-300">
              {error}
            </p>
          )}
          <button
            disabled={!ready || !available || busy}
            onClick={submit}
            className="pitch-button-primary w-full py-4"
          >
            {busy ? (
              <>
                <Loader2 size={18} className="animate-spin" />
                {phase === "Uploading" ? `Uploading · ${percent}%` : `${phase}…`}
              </>
            ) : (
              <>
                Analyse video automatically <ScanLine size={18} />
              </>
            )}
          </button>
          {busy && (
            <button
              onClick={() => upload.current?.abort()}
              className="pitch-button-secondary"
            >
              Cancel upload
            </button>
          )}
          <button
            onClick={onManual}
            disabled={busy}
            className="text-sm text-pitch-muted underline"
          >
            Open manual review instead
          </button>
        </div>
      </main>
    </>
  );
}
