"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { Navbar } from "@/components/ui/Navbar";
import {
  VisionResult,
  VisionJob,
  visionJson,
  VisionError,
  clockTime,
  rerunSavedVision,
} from "@/lib/review/vision";
import { MatchCentre } from "@/components/vision/MatchCentre";
import { AnalysisWait } from "@/components/vision/AnalysisWait";
import { matchStats } from "@/lib/review/visionStats";
import { MatchReport } from "@/components/vision/MatchReport";
import { ReviewQueue } from "@/components/vision/ReviewQueue";
import { BallLabeller } from "@/components/vision/BallLabeller";
import { PitchCalibration, CalibrationOverlay } from "@/components/vision/PitchCalibration";
import { MiniPitch } from "@/components/vision/PitchGraphics";
import {
  Analysis,
  Calibration,
  fetchAnalysis,
  fetchCalibration,
  pitchLines,
  pitchToImage,
} from "@/lib/review/analysis";

export function VisionReport({ jobId }: { jobId: string }) {
  const [job, setJob] = useState<VisionJob | null>(null);
  const [result, setResult] = useState<VisionResult | null>(null);
  const [error, setError] = useState("");
  const [retrying, setRetrying] = useState(false);
  const [rerunning, setRerunning] = useState(false);
  const [testStart, setTestStart] = useState(0);
  const rerunRequest = useRef<{ key: string; id: string } | null>(null);
  const [time, setTime] = useState(0);
  const [overlay, setOverlay] = useState(true);
  const [names, setNames] = useState(["Kit A", "Kit B"]);
  const [filter, setFilter] = useState("all");
  const player = useRef<HTMLVideoElement>(null);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [calibration, setCalibration] = useState<Calibration | null>(null);
  const [analysisError, setAnalysisError] = useState("");
  const [calibrating, setCalibrating] = useState(false);
  const [calOverlay, setCalOverlay] = useState<CalibrationOverlay | null>(null);
  const [calClick, setCalClick] = useState<{ x: number; y: number; n: number; moved?: boolean } | null>(null);
  const [showLines, setShowLines] = useState(true);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [labelling, setLabelling] = useState(false);
  const clipEnd = useRef<number | null>(null);
  const reviewRef = useRef<HTMLDivElement>(null);
  const calibrationRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let stopped = false;
    let loaded = false;
    let terminal = false;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function read() {
      try {
        const next = await visionJson<VisionJob>(`jobs/${jobId}`, {
          signal: controller.signal,
        });
        if (stopped) return;
        terminal = ["failed", "cancelled", "interrupted"].includes(next.status);
        setJob(next);
        setError("");
        if (next.status === "completed" && !loaded) {
          const data = await visionJson<VisionResult>(`jobs/${jobId}/result`, {
            signal: controller.signal,
          });
          if (stopped) return;
          setResult(data);
          setTime(data.analysedStart ?? 0);
          setTestStart(Math.floor(data.analysedStart ?? 0));
          loaded = true;
        }
      } catch (e) {
        if (!stopped) {
          if (e instanceof VisionError && e.status === 404) {
            // Permanent: wrong link, or another browser's analysis on a local worker.
            terminal = true;
            setError("This analysis was not found on the vision worker.");
          } else
            setError(e instanceof Error ? e.message : "Unable to read analysis");
        }
      } finally {
        if (!stopped && !loaded && !terminal) timer = setTimeout(read, 2000);
      }
    }
    read();
    try {
      const saved = JSON.parse(
        localStorage.getItem(`vision-names-${jobId}`) || "null",
      );
      if (
        Array.isArray(saved) &&
        saved.length === 2 &&
        saved.every((x) => typeof x === "string")
      )
        setNames(saved);
    } catch {}
    return () => {
      stopped = true;
      controller.abort();
      clearTimeout(timer);
    };
  }, [jobId]);
  async function rerun(mode: "section" | "full") {
    const start = mode === "full" ? 0 : testStart;
    const key = `${jobId}:${mode}:${start}`;
    if (rerunRequest.current?.key !== key) rerunRequest.current = { key, id: crypto.randomUUID() };
    setRerunning(true);
    setError("");
    try {
      const next = await rerunSavedVision(jobId, mode, start, rerunRequest.current.id);
      window.location.assign(`/vision/${next.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to analyse saved footage");
      setRerunning(false);
    }
  }
  const loadAnalytics = useCallback(async () => {
    try {
      const [a, c] = await Promise.all([fetchAnalysis(jobId), fetchCalibration(jobId)]);
      setAnalysis(a);
      setCalibration(c);
      setAnalysisError("");
    } catch (e) {
      // Older workers have no analytics endpoints: keep the classic report.
      setAnalysis(null);
      if (!(e instanceof VisionError && e.status === 404))
        setAnalysisError(e instanceof Error ? e.message : "Match analytics are unavailable");
    }
  }, [jobId]);
  useEffect(() => {
    if (result) loadAnalytics();
  }, [result, loadAnalytics]);
  useEffect(() => {
    const video = player.current;
    if (!video || !result) return;
    // Native timeupdate can fire only a few times/second; align boxes to decoded frames.
    let callback = 0;
    let stopped = false;
    const update: VideoFrameRequestCallback = (_, metadata) => {
      if (stopped) return;
      setTime(metadata.mediaTime);
      callback = video.requestVideoFrameCallback(update);
    };
    if (typeof video.requestVideoFrameCallback !== "function") return;
    callback = video.requestVideoFrameCallback(update);
    return () => {
      stopped = true;
      video.cancelVideoFrameCallback(callback);
    };
  }, [result]);
  // Binary lookup keeps playback cheap even for a full-length match.
  const frameIndex = useMemo(() => {
    if (!result || !result.frames.length) return -1;
    let l = 0,
      r = result.frames.length - 1;
    while (l < r) {
      const m = Math.ceil((l + r) / 2);
      if (result.frames[m].t <= time) l = m;
      else r = m - 1;
    }
    return Math.abs(result.frames[l].t - time) <= (1 / result.sampleFps) * 1.5 ? l : -1;
  }, [result, time]);
  const frame = frameIndex >= 0 && result ? result.frames[frameIndex] : null;
  // Painted pitch lines projected through this frame's calibration (a live check of its accuracy).
  const projectedLines = useMemo(() => {
    if (!showLines || calibrating || !calibration || calibration.state !== "ready" || !calibration.template || frameIndex < 0) return null;
    const entry = calibration.frames?.[frameIndex];
    if (!entry || !calibration.size) return null;
    return pitchLines(calibration.template, 0.5).map((line) => pitchToImage(entry.H, calibration.k1 ?? 0, calibration.size!, line));
  }, [showLines, calibrating, calibration, frameIndex]);
  const events = useMemo(
    () =>
      result?.metrics.events.filter(
        (e) => filter === "all" || e.type === filter,
      ) || [],
    [result, filter],
  );
  function seek(t: number) {
    if (player.current) {
      clipEnd.current = null;
      player.current.currentTime = t;
      player.current.pause();
      player.current.scrollIntoView({ behavior: "smooth", block: "center" });
      setTime(t);
    }
  }
  const watchClip = useCallback((t: number, until: number) => {
    const video = player.current;
    if (!video) return;
    clipEnd.current = until;
    video.currentTime = t;
    setTime(t);
    video.play().catch(() => {});
  }, []);
  function videoClick(e: React.MouseEvent<SVGSVGElement>) {
    if (!calibrating || !result) return;
    const box = e.currentTarget.getBoundingClientRect();
    const x = ((e.clientX - box.left) / box.width) * result.video.width;
    const y = ((e.clientY - box.top) / box.height) * result.video.height;
    player.current?.pause();
    // Pitch setup is anchored to an analysed frame (a moving camera is elsewhere
    // a moment later): off one, move to the nearest and ask for the click again.
    const now = player.current?.currentTime ?? time;
    const nearest = result.frames.length ? result.frames[nearestFrame(result.frames, now)].t : now;
    const moved = Math.abs(nearest - now) > 0.5 / (result.video.fps || 30) + 0.005;
    if (moved) snapTo(nearest);
    setCalClick((c) => ({ x: Math.round(x * 10) / 10, y: Math.round(y * 10) / 10, n: (c?.n ?? 0) + 1, moved }));
  }
  function snapTo(t: number) {
    const v = player.current;
    if (!v) return;
    v.pause();
    clipEnd.current = null;
    v.currentTime = t;
    setTime(t);
  }
  const startCalibration = () => {
    setCalibrating(true);
    // Adjusting a saved setup returns to the frame it was clicked on.
    const savedAt = calibration?.request?.t;
    if (result && result.frames.length) snapTo(result.frames[nearestFrame(result.frames, savedAt ?? time)].t);
    calibrationRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  const openReview = () => {
    setReviewOpen(true);
    setTimeout(() => reviewRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
  };
  function rename(index: number, value: string) {
    const next = [...names];
    next[index] = value;
    setNames(next);
    try {
      localStorage.setItem(`vision-names-${jobId}`, JSON.stringify(next));
    } catch {
      setError("Team names could not be saved on this browser.");
    }
  }
  const colour = (team: number) => result?.teams[team]?.colour || "#cbd5e1";
  const stats = useMemo(() => (result ? matchStats(result) : null), [result]);
  return (
    <>
      <Navbar />
      <main className="pt-24 pb-16 px-4">
        <div className="max-w-7xl mx-auto space-y-6">
          <Link href="/dashboard" className="text-sm text-pitch-muted">
            ← Your matches
          </Link>
          <header className="flex flex-wrap justify-between gap-4">
            <div>
              <p className="text-pitch-green text-xs uppercase tracking-widest mb-2">
                Computer vision report
              </p>
              <h1 className="text-3xl font-bold break-words">
                {job?.title || "Opening analysis…"}
              </h1>
              <p className="text-pitch-muted mt-2">
                {result
                  ? `${clockTime(result.analysedDuration)} analysed · ${result.metrics.sampledFrames.toLocaleString()} frames · ${result.model}${result.ballModel ? ` + ${result.ballModel}` : ""}`
                  : "Video analysis runs on the vision worker. You can leave this page and return."}
              </p>
            </div>
            {result && (
              <a
                className="pitch-button-secondary self-start"
                href={`/api/vision/jobs/${jobId}/result`}
                download
              >
                Export detection data
              </a>
            )}
          </header>
          {result?.performance && <div className="glass-card p-4 text-sm space-y-2">
            <p>Processing took {result.performance.totalSeconds.toFixed(1)}s on {result.performance.device} · players {result.performance.playerInferenceSeconds.toFixed(1)}s · ball {result.performance.ballInferenceSeconds.toFixed(1)}s.</p>
            <p>{(result.performance.totalSeconds / result.analysedDuration).toFixed(1)} seconds processing per second of footage. Full matches may take a different amount of time.</p>
            {result.analysedDuration < result.video.duration - .5 && <p className="text-amber-200">Diagnostic section: {clockTime(result.analysedStart ?? 0)}–{clockTime((result.analysedStart ?? 0) + result.analysedDuration)}. These results do not describe the full match.</p>}
          </div>}
          {result && !job?.videoDeleted && (
            <section className="glass-card p-4 space-y-3" aria-label="Analyse saved footage">
              <p className="text-sm text-pitch-muted">Test a section with visible play or analyse the full match. Your saved video is reused; this report stays available.</p>
              <div className="flex flex-wrap items-center gap-3">
                <label className="text-sm">Test start (seconds)
                  <input aria-label="Saved video test start" type="number" min="0"
                    max={Math.max(0, Math.ceil(result.video.duration) - 1)} step="1"
                    value={testStart} disabled={rerunning} className="pitch-input ml-2 w-24"
                    onChange={(e) => setTestStart(Math.max(0, Math.min(Math.ceil(result.video.duration) - 1, Math.floor(Number(e.target.value) || 0))))} />
                </label>
                <button className="pitch-button-secondary text-sm" disabled={rerunning}
                  onClick={() => setTestStart(Math.max(0, Math.min(Math.ceil(result.video.duration) - 1, Math.floor(time))))}>
                  Use playback position
                </button>
                <button className="pitch-button-secondary text-sm" disabled={rerunning} onClick={() => rerun("section")}>Test 20 seconds</button>
                <button className="pitch-button-primary text-sm" disabled={rerunning} onClick={() => rerun("full")}>Analyse full match</button>
                {rerunning && <span role="status" className="text-sm">Starting from saved footage…</span>}
              </div>
            </section>
          )}
          {error && (
            <p role="alert" className="text-red-300 glass-card p-4">
              {error}
            </p>
          )}
          {job && job.status !== "completed" && (
            <AnalysisWait
              job={job}
              onCancel={() =>
                visionJson(`jobs/${jobId}/cancel`, { method: "POST" }).catch((e) =>
                  setError(e.message),
                )
              }
            />
          )}
          {job && ["failed", "interrupted", "cancelled"].includes(job.status) && !job.videoDeleted && (
            <button className="pitch-button-primary" disabled={retrying} onClick={async () => {
              setRetrying(true);
              try {
                await visionJson(`jobs/${jobId}/retry`, { method: "POST" });
                window.location.reload();
              } catch (e) {
                setError(e instanceof Error ? e.message : "Retry failed");
                setRetrying(false);
              }
            }}>{retrying ? "Starting retry…" : "Retry saved upload"}</button>
          )}
          {result && (
            <>
              {result.metrics.ballFrames === 0 && (
                <section role="status" className="glass-card border-amber-400/40 p-5 space-y-2">
                  <h2 className="font-semibold text-amber-200">Ball tracking unavailable for this section</h2>
                  <p className="text-sm text-pitch-muted">
                    No ball track was retained. Automatic possession,
                    passes and shots cannot be established from these detections. Inspect the
                    overlays before relying on the report or processing more footage.
                  </p>
                  <button className="pitch-button-secondary text-sm" onClick={() => seek(result.analysedStart ?? 0)}>
                    Inspect detections
                  </button>
                </section>
              )}
              {result.metrics.ballFrames > 0 && analysis?.stats.coverage.controlPercent === 0 && (
                <section role="status" className="glass-card border-amber-400/40 p-5 space-y-2">
                  <h2 className="font-semibold text-amber-200">Ball detections did not establish possession</h2>
                  <p className="text-sm text-pitch-muted">A high detection count can include background objects. No reliable player control was established in this section. Inspect the ball overlay and test another section with visible play before running the full match.</p>
                  <button className="pitch-button-secondary text-sm" onClick={() => seek(result.analysedStart ?? 0)}>Inspect detections</button>
                </section>
              )}
              {analysis ? (
                <MatchReport
                  analysis={analysis}
                  names={names}
                  colours={[colour(0), colour(1)]}
                  duration={result.analysedDuration}
                  start={result.analysedStart ?? 0}
                  onSeek={seek}
                  onCalibrate={startCalibration}
                  onReview={openReview}
                />
              ) : (
                <MatchCentre
                  result={result}
                  stats={stats!}
                  names={names}
                  colours={[colour(0), colour(1)]}
                  onSeek={seek}
                />
              )}
              {analysisError && (
                <p className="text-sm text-amber-200 glass-card p-4" role="status">
                  Match analytics could not be loaded ({analysisError}). The classic report is shown.
                </p>
              )}
              <div className="grid lg:grid-cols-[minmax(0,2fr)_minmax(280px,1fr)] gap-6">
                <div className="space-y-4">
                  {job?.videoDeleted && (
                    <p className="glass-card p-4 text-sm text-pitch-muted" role="status">
                      The uploaded footage was deleted by the server&apos;s retention
                      policy. Measurements and timestamps below remain; detection
                      overlays need the video.
                    </p>
                  )}
                  <div
                    className="relative bg-black rounded-2xl overflow-hidden"
                    style={{
                      aspectRatio: `${result.video.width}/${result.video.height}`,
                      display: job?.videoDeleted ? "none" : undefined,
                    }}
                  >
                    <video
                      ref={player}
                    onLoadedMetadata={() => { if (player.current) player.current.currentTime = result?.analysedStart ?? 0; }}
                      controls
                      playsInline
                      preload="metadata"
                      className="w-full h-full"
                      src={job?.videoDeleted ? undefined : `/api/vision/jobs/${jobId}/video`}
                      onTimeUpdate={(e) => {
                        const v = e.currentTarget;
                        setTime(v.currentTime);
                        if (clipEnd.current !== null && v.currentTime >= clipEnd.current) {
                          clipEnd.current = null;
                          v.pause();
                        }
                      }}
                      onSeeked={(e) => setTime(e.currentTarget.currentTime)}
                      onError={() =>
                        !job?.videoDeleted &&
                        setError(
                          "Video playback failed. This browser may not support its codec. Use H.264 MP4.",
                        )
                      }
                    />
                    {(calibrating || projectedLines) && (
                      <svg
                        aria-label={calibrating ? "Click pitch landmarks on the video" : "Projected pitch lines"}
                        className={`absolute inset-0 w-full h-full ${calibrating ? "cursor-crosshair" : "pointer-events-none"}`}
                        viewBox={`0 0 ${result.video.width} ${result.video.height}`}
                        onClick={videoClick}
                        data-testid="calibration-overlay"
                      >
                        {splitAtGaps(calibrating ? calOverlay?.lines?.map((l) => l.map((p) => p as [number, number] | null)) : projectedLines).map((line, i) => (
                          <polyline
                            key={i}
                            points={line.map(([x, y]) => `${x},${y}`).join(" ")}
                            fill="none"
                            stroke={calibrating ? "#f43f5e" : "rgba(56,189,248,0.8)"}
                            strokeWidth={calibrating ? 1.5 : 1.2}
                          />
                        ))}
                        {calibrating &&
                          calOverlay?.points.map((p) => (
                            <g key={p.name}>
                              <circle cx={p.x} cy={p.y} r={5} fill="none" stroke="#f8ef3d" strokeWidth={1.5} />
                              <circle cx={p.x} cy={p.y} r={1} fill="#f8ef3d" />
                              <text x={p.x + 6} y={p.y - 6} fontSize={11} fill="#f8ef3d" stroke="#111" strokeWidth={0.3}>
                                {p.label}
                              </text>
                            </g>
                          ))}
                        {calibrating &&
                          calOverlay?.linePoints.map((p, i) => (
                            <rect key={i} x={p.x - 2.5} y={p.y - 2.5} width={5} height={5} fill="#38bdf8" stroke="#111" strokeWidth={0.5} />
                          ))}
                      </svg>
                    )}
                    {overlay && frame && !calibrating && (
                      <svg
                        aria-label="Computer vision detection overlay"
                        className="absolute inset-0 w-full h-full pointer-events-none"
                        viewBox={`0 0 ${result.video.width} ${result.video.height}`}
                      >
                        {frame.players.map((p) => (
                          <g key={p.id}>
                            <rect
                              x={p.box[0]}
                              y={p.box[1]}
                              width={p.box[2] - p.box[0]}
                              height={p.box[3] - p.box[1]}
                              fill="none"
                              stroke={colour(p.team)}
                              strokeWidth="2"
                            />
                            <rect
                              x={p.box[0]}
                              y={Math.max(0, p.box[1] - 13)}
                              width="36"
                              height="13"
                              fill={colour(p.team)}
                            />
                            <text
                              x={p.box[0] + 2}
                              y={Math.max(10, p.box[1] - 3)}
                              fontSize="10"
                              fill="black"
                            >
                              #{p.id}
                            </text>
                          </g>
                        ))}
                        {frame.ball && (
                          <>
                            <circle
                              cx={frame.ball.x}
                              cy={frame.ball.y}
                              r="8"
                              fill="none"
                              stroke="#f8ef3d"
                              strokeWidth="2"
                              strokeDasharray={frame.ball.inferred ? "3 3" : undefined}
                              opacity={frame.ball.inferred ? 0.7 : 1}
                            />
                            <text
                              x={frame.ball.x + 9}
                              y={frame.ball.y}
                              fill="#f8ef3d"
                              stroke="#111"
                              strokeWidth=".3"
                              fontSize="10"
                            >
                              {frame.ball.inferred ? "ball (inferred)" : "ball"}
                            </text>
                          </>
                        )}
                      </svg>
                    )}
                  </div>
                  <div className="flex flex-wrap gap-4 items-center justify-between text-sm">
                    <label className="flex gap-2">
                      <input
                        type="checkbox"
                        checked={overlay}
                        onChange={(e) => setOverlay(e.target.checked)}
                      />
                      Detection overlay
                    </label>
                    {calibration?.state === "ready" && (
                      <label className="flex gap-2">
                        <input type="checkbox" checked={showLines} onChange={(e) => setShowLines(e.target.checked)} />
                        Pitch lines
                      </label>
                    )}
                    <p className="text-pitch-muted">
                      {clockTime(time)} · {frame?.players.length ?? 0} visible
                      tracks ·{" "}
                      {frame?.ball
                        ? frame.ball.inferred
                          ? "ball inferred between observations"
                          : "ball detected"
                        : "ball not observed"}
                    </p>
                  </div>
                  <p className="text-xs text-pitch-muted">
                    Boxes update at {result.sampleFps.toFixed(1)} analysed
                    frames/sec. IDs identify track fragments, not named players.
                    Unknown kit colours appear grey.
                  </p>
                  <DetectionTimeline result={result} onSeek={seek} />
                </div>
                <aside className="space-y-4">
                  {analysis !== null && (
                    <div ref={calibrationRef}>
                      <PitchCalibration
                        jobId={jobId}
                        videoSize={[result.video.width, result.video.height]}
                        time={time}
                        click={calClick}
                        active={calibrating}
                        onActive={(on) => (on ? startCalibration() : setCalibrating(false))}
                        onOverlay={setCalOverlay}
                        calibration={calibration}
                        onApplied={loadAnalytics}
                        videoAvailable={!job?.videoDeleted}
                        onStep={(delta) => {
                          // Step between analysed frames: calibration is anchored to one of them.
                          const step = Math.sign(delta) * Math.max(1, Math.round(Math.abs(delta) * result.sampleFps));
                          const from = nearestFrame(result.frames, time);
                          const target = result.frames[Math.min(result.frames.length - 1, Math.max(0, from + step))];
                          if (target) snapTo(target.t);
                        }}
                      />
                    </div>
                  )}
                  {analysis?.calibrated && (
                    <section className="glass-card p-4 space-y-2">
                      <h2 className="text-sm font-semibold">Pitch view</h2>
                      <MiniPitch analysis={analysis} time={time} colours={[colour(0), colour(1)]} />
                    </section>
                  )}
                  <section className="glass-card p-5 space-y-4">
                    <h2 className="font-semibold">Detected kit groups</h2>
                    <p className="text-sm text-pitch-muted">
                      Match each automatically discovered colour to the correct
                      team.
                    </p>
                    {result.teams.map((team, i) => (
                      <label key={team.id} className="flex gap-3 items-center">
                        <span
                          className="w-6 h-6 rounded-full border border-white/40 shrink-0"
                          style={{ background: team.colour }}
                        />
                        <input
                          aria-label={`Name for kit ${i === 0 ? "A" : "B"}`}
                          maxLength={80}
                          value={names[i]}
                          onChange={(e) => rename(i, e.target.value)}
                          className="pitch-input w-full min-w-0"
                        />
                      </label>
                    ))}
                  </section>
                </aside>
              </div>
              {analysis && (
                <div ref={reviewRef}>
                  {reviewOpen ? (
                    <ReviewQueue
                      jobId={jobId}
                      analysis={analysis}
                      names={names}
                      colours={[colour(0), colour(1)]}
                      time={time}
                      onWatch={watchClip}
                      onAnalysis={(next) =>
                        // Review responses leave out the per-frame positions (unchanged).
                        setAnalysis((prev) => ({ ...next, positions: next.positions ?? prev?.positions ?? null }))
                      }
                    />
                  ) : (
                    <section className="glass-card p-5 flex flex-wrap items-center justify-between gap-3">
                      <div>
                        <h2 className="text-xl font-semibold">Review moments</h2>
                        <p className="text-sm text-pitch-muted">
                          {analysis.review.pending} automatic moments wait for a quick check. Confirmed goals set the score.
                        </p>
                      </div>
                      <button className="pitch-button-primary" onClick={openReview}>
                        Start reviewing
                      </button>
                    </section>
                  )}
                </div>
              )}
              {analysis && !job?.videoDeleted && (
                <section className="glass-card p-5 space-y-3">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <h2 className="text-xl font-semibold">Ball accuracy and training</h2>
                      <p className="text-sm text-pitch-muted max-w-2xl">
                        Show Pitchlens where the ball is on frames from across this match. It measures how often the
                        ball was found, and the answers train a better ball finder for footage like yours.
                      </p>
                    </div>
                    <button className="pitch-button-secondary" onClick={() => setLabelling((v) => !v)}>
                      {labelling ? "Close" : "Label the ball"}
                    </button>
                  </div>
                  {labelling && <BallLabeller jobId={jobId} />}
                </section>
              )}
              {!analysis && (
              <section className="glass-card p-5 space-y-4">
                <div className="flex flex-wrap gap-3 justify-between">
                  <h2 className="text-xl font-semibold">
                    Automatic event candidates
                  </h2>
                  <select
                    aria-label="Filter automatic events"
                    className="pitch-input"
                    value={filter}
                    onChange={(e) => setFilter(e.target.value)}
                  >
                    <option value="all">All candidates</option>
                    <option value="pass-candidate">Possible passes</option>
                    <option value="turnover-candidate">
                      Possible turnovers
                    </option>
                  </select>
                </div>
                <p className="text-sm text-pitch-muted">
                  A candidate requires consecutive ball-to-player observations
                  and a visible transfer. Click to inspect the footage. These
                  are not verified match totals.
                </p>
                {events.length ? (
                  <div className="max-h-96 overflow-auto divide-y divide-white/10">
                    {events.map((e) => (
                      <button
                        key={e.id}
                        onClick={() => seek(Math.max(0, e.t - 2))}
                        className="flex w-full text-left gap-4 justify-between py-3 hover:bg-white/5"
                      >
                        <span className="font-mono text-pitch-green">
                          {clockTime(e.t)}
                        </span>
                        <span className="flex-1">
                          {e.type === "pass-candidate"
                            ? "Possible pass"
                            : "Possible turnover"}{" "}
                          · {names[e.team]}
                          <span className="block text-xs text-pitch-muted">
                            Track #{e.from} → #{e.to}
                          </span>
                        </span>
                        <span className="text-xs text-pitch-muted">
                          Inspect →
                        </span>
                      </button>
                    ))}
                  </div>
                ) : (
                  <p className="py-6 text-pitch-muted">
                    No sufficiently continuous ball transfers were detected.
                    Missing evidence is not counted as zero passes in the match.
                  </p>
                )}
              </section>
              )}
              <section className="glass-card p-5 space-y-3">
                <h2 className="text-xl font-semibold">
                  Measurement boundaries
                </h2>
                <ul className="list-disc pl-5 text-sm text-pitch-muted space-y-2">
                  {result.limitations.map((x) => (
                    <li key={x}>{x}</li>
                  ))}
                </ul>
                <p className="text-xs text-pitch-muted break-all">
                  Player model checksum: {result.modelSha256}
                  {result.ballModelSha256 && <><br />Ball model checksum: {result.ballModelSha256}</>}
                </p>
              </section>
            </>
          )}
        </div>
      </main>
    </>
  );
}
/** Split polylines where points are missing (behind the camera) instead of joining across. */
function splitAtGaps(lines: (([number, number] | null)[] | undefined)[] | null | undefined): [number, number][][] {
  const out: [number, number][][] = [];
  for (const line of lines || []) {
    let run: [number, number][] = [];
    for (const p of line || []) {
      const ok = !!p && Number.isFinite(p[0]) && Number.isFinite(p[1]) && Math.abs(p[0]) < 1e4 && Math.abs(p[1]) < 1e4;
      if (ok) run.push(p as [number, number]);
      else {
        if (run.length > 1) out.push(run);
        run = [];
      }
    }
    if (run.length > 1) out.push(run);
  }
  return out;
}

function nearestFrame(frames: { t: number }[], t: number) {
  let l = 0;
  let r = frames.length - 1;
  while (l < r) {
    const m = Math.floor((l + r) / 2);
    if (frames[m].t < t) l = m + 1;
    else r = m;
  }
  if (l > 0 && Math.abs(frames[l - 1].t - t) <= Math.abs(frames[l].t - t)) return l - 1;
  return l;
}

function DetectionTimeline({
  result,
  onSeek,
}: {
  result: VisionResult;
  onSeek: (time: number) => void;
}) {
  const bins = useMemo(
    () =>
      Array.from({ length: 80 }, (_, i) => {
        const start = (result.analysedStart ?? 0) + (result.analysedDuration * i) / 80,
          end = (result.analysedStart ?? 0) + (result.analysedDuration * (i + 1)) / 80;
        const frames = result.frames.filter((f) => f.t >= start && f.t < end);
        return {
          start,
          rate: frames.length
            ? frames.filter((f) => f.ball && !f.ball.inferred).length / frames.length
            : 0,
        };
      }),
    [result],
  );
  return (
    <section className="glass-card p-4">
      <h2 className="font-semibold text-sm mb-2">
        Ball visibility across the video
      </h2>
      <div className="flex items-end h-16 gap-px">
        {bins.map((b, i) => (
          <button
            key={i}
            title={`${clockTime(b.start)} · ${Math.round(b.rate * 100)}% ball coverage`}
            aria-label={`Seek to ${clockTime(b.start)}`}
            onClick={() => onSeek(b.start)}
            className="flex-1 min-w-0 bg-pitch-green hover:bg-white"
            style={{
              height: `${Math.max(5, b.rate * 100)}%`,
              opacity: b.rate ? 1 : 0.2,
            }}
          />
        ))}
      </div>
      <div className="flex justify-between text-xs text-pitch-muted mt-2">
        <span>{clockTime(result.analysedStart ?? 0)}</span>
        <span>Click a bar to inspect</span>
        <span>{clockTime((result.analysedStart ?? 0) + result.analysedDuration)}</span>
      </div>
    </section>
  );
}
