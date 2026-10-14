"use client";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { useRouter } from "next/navigation";
import { Upload, ScanLine, Loader2, KeyRound } from "lucide-react";
import { inspectVideo, PreflightRejected, VideoPreflight } from "@/lib/review/preflight";
import { clockTime } from "@/lib/review/vision";
import { Navbar } from "@/components/ui/Navbar";
import { AnimatePresence, motion } from "framer-motion";
import { gradeFootage } from "@/lib/review/footageCheck";
import {
  VisionHealth,
  VisionError,
  DEFAULT_MAX_BYTES,
  analyseYouTube,
  cancelEarlierUpload,
  visionAccessCode,
  visionErrorMessage,
  setVisionAccessCode,
  sizeLabel,
  cleanAccessCode,
  ACCESS_CODE_CHARACTERS,
  CONNECTION_LOST,
  connectionLost,
  defaultVisionOptions,
  pendingUploadFor,
  pendingUploadInfo,
  sameUploadOptions,
  uploadQuery,
} from "@/lib/review/vision";
import {
  UploadEntry,
  currentUpload,
  dismissUpload,
  findUpload,
  inFlight,
  markLeft,
  startUpload,
  subscribeUploads,
  uploadsVersion,
  wakeLockSupported,
} from "@/lib/review/uploadStore";
import { etaSeconds, formatBytes, minutesAt10Mbit, percentLabel } from "@/lib/review/uploadProgress";

/** Bytes as the size people see in Photos or Files ("1.5 MB", "820 MB", "2 GB"). */
function megabytes(bytes: number) {
  if (bytes >= 1024 ** 3) return sizeLabel(bytes);
  const mb = bytes / (1024 * 1024);
  return `${mb >= 10 ? Math.round(mb) : mb.toFixed(1).replace(/\.0$/, "")} MB`;
}

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
  const [title, setTitle] = useState("");
  const [health, setHealth] = useState<VisionHealth | null>(null);
  const [healthAttempt, setHealthAttempt] = useState(0);
  const [checkingServer, setCheckingServer] = useState(false);
  const defaultsApplied = useRef(false);
  const [error, setError] = useState("");
  /** A YouTube link being sent, or an earlier upload being cancelled (uploads live in the store). */
  const [localBusy, setLocalBusy] = useState(false);
  const [localPhase, setLocalPhase] = useState("");
  // Re-render whenever the upload moves on; it lives outside this page, so leaving
  // for another page of the site (navbar links) neither stops nor cancels it.
  const uploadVersion = useSyncExternalStore(subscribeUploads, uploadsVersion, () => 0);
  const [active, setActive] = useState<UploadEntry | null>(null);
  const activeRef = useRef<UploadEntry | null>(null);
  activeRef.current = active;
  const running = inFlight(active);
  // A started upload keeps the page busy until the report opens.
  const busy = localBusy || (!!active && active.state !== "failed");
  const phase = active
    ? active.state === "started"
      ? "Opening the analysis"
      : active.state === "starting"
        ? "Checking the video"
        : active.jobId
          ? "Uploading"
          : "Reserving the analysis server"
    : localPhase;
  const sent = active?.sentBytes ?? 0;
  const total = active?.totalBytes ?? 0;
  const percentText = total > 0 ? percentLabel(sent, total) : "0%";
  const eta = active && phase === "Uploading" ? etaSeconds(active.acks, total) : null;
  const [profile, setProfile] = useState("general");
  const [fps, setFps] = useState("3");
  const [playerLoadOnly, setPlayerLoadOnly] = useState(true);
  const [adaptive, setAdaptive] = useState(false);
  const [preflight, setPreflight] = useState<VideoPreflight | null>(null);
  const [checking, setChecking] = useState(false);
  /** The browser could not decode the file (codec gap or corrupt); the server decides. */
  const [previewFailed, setPreviewFailed] = useState(false);
  const [rejected, setRejected] = useState(false);
  const [diagnostic, setDiagnostic] = useState(false);
  const [start, setStart] = useState(0);
  /** This browser's earlier upload still holds the worker (409 "own-upload"). */
  const [earlier, setEarlier] = useState<{ jobId: string; expectedBytes: number } | null>(null);
  /** An upload this browser started before a reload, not finished yet (named until a file is chosen). */
  const [unfinished, setUnfinished] = useState<ReturnType<typeof pendingUploadInfo>>(null);
  useEffect(() => {
    setPreflight(null);
    setPreviewFailed(false);
    setRejected(false);
    setStart(0);
    if (!file) { setChecking(false); return; }
    const controller = new AbortController();
    setChecking(true);
    inspectVideo(file, controller.signal).then((result) => {
      if (!controller.signal.aborted) setPreflight(result);
    }).catch((e) => {
      if (controller.signal.aborted) return;
      if (e instanceof PreflightRejected) {
        setRejected(true);
        setError(e.message);
      } else {
        // HEVC .mov from a phone in Firefox or Linux Chromium ends here although the
        // analysis server decodes it fine: warn, drop the short test, allow the upload.
        setPreviewFailed(true);
        setDiagnostic(false);
      }
    }).finally(() => { if (!controller.signal.aborted) setChecking(false); });
    return () => controller.abort();
  }, [file]);
  const check = preflight
    ? gradeFootage(preflight.width, preflight.height, preflight.duration, preflight.motion)
    : null;
  // The worker's limit (it can be raised by the operator); 500 MB until /health answers.
  const maxBytes = health?.maxBytes && health.maxBytes > 0 ? health.maxBytes : DEFAULT_MAX_BYTES;
  const tooLarge = !!file && file.size > maxBytes;
  // A hosted worker without a GPU for full matches (an older worker omits fullMatch):
  // only the 20-second test, which needs the decoded preview, can run there.
  const testOnly = health?.fullMatch === false;
  const ready =
    source === "file"
      ? !!file && !checking && !rejected && !tooLarge && (!testOnly || !!preflight)
      : linkOk && rights && !testOnly;
  const [code, setCode] = useState("");
  const [needsCode, setNeedsCode] = useState(false);
  const codeField = useRef<HTMLInputElement>(null);
  /** Bumped when the code is missing or refused: the field is brought into view and focused. */
  const [focusCode, setFocusCode] = useState(0);
  const [codeProblem, setCodeProblem] = useState("");
  useEffect(() => {
    setCode(visionAccessCode());
    // Back on this page while an upload of this tab runs: show it, not the empty form.
    const entry = currentUpload();
    if (inFlight(entry)) {
      markLeft(entry!, false);
      setActive(entry);
    } else if (entry?.state === "failed") {
      showFailure(entry.error);
      dismissUpload(entry);
    } else setUnfinished(pendingUploadInfo());
    return () => {
      // Leaving never stops the upload: only Cancel does. The navbar follows it meanwhile.
      const current = activeRef.current;
      if (inFlight(current)) markLeft(current!, true);
    };
    // Once, on arrival.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // The upload finished, failed or was cancelled (handled once: dismissing it moves the store on).
  const finished = useRef<UploadEntry | null>(null);
  useEffect(() => {
    const entry = activeRef.current;
    if (!entry || inFlight(entry) || finished.current === entry) return;
    finished.current = entry;
    dismissUpload(entry);
    if (entry.state === "started" && entry.jobId) {
      router.push(`/vision/${entry.jobId}`);
      return;
    }
    setActive(null);
    showFailure(entry.error);
    // Only when the upload moves on.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [uploadVersion]);
  useEffect(() => {
    // Only where the site asks for a code (an open site has no field).
    const field = codeField.current;
    if (!focusCode || !field) return;
    field.scrollIntoView({ block: "center" });
    field.focus({ preventScroll: true });
  }, [focusCode]);
  useEffect(() => {
    const controller = new AbortController();
    let stopped = false;
    const deadline = setTimeout(() => controller.abort(), 15000);
    let timer: ReturnType<typeof setTimeout> | undefined;
    setCheckingServer(true);
    const retry = () => {
      if (healthAttempt < 3) timer = setTimeout(() => setHealthAttempt((n) => n + 1), 4000);
    };
    // Read the body even on 503: it says whether the worker is unset, down or local.
    fetch("/api/vision/health", { cache: "no-store", signal: controller.signal })
      .then((r) => r.json().catch(() => ({})))
      .then((x: VisionHealth) => {
        if (stopped) return;
        setHealth({ ...x, available: x.available === true });
        if (!x.available && x.configured !== false) retry();
        if (x.available && !defaultsApplied.current) {
          defaultsApplied.current = true;
          setDiagnostic(x.diagnostics === true);
          // Initial choices only: reconnecting must preserve the user's options.
          setProfile(defaultVisionOptions(x, { playerLoadOnly: true }).profile);
          if (x.gpu) setFps("6");
        }
        // Show the field whenever a code is required (pre-filled with any saved
        // one); forget a saved code when the site is open.
        if (x.accessRequired) setNeedsCode(true);
        else if (x.available) setVisionAccessCode("");
      })
      .catch(() => {
        if (!stopped) { setHealth({ available: false }); retry(); }
      })
      .finally(() => { clearTimeout(deadline); if (!stopped) setCheckingServer(false); });
    return () => { stopped = true; controller.abort(); clearTimeout(timer); clearTimeout(deadline); };
  }, [healthAttempt]);
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
  // The short test needs the decoded preview: without one (a file this browser
  // cannot decode) its checkbox is not even shown, so it can never apply.
  const shortTest = (diagnostic || testOnly) && !!preflight;
  const choices = {
    title: title.trim() || file?.name || "",
    profile,
    fps,
    diagnostic: shortTest,
    start: shortTest ? start : 0,
    search: defaultVisionOptions(health, { playerLoadOnly, adaptive, profile }).search,
  };
  // How long the worker waits for a paused upload: the stall timeout and the give-up message use them.
  const limits = { uploadIdleSeconds: health?.uploadIdleSeconds, uploadAbandonSeconds: health?.uploadAbandonSeconds };
  // The chosen file is this browser's unfinished upload, picked again after a reload.
  const saved = source === "file" && file && ready && !busy ? pendingUploadFor(file) : null;
  const optionsChanged = !!saved && !!file && !sameUploadOptions(uploadQuery(file, choices), saved.query);
  // The worker still holds this browser's earlier upload of this very file.
  const earlierIsThisFile = !!earlier && !!file && earlier.expectedBytes === file.size && !!saved;
  /** What a failed or stopped upload means, in words; a refused code puts the cursor in its field. */
  function showFailure(e: unknown) {
    if (e instanceof VisionError && e.code === "access") {
      setVisionAccessCode("");
      setCode("");
      setNeedsCode(true);
      setFocusCode((n) => n + 1);
    }
    if (e instanceof VisionError && e.code === "own-upload" && typeof e.data?.jobId === "string")
      setEarlier({ jobId: e.data.jobId, expectedBytes: Number(e.data.expectedBytes) });
    setError(
      e instanceof DOMException && e.name === "AbortError"
        ? file
          ? "Upload cancelled. Your file is still selected."
          : "Upload cancelled."
        : connectionLost(e)
          ? CONNECTION_LOST
          : visionErrorMessage(e, "Unable to start analysis"),
    );
  }
  async function submit(resume?: { requestId: string; query: [string, string][] }) {
    if (!ready) return;
    if (health?.accessRequired) {
      if (!cleanAccessCode(code)) {
        setNeedsCode(true);
        setError("Enter the access code first.");
        setFocusCode((n) => n + 1);
        return;
      }
      if (!setVisionAccessCode(code)) {
        setError(ACCESS_CODE_CHARACTERS);
        setFocusCode((n) => n + 1);
        return;
      }
    }
    setError("");
    setEarlier(null);
    if (source === "youtube") {
      setLocalBusy(true);
      setLocalPhase("Sending the link to the analysis server");
      try {
        const job = await analyseYouTube(link.trim(), {
          title: title.trim(),
          profile,
          fps,
          search: playerLoadOnly ? "none" : "exhaustive",
        });
        router.push(`/vision/${job.id}`);
      } catch (e) {
        showFailure(e);
        setLocalBusy(false);
      }
      return;
    }
    if (!file) return;
    // This tab is already sending this file (the page was left and opened again):
    // show that upload instead of a second one on the same job.
    const already = findUpload(file);
    if (already) {
      setActive(already);
      return;
    }
    setUnfinished(null);
    setActive(
      startUpload(
        file,
        resume ? { title: new URLSearchParams(resume.query).get("title") || file.name, resume, ...limits } : { ...choices, ...limits },
      ),
    );
  }
  /** Free the worker from this browser's stopped upload, then try once more. */
  async function replaceEarlier() {
    if (!earlier) return;
    setLocalBusy(true);
    setLocalPhase("Cancelling the earlier upload");
    await cancelEarlierUpload(earlier.jobId);
    setEarlier(null);
    setLocalBusy(false);
    await submit();
  }
  /** Continue the unfinished upload of this file with the choices it was started with. */
  function continueUpload() {
    if (!file) return;
    const resume = pendingUploadFor(file);
    if (!resume) return;
    const query = new URLSearchParams(resume.query);
    setTitle(query.get("title") ?? title);
    setDiagnostic(query.get("diagnostic") === "true");
    setStart(Math.max(0, Number(query.get("start")) || 0));
    if (query.get("profile")) setProfile(query.get("profile")!);
    if (query.get("fps")) setFps(query.get("fps")!);
    const search = query.get("search");
    if (search) {
      setPlayerLoadOnly(search === "none");
      setAdaptive(search === "adaptive");
    }
    submit(resume);
  }
  const unfinishedQuery = unfinished ? new URLSearchParams(unfinished.query) : null;
  const unfinishedStart = Number(unfinishedQuery?.get("start")) || 0;
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
            {/* On a phone the video picker belongs on the first screen: one line here. */}
            <p className="text-pitch-muted sm:hidden">Upload a match and get its stats.</p>
            <p className="text-pitch-muted hidden sm:block">
              Detect players and the ball across the video, follow track IDs,
              separate kit colours and find possible passes and changes of
              possession automatically.
            </p>
          </div>
          <div className="hidden sm:grid sm:grid-cols-3 gap-3">
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
                    Then choose Retry connection below.
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
                    It may be starting up or redeploying. Your selected file stays here
                    while you reconnect.
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
              <button type="button" className="pitch-button-secondary" disabled={checkingServer}
                onClick={() => setHealthAttempt((n) => n + 1)}>
                {checkingServer ? "Checking connection…" : "Retry connection"}
              </button>
            </div>
          )}
          {available && testOnly && (
            <p role="status" className="border border-amber-500/40 bg-amber-500/10 rounded-xl p-4 text-sm" data-testid="test-only">
              Full-match analysis is not set up on this server yet. You can run a 20-second test.
            </p>
          )}
          {unfinished && unfinishedQuery && !file && !busy && source === "file" && (
            <p role="status" className="border border-sky-400/40 bg-sky-400/10 rounded-xl p-4 text-sm" data-testid="unfinished-upload">
              Unfinished upload: &quot;{unfinishedQuery.get("title") || unfinished.name}&quot;
              {unfinishedQuery.get("diagnostic") === "true"
                ? `, test ${clockTime(unfinishedStart)}–${clockTime(unfinishedStart + 20)}`
                : ""}
              . Choose the same video to continue.
            </p>
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
                disabled={busy || (value === "youtube" && testOnly)}
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
          <label className={`block glass-card border-2 border-dashed p-10 text-center cursor-pointer transition-all duration-300 hover:-translate-y-0.5 hover:border-pitch-green/80 hover:shadow-[0_0_40px_-12px_rgba(46,204,113,0.5)] ${file ? "border-pitch-green/80 bg-pitch-green/5" : "border-pitch-green/40"}`}>
            <motion.span
              className="inline-block"
              animate={file ? { scale: [1, 1.15, 1] } : { y: [0, -5, 0] }}
              transition={file ? { duration: 0.4 } : { duration: 2.2, repeat: Infinity, ease: "easeInOut" }}
              key={file ? file.name : "idle"}
            >
              <Upload className="mx-auto text-pitch-green mb-4" />
            </motion.span>
            <span className="block font-semibold break-all">
              {file ? file.name : running && active ? active.fileName : "Choose your match video"}
            </span>
            <span className="text-sm text-pitch-muted block mt-2">
              MP4 or MOV (H.264/HEVC) · Up to {megabytes(maxBytes)} · upload the camera&apos;s original file ·{" "}
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
                // A file over the limit stays selected: its length (once read) says how much fits.
                if (!/\.(mp4|mov)$/i.test(f.name)) {
                  setFile(null);
                  setError("Choose an MP4 or MOV video.");
                  return;
                }
                setFile(f);
                setTitle(f.name.replace(/\.(mp4|mov)$/i, ""));
                setError("");
                setEarlier(null);
                setUnfinished(null);
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
                The analysis server fetches the sharpest version up to 1080p that fits
                (under {megabytes(maxBytes)}, up to three hours) — nothing is downloaded to your device. Public or
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
          {source === "file" && file && tooLarge && (
            <div role="alert" className="border border-amber-500/40 bg-amber-500/10 rounded-xl p-4 text-sm space-y-1" data-testid="too-large">
              <p className="font-semibold">
                This video is {megabytes(file.size)}; this server accepts up to {megabytes(maxBytes)}.
              </p>
              {preflight && preflight.duration > 0 && (
                <p>
                  {(() => {
                    // Minutes of this footage that fit, at this file's own bitrate.
                    const minutes = maxBytes / (file.size / preflight.duration) / 60;
                    return minutes < 1 ? "Less than a minute of it fits." : `About ${Math.floor(minutes)} minute${Math.floor(minutes) === 1 ? "" : "s"} of it fit.`;
                  })()}
                </p>
              )}
              <p>
                In Photos, tap Edit, trim, then Save Video as New Clip; repeat for each part. iMovie can
                also export at 720p.
              </p>
            </div>
          )}
          {source === "file" && checking && (
            <p role="status" className="text-sm text-pitch-muted">
              Checking the video on this device before upload…
            </p>
          )}
          {source === "file" && previewFailed && !checking && (
            <p
              role="alert"
              className="border border-amber-500/40 bg-amber-500/10 rounded-xl p-4 text-sm"
            >
              This browser could not decode the video, so the footage grade and the 20-second
              test are unavailable here.{" "}
              {testOnly
                ? "This server cannot analyse full matches yet: export the video as an H.264 MP4 and choose it again to run the test."
                : "The analysis server decodes files itself (H.264 and HEVC); you can still analyse the full match. If the server rejects it too, export it as an H.264 MP4."}
            </p>
          )}
          <AnimatePresence>
            {source === "file" && preflight && check && (
              <motion.section
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                className="glass-card p-5 space-y-4"
                aria-label="Video readiness"
                aria-live="polite"
              >
                <div className="flex items-center justify-between gap-4">
                  <div>
                    <h2 className="font-semibold">Video checked on your device</h2>
                    <p className="text-sm text-pitch-muted">
                      {preflight.width} × {preflight.height} · {clockTime(preflight.duration)} · ball ≈{" "}
                      {check.ballPixels}px wide · {file ? `${formatBytes(file.size)} to upload` : "No video uploaded yet."}
                    </p>
                    {file && file.size > 300 * 1024 * 1024 && (
                      <p className="text-sm text-amber-200 mt-1">
                        About {minutesAt10Mbit(file.size)} minutes on a 10 Mbit/s connection. Use Wi-Fi if you
                        can; mobile data charges may apply.
                      </p>
                    )}
                  </div>
                  <span
                    className="px-3 py-1 rounded-full text-sm font-bold shrink-0"
                    style={{
                      background:
                        check.grade === "great"
                          ? "#22c55e"
                          : check.grade === "good"
                            ? "#84cc16"
                            : check.grade === "limited"
                              ? "#f59e0b"
                              : "#ef4444",
                      color: "#0b0f1a",
                    }}
                  >
                    {check.score}/100 · {check.grade}
                  </span>
                </div>
                <div className="h-1.5 rounded-full bg-white/10 overflow-hidden">
                  <motion.div
                    className="h-full rounded-full bg-gradient-to-r from-emerald-400 to-sky-400"
                    initial={{ width: 0 }}
                    animate={{ width: `${check.score}%` }}
                    transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1] }}
                  />
                </div>
                <ul className="text-sm text-pitch-muted space-y-1">
                  {check.notes.map((n) => (
                    <li key={n}>· {n}</li>
                  ))}
                  {Math.min(check.width, check.height) <= 480 && !check.notes.some((n) => n.includes("size of a boot")) && (
                    <li className="text-amber-200">
                      · This looks like a reduced copy (shortest side {Math.min(preflight.width, preflight.height)}px): the ball is only about{" "}
                      {check.ballPixels}px across, about the size of a boot, and is easily missed or
                      confused. If you have the camera&apos;s original file (often 1080p), upload that
                      instead: the ball is about three times larger and every frame is analysed at full
                      resolution.
                    </li>
                  )}
                  {check.grade === "great" && check.notes.length <= 1 && (
                    <li>· One fixed, high camera showing the whole pitch gives the best results.</li>
                  )}
                </ul>
                {!tooLarge && (
                <>
                <div className="grid grid-cols-3 gap-2">
                  {preflight.samples.map((sample) => (
                    <button
                      key={sample.t}
                      type="button"
                      disabled={busy}
                      className="text-xs text-left"
                      onClick={() => {
                        setStart(Math.max(0, Math.floor(sample.t)));
                        setDiagnostic(health?.diagnostics === true);
                      }}
                    >
                      {/* Native image: these are local canvas previews, not remote assets. */}
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        src={sample.image}
                        alt={`Video sample at ${clockTime(sample.t)}`}
                        className="rounded w-full"
                      />
                      Test from {clockTime(sample.t)}
                    </button>
                  ))}
                </div>
                <label className="flex gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={shortTest}
                    disabled={busy || !health?.diagnostics || testOnly}
                    onChange={(e) => setDiagnostic(e.target.checked)}
                  />
                  <span>
                    Only analyse a 20-second test
                    <span className="block text-xs text-pitch-muted">
                      {testOnly
                        ? "The only analysis this server offers for now."
                        : "After the test you can analyse the full match from its report, without uploading again."}
                    </span>
                  </span>
                </label>
                {!health?.diagnostics && (
                  <p className="text-sm text-amber-200">
                    This worker needs an update to support short tests. Submitting now analyses
                    the full video.
                  </p>
                )}
                {shortTest && (
                  <label className="block text-sm">
                    Test start (seconds)
                    <input
                      aria-label="Test start (seconds)"
                      type="number"
                      min="0"
                      max={Math.max(0, Math.ceil(preflight.duration) - 1)}
                      step="1"
                      value={start}
                      disabled={busy}
                      className="pitch-input ml-3 w-28"
                      onChange={(e) =>
                        setStart(
                          Math.max(
                            0,
                            Math.min(
                              Math.ceil(preflight.duration) - 1,
                              Math.floor(Number(e.target.value) || 0),
                            ),
                          ),
                        )
                      }
                    />
                  </label>
                )}
                <p className="text-xs text-pitch-muted">
                  {Math.ceil(
                    (shortTest ? Math.min(20, preflight.duration - start) : preflight.duration) *
                      Number(fps),
                  ).toLocaleString()}{" "}
                  frames requested. The short test still transfers the file to the analysis
                  server; only the selected section is analysed. Detection coverage is not an
                  accuracy score.
                </p>
                </>
                )}
              </motion.section>
            )}
          </AnimatePresence>
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
                ref={codeField}
                aria-label="Access code"
                data-access-field
                type="password"
                autoComplete="off"
                className="pitch-input w-full mt-2"
                value={code}
                onChange={(e) => {
                  setCode(e.target.value);
                  // Saved as typed, so returning to a match page works without uploading.
                  setCodeProblem(setVisionAccessCode(e.target.value) ? "" : ACCESS_CODE_CHARACTERS);
                }}
                disabled={busy}
              />
              {codeProblem && (
                <span role="alert" className="text-xs text-red-300 block mt-1">
                  {codeProblem}
                </span>
              )}
              <span className="text-xs text-pitch-muted block mt-1">
                Analysis runs on paid servers. Ask the Pitchlens owner for the code.
              </span>
            </label>
          )}
          <p className="text-sm text-pitch-muted">
            {health?.gpu
              ? "Analysis runs on a GPU (the first run of the day can take a few extra minutes to start). "
              : testOnly
                ? ""
                : "On a CPU server, expect analysis to take about as long as the video or longer at the quick setting (a 4-core test took about 1.6 minutes per minute of footage). "}
            The report shows a live time estimate, and you can leave and come
            back.{" "}
            {/* What this job measures depends on whether the ball model runs. */}
            {playerLoadOnly
              ? "This job estimates how far each kit ran (camera pan removed). Possession and passes need the ball model: untick Player load only. A two-minute pitch setup (or a saved venue) adds heatmaps and positions. Speed and xG are not measured."
              : "You get possession and passes automatically; a two-minute pitch setup (or a saved venue) adds shots, heatmaps and positions. Goals and shots are proposed for you to confirm, and the score comes from you. Speed and xG are not measured; running distance is a per-kit estimate."}
          </p>
          <div className="grid sm:grid-cols-2 gap-4">
            <label className="text-sm">Footage type
              <select className="pitch-input w-full mt-2" value={profile}
                disabled={busy} onChange={(e) => setProfile(e.target.value)}>
                <option value="small-ball" disabled={!profiles.includes("small-ball")}>Small ball / lightweight players · experimental</option>
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
          {source === "file" || source === "youtube" ? (
            <label className="flex gap-2 text-sm">
              <input type="checkbox" checked={playerLoadOnly} disabled={busy} onChange={(e) => setPlayerLoadOnly(e.target.checked)} />
              Player load only: running distance per team, quicker. Possession, passes and shots need the ball model.
            </label>
          ) : null}
          {source === "file" && profile !== "general" && !playerLoadOnly && <label className="flex gap-2 text-sm"><input type="checkbox" checked={adaptive} disabled={busy} onChange={(e) => setAdaptive(e.target.checked)} />Experimental faster ball search; compare a short test first.</label>}
          <p className="text-xs text-pitch-muted">
            Detailed analysis follows fast movement more closely and takes longer.
            The football-trained models come from Roboflow&apos;s football example (trained on broadcast matches); try the general detector if a small indoor venue gives poor results.
          </p>
          {error && (
            <p role="alert" className="text-red-300">
              {error}
            </p>
          )}
          {earlier && !busy && (
            <div className="flex flex-wrap gap-3">
              {earlierIsThisFile && (
                <button type="button" className="pitch-button-primary" onClick={continueUpload}>
                  Continue this upload
                </button>
              )}
              <button type="button" className="pitch-button-secondary" onClick={replaceEarlier}>
                Cancel the earlier upload
              </button>
            </div>
          )}
          {optionsChanged && !earlier ? (
            // The same video as the unfinished upload, with other choices: continuing
            // keeps what was sent; the new choices would start from the first byte.
            <div className="grid sm:grid-cols-2 gap-3">
              <button disabled={!available} onClick={continueUpload} className="pitch-button-primary w-full py-4">
                Continue this upload
              </button>
              <button disabled={!available} onClick={() => submit()} className="pitch-button-secondary w-full py-4">
                Start again with these options
              </button>
            </div>
          ) : (
          <button
            disabled={!ready || !available || busy}
            onClick={() => submit()}
            className="pitch-button-primary w-full py-4"
          >
            {busy ? (
              <>
                <Loader2 size={18} className="animate-spin" />
                {phase === "Uploading" ? `Uploading · ${percentText}` : `${phase}…`}
              </>
            ) : (
              <>
                Analyse video automatically <ScanLine size={18} />
              </>
            )}
          </button>
          )}
          <AnimatePresence>
            {busy && (
              <motion.div
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: "auto" }}
                exit={{ opacity: 0, height: 0 }}
                className="glass-card p-5 space-y-3 overflow-hidden"
                aria-live="polite"
              >
                <div className="flex items-baseline justify-between">
                  <span className="text-sm text-pitch-muted">
                    {phase === "Uploading"
                      ? "Sending your video in secure 4 MB pieces"
                      : `${phase}…`}
                  </span>
                  <span className="text-2xl font-black tabular-nums">{percentText}</span>
                </div>
                <div className="relative h-2.5 rounded-full bg-white/10 overflow-hidden">
                  <motion.div
                    className="absolute inset-y-0 left-0 rounded-full bg-gradient-to-r from-emerald-400 to-sky-400"
                    animate={{ width: `${Math.max(3, total > 0 ? (sent / total) * 100 : 0)}%` }}
                    transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
                  />
                  <motion.div
                    className="absolute inset-y-0 w-1/3 bg-gradient-to-r from-transparent via-white/40 to-transparent"
                    animate={{ left: ["-35%", "110%"] }}
                    transition={{ duration: 1.6, repeat: Infinity, ease: "linear" }}
                  />
                </div>
                {phase === "Uploading" && total > 0 && (
                  <p className="text-sm tabular-nums" data-testid="upload-bytes">
                    {formatBytes(sent)} of {formatBytes(total)}
                    {eta !== null ? ` · about ${Math.max(1, Math.round(eta / 60))} min left` : ""}
                  </p>
                )}
                <p className="text-xs text-pitch-muted">
                  {wakeLockSupported()
                    ? "Keep this page open until the upload finishes; we keep the screen awake while it uploads."
                    : "Keep this page open and the screen on until the upload finishes."}{" "}
                  After that the analysis runs on the server and you can leave.
                </p>
                {/* Once the video is being checked the analysis may already be running:
                    the worker is asked to stop it, and a run it could not stop opens. */}
                {running && (
                  <button
                    onClick={() => active?.controller.abort()}
                    className="pitch-button-secondary"
                  >
                    {phase === "Checking the video" ? "Cancel analysis" : "Cancel upload"}
                  </button>
                )}
              </motion.div>
            )}
          </AnimatePresence>
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
