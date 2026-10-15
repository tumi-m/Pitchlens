"use client";
import { CSSProperties, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { CheckCircle2, ChevronDown } from "lucide-react";
import { Navbar } from "@/components/ui/Navbar";
import {
  VisionResult,
  VisionJob,
  visionJson,
  VisionError,
  clockTime,
  rerunSavedVision,
  isMatchDeleted,
  visionErrorMessage,
  saveVisionDetails,
  matchDateLabel,
  getTeams,
  saveTeams,
  TeamCrop,
  TeamGroups,
  TeamPick,
} from "@/lib/review/vision";
import { analysedFrameTime, frameIndexAt, nearestFrame, seekTarget } from "@/lib/review/frames";
import { buildReportExport } from "@/lib/review/reportExport";
import { MatchCentre } from "@/components/vision/MatchCentre";
import { AnalysisWait } from "@/components/vision/AnalysisWait";
import { matchStats } from "@/lib/review/visionStats";
import { MatchReport } from "@/components/vision/MatchReport";
import { ReviewQueue, ReviewFocus } from "@/components/vision/ReviewQueue";
import { BallLabeller } from "@/components/vision/BallLabeller";
import { PitchCalibration, CalibrationOverlay } from "@/components/vision/PitchCalibration";
import { MiniPitch } from "@/components/vision/PitchGraphics";
import {
  Analysis,
  AnalysisEvent,
  Calibration,
  describeEvent,
  fetchAnalysis,
  fetchCalibration,
  frameImageUrl,
  isConfirmedGoal,
  pitchLines,
  pitchToImage,
  sampleNeeds,
} from "@/lib/review/analysis";
import { overlayAt, playerMaps, zoomCrop, zoomToSource, ZoomCrop } from "@/lib/review/overlay";
import { reportSummary } from "@/lib/review/reportShare";

/** Magnification of the precise-tap view for pitch landmarks (as in the ball labeller). */
const ZOOM = 5;

const FULL_RERUN_CONFIRM =
  "Analyse the whole match again? It takes a while, uses one of today's analyses, and the new report starts without this report's score and reviews. This report stays in Your matches.";

/** Saved-footage tests start after the analysed section: the same section again would repeat this run. */
function afterSection(start: number, duration: number, videoDuration: number) {
  const after = Math.floor(start + duration);
  return after < Math.ceil(videoDuration) - 1 ? after : 0;
}

/** Two team names typed or saved for a match, or null. */
function namePair(value: unknown): [string, string] | null {
  return Array.isArray(value) && value.length === 2 && value.every((x) => typeof x === "string") ? [value[0], value[1]] : null;
}

function localNames(jobId: string): [string, string] | null {
  try {
    return namePair(JSON.parse(localStorage.getItem(`vision-names-${jobId}`) || "null"));
  } catch {
    return null;
  }
}

/** An action on the video or its detections, asked for before they arrived. */
type Later =
  | { kind: "seek"; t: number }
  | { kind: "calibrate" }
  | { kind: "review"; target?: "score" | "sample" | "key" }
  | { kind: "label" };

export function VisionReport({ jobId }: { jobId: string }) {
  const [job, setJob] = useState<VisionJob | null>(null);
  const [result, setResult] = useState<VisionResult | null>(null);
  const [error, setError] = useState("");
  const [retrying, setRetrying] = useState(false);
  const [rerunning, setRerunning] = useState(false);
  const [testStart, setTestStart] = useState(0);
  /** Run the ball model on the next saved-footage run (null: as this job did). */
  const [ballChoice, setBallChoice] = useState<boolean | null>(null);
  const rerunRequest = useRef<{ key: string; id: string } | null>(null);
  /** Where the team names came from: typed on this page wins over everything saved. */
  const namesFrom = useRef<"default" | "source" | "local" | "worker" | "typed">("default");
  /** The last names typed that the worker has not saved yet (sent after a pause, or on blur). */
  const unsavedNames = useRef<[string, string] | null>(null);
  const namesTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  /** The worker could not save the names: they are kept on this browser only. */
  const [namesLocalOnly, setNamesLocalOnly] = useState(false);
  /** The title and match date being edited in the header. */
  const [editing, setEditing] = useState<{ title: string; matchDate: string; saving: boolean; error: string } | null>(null);
  const [time, setTime] = useState(0);
  const [overlay, setOverlay] = useState(true);
  const [names, setNames] = useState<string[]>(["Kit A", "Kit B"]);
  const [filter, setFilter] = useState("all");
  const player = useRef<HTMLVideoElement>(null);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  /** loading until the analysis answers; missing on an older worker without analytics (the classic report). */
  const [analysisState, setAnalysisState] = useState<"loading" | "ready" | "missing" | "error">("loading");
  const [calibration, setCalibration] = useState<Calibration | null>(null);
  const [analysisError, setAnalysisError] = useState("");
  const loadId = useRef(0);
  const [calibrating, setCalibrating] = useState(false);
  const [calOverlay, setCalOverlay] = useState<CalibrationOverlay | null>(null);
  const [calClick, setCalClick] = useState<{ x: number; y: number; n: number; moved?: boolean } | null>(null);
  const [showLines, setShowLines] = useState(true);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [labelling, setLabelling] = useState(false);
  /** The worker no longer has this match (deleted in another tab, for example). */
  const [deleted, setDeleted] = useState(false);
  /** This browser cannot open the match: another browser's, an in-app browser's storage, or deleted. */
  const [notAvailable, setNotAvailable] = useState(false);
  /** Seconds since the worker last received bytes of an upload in progress. */
  const [stalledSeconds, setStalledSeconds] = useState(0);
  const received = useRef<{ bytes: number; at: number } | null>(null);
  const [reviewFocus, setReviewFocus] = useState<ReviewFocus>({ target: null, filter: "key", n: 0 });
  /** The magnified crop a first tap on a phone opens for placing a pitch landmark. */
  const [calZoom, setCalZoom] = useState<ZoomCrop | null>(null);
  const zoomCanvas = useRef<HTMLCanvasElement>(null);
  /** "Summary copied", or the text to copy by hand when neither sharing nor the clipboard works. */
  const [shared, setShared] = useState<{ copied: boolean; text: string } | null>(null);
  const clipEnd = useRef<number | null>(null);
  const reviewRef = useRef<HTMLDivElement>(null);
  const labellerRef = useRef<HTMLElement>(null);
  const calibrationRef = useRef<HTMLDivElement>(null);
  const videoBox = useRef<HTMLDivElement>(null);
  /** A seek, pitch setup, review or labeller asked for before the video and detections arrived. */
  const queued = useRef<Later | null>(null);
  /**
   * The match stats, drawn as soon as they arrive: without the per-frame positions
   * (most of a calibrated analysis, fetched afterwards for the mini pitch) and
   * never behind the calibration, which only feeds the line overlay and pitch setup.
   */
  const loadAnalytics = useCallback(async () => {
    const id = ++loadId.current;
    fetchCalibration(jobId).then(
      (c) => id === loadId.current && setCalibration(c),
      () => {},
    );
    setAnalysisState((state) => (state === "ready" ? state : "loading"));
    try {
      const a = await fetchAnalysis(jobId, { positions: false });
      if (id !== loadId.current) return;
      setAnalysis({ ...a, positions: a.positions ?? null });
      setAnalysisError("");
      setAnalysisState("ready");
      if (a.calibrated && a.positionsOmitted)
        fetchAnalysis(jobId).then(
          (full) => id === loadId.current && setAnalysis((prev) => (prev ? { ...prev, positions: full.positions ?? null } : prev)),
          () => {},
        );
    } catch (e) {
      if (id !== loadId.current) return;
      // Older workers have no analytics endpoint: the classic report is the report there.
      if (e instanceof VisionError && e.status === 404) {
        setAnalysis(null);
        setAnalysisError("");
        setAnalysisState("missing");
        return;
      }
      setAnalysisError(e instanceof Error ? e.message : "Match analytics are unavailable");
      // A report already on screen stays; a first load that failed offers a retry.
      setAnalysisState((state) => (state === "ready" ? state : "error"));
    }
  }, [jobId]);
  useEffect(() => {
    let stopped = false;
    let loaded = false;
    let terminal = false;
    /** The analysis was asked for: in the same tick the job is seen complete, beside the result. */
    let analysing = false;
    /** The worker answered for this match at least once on this page. */
    let seen = false;
    let inFlight = false;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const title = document.title;
    // Each poll is a serverless call plus a worker status read: none while the tab is hidden.
    const schedule = () => {
      clearTimeout(timer);
      if (!stopped && !loaded && !terminal && document.visibilityState !== "hidden") timer = setTimeout(read, 2000);
    };
    async function read() {
      if (inFlight || stopped) return;
      inFlight = true;
      clearTimeout(timer);
      try {
        const next = await visionJson<VisionJob>(`jobs/${jobId}`, {
          signal: controller.signal,
        });
        if (stopped) return;
        seen = true;
        terminal = ["failed", "cancelled", "interrupted"].includes(next.status);
        // An upload still arriving: when its bytes last moved says whether anything is sending them.
        if (next.status === "uploading" && typeof next.receivedBytes === "number") {
          const now = Date.now();
          if (!received.current || received.current.bytes !== next.receivedBytes) received.current = { bytes: next.receivedBytes, at: now };
          setStalledSeconds((now - received.current.at) / 1000);
        }
        setJob(next);
        setError("");
        if (next.status === "completed") document.title = `Report ready · ${next.title}`;
        if (next.status === "completed" && !analysing) {
          // The stats need only the analysis (about 100 KB): never wait for the
          // result (megabytes of detections) before drawing them.
          analysing = true;
          loadAnalytics();
        }
        if (next.status === "completed" && !loaded) {
          const data = await visionJson<VisionResult>(`jobs/${jobId}/result`, {
            signal: controller.signal,
          });
          if (stopped) return;
          setResult(data);
          setTime(data.analysedStart ?? 0);
          // Offer the footage after this section: the same section again would repeat this run.
          setTestStart(afterSection(data.analysedStart ?? 0, data.analysedDuration, data.video.duration));
          loaded = true;
        }
      } catch (e) {
        if (!stopped) {
          if (e instanceof VisionError && ((e.status === 404 && !seen) || (e.code === "ownership" && [401, 403].includes(e.status)))) {
            // Permanent: another browser's match (each browser sees only its own), this
            // browser with its storage blocked, or a deleted match. The worker answers
            // 404 for all of them, so the page cannot tell which (and does not try).
            terminal = true;
            setNotAvailable(true);
            setError("");
          } else if (isMatchDeleted(e)) {
            // Seen on this page a moment ago: deleted meanwhile (in another tab, for example).
            terminal = true;
            setDeleted(true);
            setError("");
          } else setError(e instanceof Error ? e.message : "Unable to read analysis");
        }
      } finally {
        inFlight = false;
        schedule();
      }
    }
    const visible = () => {
      if (document.visibilityState === "visible") {
        if (!loaded && !terminal) read();
      } else clearTimeout(timer);
    };
    document.addEventListener("visibilitychange", visible);
    read();
    // Names typed on this browser before (the worker's copy, once it answers, wins over them).
    const saved = localNames(jobId);
    if (saved) {
      namesFrom.current = "local";
      setNames(saved);
    }
    return () => {
      stopped = true;
      controller.abort();
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", visible);
      document.title = title;
    };
  }, [jobId, loadAnalytics]);
  // The names saved on the worker for this browser's match; names saved only on
  // this browser before that existed are sent to the worker once.
  const teamNames = job?.teamNames ?? null;
  const migrated = useRef(false);
  useEffect(() => {
    if (!job || namesFrom.current === "typed") return;
    if (teamNames) {
      namesFrom.current = "worker";
      setNames(teamNames);
      return;
    }
    const saved = localNames(jobId);
    if (saved && !migrated.current) {
      migrated.current = true;
      saveVisionDetails(jobId, { names: saved }).catch(() => {});
    }
  }, [job, teamNames, jobId]);
  const matchGone = useCallback(() => {
    setDeleted(true);
    setError("");
    setCalibrating(false);
    setReviewOpen(false);
    setLabelling(false);
  }, []);
  // A rerun keeps Kit A / Kit B in its source's order (teamsAlignedTo): its names apply too,
  // the ones saved on the worker first, then those saved on this browser.
  const sourceJobId = job?.sourceJobId;
  const aligned = !!sourceJobId && result?.teamsAlignedTo === sourceJobId;
  useEffect(() => {
    if (!aligned || !sourceJobId || teamNames || namesFrom.current !== "default") return;
    let stopped = false;
    visionJson<VisionJob>(`jobs/${sourceJobId}`)
      .then((source) => source.teamNames ?? null, () => null)
      .then((fromWorker) => {
        const borrowed = fromWorker ?? localNames(sourceJobId);
        if (stopped || !borrowed || namesFrom.current !== "default") return;
        namesFrom.current = "source";
        setNames(borrowed);
      });
    return () => {
      stopped = true;
    };
  }, [aligned, sourceJobId, teamNames]);
  async function rerun(mode: "section" | "full", withBall?: boolean) {
    // A full run is long and paid for, and its report starts without this one's
    // score and reviews: ask first. A 20-second section does not ask.
    if (mode === "full" && !window.confirm(FULL_RERUN_CONFIRM)) return;
    // Before the result arrives (a banner's test button), the section after this one.
    const start =
      mode === "full"
        ? 0
        : result || analysedDuration === undefined || videoDuration === undefined
          ? testStart
          : afterSection(analysedStart, analysedDuration, videoDuration);
    // The ball model as chosen; unchanged from this job means the worker copies its settings.
    const usedBall = ballSearch !== "none";
    const wanted = withBall ?? ballChoice ?? usedBall;
    const search = wanted === usedBall ? undefined : wanted ? "exhaustive" : "none";
    const key = `${jobId}:${mode}:${start}:${search ?? "same"}`;
    if (rerunRequest.current?.key !== key) rerunRequest.current = { key, id: crypto.randomUUID() };
    setRerunning(true);
    setError("");
    try {
      const next = await rerunSavedVision(jobId, mode, start, rerunRequest.current.id, search);
      window.location.assign(`/vision/${next.id}`);
    } catch (e) {
      if (isMatchDeleted(e)) matchGone();
      else setError(visionErrorMessage(e, "Unable to analyse saved footage"));
      setRerunning(false);
    }
  }
  /** A video that will not play may be gone rather than unsupported: ask the worker first. */
  async function videoFailed() {
    if (job?.videoDeleted || deleted) return;
    try {
      const next = await visionJson<VisionJob>(`jobs/${jobId}`);
      if (next.videoDeleted) {
        // Retention removed it since this page loaded: show the notice, hide what needs the video.
        setJob(next);
        setCalibrating(false);
        setLabelling(false);
        return;
      }
    } catch (e) {
      if (isMatchDeleted(e)) matchGone();
      else setError("The video could not be loaded. Check your connection and reload the page.");
      return;
    }
    setError("Video playback failed. This browser may not support its codec. Use H.264 MP4.");
  }
  // A pitch setup is running on the worker: set up automatically from a saved
  // venue, or applied by hand before a reload. Follow it (the light status only)
  // and refresh the report when it finishes.
  const setupRunning = calibration?.job?.state === "processing";
  useEffect(() => {
    if (!setupRunning) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await fetchCalibration(jobId, undefined, false);
        if (stopped) return;
        if (next.job?.state === "processing") {
          setCalibration((prev) => (prev ? { ...prev, job: next.job } : next));
          timer = setTimeout(poll, 5000);
        } else loadAnalytics();
      } catch (e) {
        if (stopped || (e instanceof VisionError && e.status < 500 && e.status !== 429)) return;
        // A restarting worker comes back: keep checking, more slowly.
        timer = setTimeout(poll, 15_000);
      }
    };
    timer = setTimeout(poll, 5000);
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [setupRunning, jobId, loadAnalytics]);
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
  const frameIndex = useMemo(
    () => (result ? frameIndexAt(result.frames, time, result.video.fps, result.sampleFps) : -1),
    [result, time],
  );
  const frame = frameIndex >= 0 && result ? result.frames[frameIndex] : null;
  // Drawn boxes glide between analysed frames; the status line and the ball stay on the held frame.
  const maps = useMemo(() => (result ? playerMaps(result.frames) : null), [result]);
  const drawn = useMemo(
    () => (result && maps && frameIndex >= 0 ? overlayAt(result.frames, frameIndex, time, result.sampleFps, maps) : null),
    [result, maps, frameIndex, time],
  );
  // Pitch setup is anchored to the analysed frame on screen: send its stored time,
  // not the decoder's (which can differ by a fraction of a millisecond).
  const calibrationTime = result ? analysedFrameTime(result.frames, time, result.video.fps) ?? time : time;
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
  // A seek made before the video's metadata arrives is replayed once it does
  // (the browser would otherwise start at the default position).
  const pendingSeek = useRef<{ t: number; play: boolean } | null>(null);
  // Where the page itself last moved the video: any other seek is the viewer's.
  const ownSeekTo = useRef<number | null>(null);
  const moveVideo = useCallback((video: HTMLVideoElement, t: number, play = false) => {
    // 1 ms past a stored (rounded) frame time, so the analysed frame itself is shown.
    const target = seekTarget(t);
    ownSeekTo.current = target;
    // readyState 0 (HAVE_NOTHING): no metadata yet.
    pendingSeek.current = video.readyState < 1 ? { t, play } : null;
    video.currentTime = target;
  }, []);
  function seek(t: number) {
    if (player.current) {
      clipEnd.current = null;
      reelRef.current = null;
      setReel(null);
      moveVideo(player.current, t);
      player.current.pause();
      player.current.scrollIntoView({ behavior: "smooth", block: "center" });
      setTime(t);
    }
  }
  const watchClip = useCallback((t: number, until: number) => {
    const video = player.current;
    if (!video) return;
    clipEnd.current = until;
    moveVideo(video, t, true);
    setTime(t);
    video.play().catch(() => {});
  }, [moveVideo]);
  // Highlights: goals, possible goals and shots played back to back. Moments
  // within 3 s of each other are one highlight, shown as its most important
  // moment and played from before the first to after the last.
  const highlights = useMemo(() => {
    const moments = (analysis?.events ?? [])
      .filter((e) => e.status !== "rejected" && ["goal", "goal-candidate", "shot"].includes(e.type))
      .sort((a, b) => a.t - b.t);
    const groups: AnalysisEvent[][] = [];
    for (const e of moments) {
      const group = groups[groups.length - 1];
      if (group && e.t - group[group.length - 1].t <= 3) group.push(e);
      else groups.push([e]);
    }
    return groups.map((group) => ({
      event: group.reduce((best, e) => (highlightRank(e) < highlightRank(best) ? e : best)),
      from: Math.max(0, group[0].t - 4),
      until: group[group.length - 1].t + 2,
    }));
  }, [analysis]);
  const [reel, setReel] = useState<number | null>(null);
  const reelRef = useRef<number | null>(null);
  const playHighlight = useCallback(
    (k: number | null) => {
      reelRef.current = k;
      setReel(k);
      if (k === null || !highlights[k]) return;
      watchClip(highlights[k].from, highlights[k].until);
    },
    [highlights, watchClip],
  );
  // Review clips own the video: the reel stops, so the end of a review clip
  // never jumps to the next highlight.
  const stopReel = useCallback(() => {
    reelRef.current = null;
    setReel(null);
  }, []);
  const reviewWatch = useCallback(
    (t: number, until: number) => {
      stopReel();
      watchClip(t, until);
    },
    [stopReel, watchClip],
  );
  function videoClick(e: React.MouseEvent<SVGSVGElement>) {
    if (!calibrating || !result) return;
    const box = e.currentTarget.getBoundingClientRect();
    const x = ((e.clientX - box.left) / box.width) * result.video.width;
    const y = ((e.clientY - box.top) / box.height) * result.video.height;
    // A fingertip, or a video shown smaller than its pixels, is too coarse for a fit
    // graded in pixels: the first tap opens a magnified crop, the second places.
    if (window.matchMedia("(pointer: coarse)").matches || box.width < 640) {
      player.current?.pause();
      const now = player.current?.currentTime ?? time;
      const nearest = result.frames.length ? result.frames[nearestFrame(result.frames, now)].t : now;
      if (Math.abs(nearest - now) > 0.5 / (result.video.fps || 30) + 0.005) snapTo(nearest);
      setCalZoom(zoomCrop(x, y, result.video.width, result.video.height, ZOOM));
      return;
    }
    place(x, y);
  }
  function zoomTap(e: React.MouseEvent<HTMLCanvasElement>) {
    if (!calZoom) return;
    const box = e.currentTarget.getBoundingClientRect();
    const p = zoomToSource(calZoom, (e.clientX - box.left) / box.width, (e.clientY - box.top) / box.height);
    setCalZoom(null);
    place(p.x, p.y);
  }
  /** A landmark tap or click at (x, y) in video pixels. */
  function place(x: number, y: number) {
    if (!result) return;
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
    moveVideo(v, t);
    setTime(t);
  }
  /** Bring a panel into view. On a phone the video is pinned under the header while
   *  pitch setup or review is open, so its controls go just below the video. */
  const reveal = (panel: HTMLElement | null) => {
    if (!panel) return;
    const pinned = videoBox.current;
    if (!pinned || window.matchMedia("(min-width: 1024px)").matches) {
      panel.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
    const target = panel.querySelector("[data-reveal]") ?? panel;
    const below = 64 + pinned.getBoundingClientRect().height + 12;
    window.scrollTo({ top: target.getBoundingClientRect().top + window.scrollY - below, behavior: "smooth" });
  };
  // The magnified crop is drawn from the paused video, and again once a snap to the
  // nearest analysed frame lands; points already placed are marked in it.
  useEffect(() => {
    const video = player.current;
    const out = zoomCanvas.current;
    if (!calZoom || !video || !out || !result) return;
    const draw = () => {
      const sx = video.videoWidth ? video.videoWidth / result.video.width : 1;
      const sy = video.videoHeight ? video.videoHeight / result.video.height : 1;
      out.width = 960;
      out.height = Math.max(1, Math.round((960 * calZoom.h) / calZoom.w));
      const ctx = out.getContext("2d");
      if (!ctx) return;
      ctx.imageSmoothingEnabled = false;
      try {
        ctx.drawImage(video, calZoom.x0 * sx, calZoom.y0 * sy, calZoom.w * sx, calZoom.h * sy, 0, 0, out.width, out.height);
      } catch {}
      const k = out.width / calZoom.w;
      ctx.strokeStyle = "#f8ef3d";
      ctx.lineWidth = 2;
      for (const p of calOverlay?.points ?? []) {
        ctx.beginPath();
        ctx.arc((p.x - calZoom.x0) * k, (p.y - calZoom.y0) * k, 10, 0, 2 * Math.PI);
        ctx.stroke();
      }
    };
    draw();
    video.addEventListener("seeked", draw);
    return () => video.removeEventListener("seeked", draw);
  }, [calZoom, result, calOverlay]);
  useEffect(() => {
    if (!calibrating) setCalZoom(null);
  }, [calibrating]);
  const startCalibration = () => {
    // Pitch setup is clicked on the video: nothing to click once the footage is gone.
    if (job?.videoDeleted || deleted) return;
    // One setup at a time: while one runs the panel shows its progress instead.
    if (setupRunning) {
      reveal(calibrationRef.current);
      return;
    }
    setCalibrating(true);
    // Adjusting a saved setup returns to the frame it was clicked on.
    const savedAt = calibration?.request?.t;
    if (result && result.frames.length) snapTo(result.frames[nearestFrame(result.frames, savedAt ?? time)].t);
    setTimeout(() => reveal(calibrationRef.current), 50);
  };
  /** Open the review for the score, the random sample or the key moments. Without a
   *  target it opens on what is left: key moments first, then the sample. */
  const openReview = (target?: "score" | "sample" | "key") => {
    if (!analysis) return;
    stopReel();
    // One panel with keyboard shortcuts at a time (both use Enter and arrows).
    setLabelling(false);
    const pendingKey = analysis.review.pendingKey;
    const filter =
      target === "sample" || target === "key"
        ? target
        : pendingKey !== undefined && pendingKey > 0
          ? "key"
          : pendingKey === 0 && sampleNeeds(analysis).some((t) => !t.done)
            ? "sample"
            : "key";
    setReviewFocus((f) => ({ target: target ?? null, filter, n: f.n + 1 }));
    setReviewOpen(true);
    setTimeout(() => reveal(reviewRef.current), 50);
  };
  const openLabeller = () => {
    setReviewOpen(false);
    setLabelling(true);
    setTimeout(() => labellerRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
  };
  /** The report as plain text for a team chat: the share sheet, else the clipboard,
   *  else the text itself to copy by hand. Never the link (it opens only here). */
  async function shareSummary() {
    if (!analysis || analysedDuration === undefined) return;
    const text = reportSummary({
      title: job?.title ?? "",
      // The day it was played, when the team set it (never the upload date).
      matchDate: matchDateLabel(job?.matchDate) ?? undefined,
      names,
      analysis,
      analysedDuration,
      diagnosticSpan,
      ballTracked,
      playerLoad: playerLoad ?? null,
    });
    setShared(null);
    if (typeof navigator.share === "function") {
      try {
        await navigator.share({ title: job?.title || "Match report", text });
        return;
      } catch (e) {
        // Closing the share sheet is a choice, not a failure.
        if (e instanceof DOMException && e.name === "AbortError") return;
      }
    }
    try {
      await navigator.clipboard.writeText(text);
      setShared({ copied: true, text });
    } catch {
      setShared({ copied: false, text });
    }
  }
  function exportReport() {
    if (!result || !analysis) return;
    const report = buildReportExport({ jobId, title: job?.title ?? "", names, result, analysis });
    const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `pitchlens-report-${jobId}.json`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  /** Sends the names typed last to the worker (they are already on this browser). */
  const flushNames = useCallback(async () => {
    clearTimeout(namesTimer.current);
    const pending = unsavedNames.current;
    unsavedNames.current = null;
    // An emptied field is still being typed: nothing to save yet.
    if (!pending || pending.some((n) => !n.trim())) return;
    try {
      await saveVisionDetails(jobId, { names: [pending[0].trim(), pending[1].trim()] });
      setNamesLocalOnly(false);
    } catch (e) {
      if (isMatchDeleted(e)) matchGone();
      else setNamesLocalOnly(true);
    }
  }, [jobId, matchGone]);
  // Names typed just before leaving the page are still sent.
  useEffect(() => () => void flushNames(), [flushNames]);
  function rename(index: number, value: string) {
    const next: [string, string] = [names[0], names[1]];
    next[index] = value;
    typedNames(next);
  }
  /** Swaps the two names only: the kits and every count stay where they are. */
  function swapNames() {
    typedNames([names[1], names[0]], 0);
  }
  function typedNames(next: [string, string], wait = 800) {
    setNames(next);
    namesFrom.current = "typed";
    try {
      localStorage.setItem(`vision-names-${jobId}`, JSON.stringify(next));
    } catch {
      setError("Team names could not be saved on this browser.");
    }
    // Saved on the worker after a pause in typing (and on blur), so they survive this browser's storage.
    unsavedNames.current = next;
    clearTimeout(namesTimer.current);
    namesTimer.current = setTimeout(flushNames, wait);
  }
  /** After a team fix on the worker: the detections and kit colours changed. */
  async function reloadResult() {
    try {
      setResult(await visionJson<VisionResult>(`jobs/${jobId}/result`));
    } catch (e) {
      if (isMatchDeleted(e)) matchGone();
      else setError("The teams were fixed. Reload the page to see the new kit colours.");
    }
  }
  /** Saves the title and match date edited in the header. */
  async function saveHeader() {
    if (!editing) return;
    const title = editing.title.trim();
    if (!title) return;
    setEditing({ ...editing, saving: true, error: "" });
    try {
      const saved = await saveVisionDetails(jobId, { title, matchDate: editing.matchDate || null });
      setJob((prev) => (prev ? { ...prev, title: saved.title ?? prev.title, matchDate: saved.matchDate } : prev));
      setEditing(null);
    } catch (e) {
      if (isMatchDeleted(e)) {
        setEditing(null);
        matchGone();
      } else setEditing({ ...editing, saving: false, error: visionErrorMessage(e, "The title and date could not be saved.") });
    }
  }
  // Kit colours, the analysed span, the ball search and the running distance come
  // with the analysis, so the report draws before the result (megabytes) arrives;
  // an older worker's analysis lacks them, and the report waits for the result there.
  const colour = (team: number) => analysis?.teams?.[team]?.colour || result?.teams[team]?.colour || "#cbd5e1";
  const analysedStart = result?.analysedStart ?? analysis?.analysedStart ?? 0;
  const analysedDuration = result?.analysedDuration ?? analysis?.analysedDuration;
  const videoDuration = result?.video.duration ?? job?.video?.duration;
  const ballSearch = result?.ballSearch ?? analysis?.ballSearch ?? job?.ballSearch;
  const ballTracked = ballSearch !== "none";
  const ballFrames = result?.metrics.ballFrames ?? analysis?.ballFrames;
  const playerLoad = analysis?.playerLoad ?? result?.metrics.playerLoad ?? undefined;
  const reportReady = analysisState === "ready" && !!analysis && (!!result || analysedDuration !== undefined);
  const stats = useMemo(() => (result ? matchStats(result) : null), [result]);
  const footage = !job?.videoDeleted && !deleted;
  // On a phone the video stays on screen while pitch setup clicks on it or review plays clips in it.
  const pinVideo = footage && (calibrating || reviewOpen);
  const perf = result?.performance;
  // A test section of a longer video: [start, end] seconds (null for the full match).
  const diagnosticSpan: [number, number] | null =
    analysedDuration !== undefined &&
    videoDuration !== undefined &&
    (job?.maxSeconds || analysedStart > 0) &&
    analysedDuration < videoDuration - 0.5
      ? [analysedStart, analysedStart + analysedDuration]
      : null;
  // A finished 20-second test whose footage is still here: the full match is one tap away.
  const testOffer = !!diagnosticSpan && !!job?.maxSeconds && footage;
  // What the analysed span is: a requested test section, or a file that ends early.
  const spanNote = (() => {
    if (analysedDuration === undefined || videoDuration === undefined) return null;
    const end = clockTime(analysedStart + analysedDuration);
    if (diagnosticSpan)
      return `Diagnostic section: ${clockTime(analysedStart)}–${end}. These results do not describe the full match.`;
    if (!job?.maxSeconds && analysedDuration < 0.97 * videoDuration)
      return `The file ends at ${end} of a declared ${clockTime(videoDuration)}; it may be incomplete. Re-export or re-upload it.`;
    return null;
  })();
  /** Seek, pitch setup, review and the labeller act on the video and its detections:
   *  asked for before they arrive, they run once they have. */
  const later = (action: Later) => {
    if (result) run(action);
    else queued.current = action;
  };
  const run = (action: Later) => {
    if (action.kind === "seek") seek(action.t);
    else if (action.kind === "calibrate") startCalibration();
    else if (action.kind === "review") openReview(action.target);
    else openLabeller();
  };
  useEffect(() => {
    const action = queued.current;
    if (!result || !action) return;
    queued.current = null;
    run(action);
    // Once, when the result arrives (the video is mounted in this render).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result]);
  const seekLater = (t: number) => later({ kind: "seek", t });
  const calibrateLater = () => later({ kind: "calibrate" });
  const reviewLater = (target?: "score" | "sample" | "key") => later({ kind: "review", target });
  const labelLater = () => later({ kind: "label" });
  const matchDay = matchDateLabel(job?.matchDate);
  return (
    <>
      <Navbar />
      <main className="pt-24 pb-16 px-4">
        <div className="max-w-7xl mx-auto space-y-6">
          <Link href="/dashboard" className="text-sm text-pitch-muted">
            ← Your matches
          </Link>
          <header className="flex flex-wrap justify-between gap-4">
            <div className="min-w-0">
              <p className="text-pitch-green text-xs uppercase tracking-widest mb-2">
                Computer vision report
              </p>
              {editing ? (
                // The owner names the match and the day it was played (saved on the worker).
                <form
                  className="space-y-3 max-w-xl"
                  aria-label="Match details"
                  onSubmit={(e) => {
                    e.preventDefault();
                    saveHeader();
                  }}
                >
                  <label className="block text-sm">
                    Title
                    <input
                      aria-label="Match title"
                      className="pitch-input w-full mt-1"
                      maxLength={200}
                      value={editing.title}
                      onChange={(e) => setEditing({ ...editing, title: e.target.value })}
                    />
                  </label>
                  <label className="block text-sm">
                    Match date
                    <input
                      aria-label="Match date"
                      type="date"
                      min="2000-01-01"
                      className="pitch-input w-full mt-1"
                      value={editing.matchDate}
                      onChange={(e) => setEditing({ ...editing, matchDate: e.target.value })}
                    />
                  </label>
                  {editing.error && (
                    <p role="alert" className="text-sm text-red-300">
                      {editing.error}
                    </p>
                  )}
                  <div className="flex flex-wrap gap-2">
                    <button type="submit" className="pitch-button-primary min-h-11" disabled={editing.saving || !editing.title.trim()}>
                      {editing.saving ? "Saving…" : "Save"}
                    </button>
                    <button type="button" className="pitch-button-secondary min-h-11" onClick={() => setEditing(null)}>
                      Cancel
                    </button>
                  </div>
                </form>
              ) : (
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <h1 className="text-3xl font-bold break-words min-w-0">
                    {notAvailable ? "Match not available" : job?.title || "Opening analysis…"}
                  </h1>
                  {job && !notAvailable && !deleted && (
                    <button
                      className="text-sm text-pitch-muted underline min-h-11 px-1"
                      aria-label="Edit match title and date"
                      onClick={() => setEditing({ title: job.title, matchDate: job.matchDate ?? "", saving: false, error: "" })}
                    >
                      Edit
                    </button>
                  )}
                </div>
              )}
              {matchDay && !editing && !notAvailable && (
                <p className="text-sm text-pitch-muted mt-1" data-testid="match-date">
                  Played {matchDay}
                </p>
              )}
              {!notAvailable && (
                <p className="text-pitch-muted mt-2">
                  {result
                    ? `${clockTime(result.analysedDuration)} analysed · ${result.metrics.sampledFrames.toLocaleString()} frames`
                    : job?.status === "completed"
                      ? "Loading the report…"
                      : job?.status === "uploading" && job.expectedBytes
                        ? "Waiting for the video upload."
                        : job
                          ? "Video analysis runs on the vision worker. You can leave this page and return."
                          : ""}
                </p>
              )}
            </div>
            {(result || reportReady) && (
              <div className="flex flex-wrap items-center gap-2 self-start">
                {reportReady && (
                  <button className="pitch-button-secondary" onClick={shareSummary}>
                    Share summary
                  </button>
                )}
                {/* The export carries the detections too: it waits for the result. */}
                {result && analysis && (
                  <button className="pitch-button-secondary" onClick={exportReport}>
                    Export report
                  </button>
                )}
                {result && (
                  <a
                    className={analysis ? "text-xs text-pitch-muted underline" : "pitch-button-secondary"}
                    href={`/api/vision/jobs/${jobId}/result`}
                    download
                  >
                    Export detection data
                  </a>
                )}
                {shared?.copied && (
                  <span role="status" className="text-xs text-emerald-200 w-full">
                    Summary copied
                  </span>
                )}
                {shared && !shared.copied && (
                  <label className="block text-xs text-pitch-muted w-full">
                    Copy this summary
                    <textarea readOnly value={shared.text} rows={8} className="pitch-input w-full mt-1 font-mono text-base sm:text-xs" onFocus={(e) => e.currentTarget.select()} />
                  </label>
                )}
              </div>
            )}
          </header>
          {notAvailable && (
            <section role="alert" className="glass-card p-5 space-y-3" data-testid="match-not-available">
              <p>
                This match can&apos;t be opened here. Pitchlens keeps each match private to the browser that uploaded it,
                so a copied link doesn&apos;t open it anywhere else. If it&apos;s yours, open the link in the browser you
                uploaded from (for example Safari, not inside WhatsApp). It may also have been deleted.
              </p>
              <p className="text-sm text-pitch-muted">
                If someone sent you this link, ask them to share a summary from their report.
              </p>
              <div className="flex flex-wrap gap-3">
                <Link href="/upload" className="pitch-button-primary">
                  Analyse your own match
                </Link>
                <Link href="/dashboard" className="pitch-button-secondary">
                  Your matches
                </Link>
              </div>
            </section>
          )}
          {deleted && (
            <section role="alert" className="glass-card border-red-400/40 p-5 space-y-2">
              <h2 className="font-semibold">This match was deleted</h2>
              <p className="text-sm text-pitch-muted">
                It is no longer on the analysis server, so it cannot be reviewed, replayed or analysed again.
              </p>
              <Link href="/dashboard" className="pitch-button-secondary">
                Back to your matches
              </Link>
            </section>
          )}
          {/* Qualifies every stat below, so it stays above the score. */}
          {spanNote && (
            <section className="glass-card border-amber-400/40 p-4 text-sm space-y-3" data-testid="span-note">
              {testOffer && diagnosticSpan && (
                <p className="font-semibold">
                  This was a {Math.round(job?.maxSeconds ?? 20)}-second test ({clockTime(diagnosticSpan[0])}–{clockTime(diagnosticSpan[1])}).
                  Analyse the full match now, no new upload needed.
                </p>
              )}
              <p className="text-amber-200">{spanNote}</p>
              {testOffer && (
                <div className="flex flex-wrap items-center gap-3">
                  <button className="pitch-button-primary" disabled={rerunning} onClick={() => rerun("full")}>
                    Analyse full match
                  </button>
                  {rerunning && <span role="status" className="text-sm">Starting from saved footage…</span>}
                </div>
              )}
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
              stalledSeconds={stalledSeconds}
              onCancel={() =>
                visionJson(`jobs/${jobId}/cancel`, { method: "POST" }).catch((e) =>
                  setError(e.message),
                )
              }
            />
          )}
          {job && ["failed", "interrupted", "cancelled"].includes(job.status) && (
            // Retry only when the worker can: a complete saved upload with retries left.
            // Older workers do not say; a decoded, undeleted upload is the best guess there.
            (job.retryable ?? (!!job.video && !job.videoDeleted)) ? (
              <button className="pitch-button-primary" disabled={retrying} onClick={async () => {
                setRetrying(true);
                try {
                  await visionJson(`jobs/${jobId}/retry`, { method: "POST" });
                  window.location.reload();
                } catch (e) {
                  setError(visionErrorMessage(e, "Retry failed"));
                  setRetrying(false);
                }
              }}>{retrying ? "Starting retry…" : "Retry saved upload"}</button>
            ) : (
              <Link href="/upload" className="pitch-button-primary">
                Upload again
              </Link>
            )
          )}
          {job?.status === "completed" && (
            // Drawn from the analysis as soon as it arrives: the result (the video's
            // detections, megabytes on a full match) follows underneath.
            <>
              {ballFrames === 0 && ballSearch === "none" && (
                <section role="status" className="glass-card border-white/10 p-5 space-y-2">
                  <h2 className="font-semibold">Player load</h2>
                  <p className="text-sm text-pitch-muted">
                    The ball model was not run. Distance is measured from the players.
                    Possession, passes and shots are not part of this job. Turn the ball
                    model on only if you need them, and check a short section first.
                  </p>
                  {footage && (
                    <button className="pitch-button-secondary text-sm" disabled={rerunning} onClick={() => rerun("section", true)}>
                      Test 20 seconds with the ball model
                    </button>
                  )}
                </section>
              )}
              {ballFrames === 0 && ballSearch !== "none" && (
                <section role="status" className="glass-card border-amber-400/40 p-5 space-y-2">
                  <h2 className="font-semibold text-amber-200">Ball tracking unavailable for this section</h2>
                  <p className="text-sm text-pitch-muted">
                    No ball track was retained. Automatic possession,
                    passes and shots cannot be established from these detections. Inspect the
                    overlays before relying on the report or processing more footage.
                  </p>
                  <button className="pitch-button-secondary text-sm" onClick={() => seekLater(analysedStart)}>
                    Inspect detections
                  </button>
                </section>
              )}
              {(ballFrames ?? 0) > 0 && analysis?.stats.coverage.controlPercent === 0 && (
                <section role="status" className="glass-card border-amber-400/40 p-5 space-y-2">
                  <h2 className="font-semibold text-amber-200">Ball detections did not establish possession</h2>
                  <p className="text-sm text-pitch-muted">A high detection count can include background objects. No reliable player control was established in this section. Inspect the ball overlay and test another section with visible play before running the full match.</p>
                  <button className="pitch-button-secondary text-sm" onClick={() => seekLater(analysedStart)}>Inspect detections</button>
                </section>
              )}
              {reportReady && analysis ? (
                <MatchReport
                  analysis={analysis}
                  names={names}
                  colours={[colour(0), colour(1)]}
                  duration={analysedDuration ?? 0}
                  start={analysedStart}
                  onSeek={seekLater}
                  onCalibrate={calibrateLater}
                  onReview={reviewLater}
                  videoAvailable={footage}
                  playerLoad={playerLoad}
                  ballTracked={ballTracked}
                  afterHero={
                    // A test section is not the match: its steps would be done on the full run.
                    diagnosticSpan ? null : (
                      <NextSteps
                        jobId={jobId}
                        analysis={analysis}
                        names={names}
                        onRename={rename}
                        onNamesDone={flushNames}
                        namesLocalOnly={namesLocalOnly}
                        calibrated={analysis.calibrated}
                        ballTracked={ballTracked}
                        footage={footage}
                        onReview={reviewLater}
                        onCalibrate={calibrateLater}
                        onLabel={labelLater}
                        onShare={shareSummary}
                      />
                    )
                  }
                />
              ) : analysisState === "missing" && result && stats ? (
                // An older worker without analytics: the classic report is the report there.
                <MatchCentre
                  result={result}
                  stats={stats}
                  names={names}
                  colours={[colour(0), colour(1)]}
                  onSeek={seek}
                />
              ) : analysisState === "error" ? null : (
                // Never the classic report while the stats load: its zeros would read as measured.
                <div role="status" className="glass-card p-5 min-h-[24rem] flex items-center justify-center text-pitch-muted" data-testid="report-loading">
                  Loading match stats…
                </div>
              )}
              {analysisError && (
                <div className="text-sm text-amber-200 glass-card p-4 flex flex-wrap items-center gap-3" role="status">
                  <span>Match analytics could not be loaded ({analysisError}).</span>
                  <button className="pitch-button-secondary min-h-11" onClick={loadAnalytics}>
                    Retry
                  </button>
                </div>
              )}
              {!result && reportReady && (
                <p role="status" className="text-sm text-pitch-muted glass-card p-4" data-testid="result-loading">
                  Loading the video and detections…
                </p>
              )}
            </>
          )}
          {result && (
            <>
              <div className="grid grid-cols-[minmax(0,1fr)] lg:grid-cols-[minmax(0,2fr)_minmax(280px,1fr)] gap-4 lg:gap-6">
                {/* Below lg this column's children join the grid, so a pinned video
                    stays on screen over the panels that follow it. */}
                <div className="max-lg:contents lg:space-y-4">
                  {job?.videoDeleted && (
                    <p className="glass-card p-4 text-sm text-pitch-muted" role="status">
                      The uploaded footage was deleted by the server&apos;s retention
                      policy. Measurements and timestamps below remain; detection
                      overlays need the video.
                    </p>
                  )}
                  {analysis && highlights.length > 0 && footage && (
                    <div
                      className="flex flex-wrap items-center gap-2 text-sm"
                      data-testid="highlights"
                      data-clip={reel !== null && highlights[reel] ? `${highlights[reel].from.toFixed(1)}-${highlights[reel].until.toFixed(1)}` : undefined}
                    >
                      {reel === null || !highlights[reel] ? (
                        <button className="pitch-button-secondary" onClick={() => playHighlight(0)}>
                          Play highlights ({highlights.length})
                        </button>
                      ) : (
                        <>
                          <span className="text-pitch-muted">
                            Highlight {reel + 1} of {highlights.length} · {clockTime(highlights[reel].event.t)} · {highlightLabel(highlights[reel].event)}
                            {highlights[reel].event.status === "confirmed" ? "" : " (to review)"}
                          </span>
                          <button className="pitch-button-secondary" onClick={() => playHighlight(reel + 1 < highlights.length ? reel + 1 : null)}>
                            Next
                          </button>
                          <button
                            className="pitch-button-secondary"
                            onClick={() => {
                              playHighlight(null);
                              player.current?.pause();
                            }}
                          >
                            Stop
                          </button>
                        </>
                      )}
                    </div>
                  )}
                  <div
                    ref={videoBox}
                    className={`relative bg-black rounded-2xl overflow-hidden ${
                      // Pinned under the 4rem header; a tall (portrait) video is capped at 40% of the screen.
                      pinVideo ? "max-lg:sticky max-lg:top-16 max-lg:z-20 max-lg:mx-auto max-lg:w-full max-lg:max-w-[calc(40vh*var(--video-ratio))]" : ""
                    }`}
                    style={{
                      aspectRatio: `${result.video.width}/${result.video.height}`,
                      display: footage ? undefined : "none",
                      ["--video-ratio" as string]: result.video.width / result.video.height,
                    }}
                  >
                    <video
                      ref={player}
                      onLoadedMetadata={(e) => {
                        const v = e.currentTarget;
                        const pending = pendingSeek.current;
                        pendingSeek.current = null;
                        if (pending) {
                          // A marker, bar or review clip chosen while the video was still loading.
                          moveVideo(v, pending.t);
                          if (pending.play) v.play().catch(() => {});
                        } else if (v.currentTime === 0 && (result.analysedStart ?? 0) > 0) moveVideo(v, result.analysedStart ?? 0);
                      }}
                      onSeeking={(e) => {
                        const v = e.currentTarget;
                        const target = ownSeekTo.current;
                        // The browser clamps a seek past the end to the duration.
                        if (target !== null && Math.abs(v.currentTime - Math.min(target, v.duration || target)) < 0.25) return;
                        // The viewer moved the video with its own controls: the reel and
                        // any clip end stop, so dragging past a clip end does not jump on.
                        clipEnd.current = null;
                        if (reelRef.current !== null) stopReel();
                      }}
                      controls
                      playsInline
                      preload="metadata"
                      className="w-full h-full"
                      src={footage ? `/api/vision/jobs/${jobId}/video` : undefined}
                      onTimeUpdate={(e) => {
                        const v = e.currentTarget;
                        setTime(v.currentTime);
                        if (clipEnd.current !== null && v.currentTime >= clipEnd.current) {
                          clipEnd.current = null;
                          v.pause();
                          const k = reelRef.current;
                          if (k !== null) playHighlight(k + 1 < highlights.length ? k + 1 : null);
                        }
                      }}
                      onSeeked={(e) => setTime(e.currentTarget.currentTime)}
                      onError={videoFailed}
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
                    {calibrating && calZoom && (
                      // Second tap: place the landmark exactly, at five times the size.
                      <div className="absolute inset-0 z-10 bg-black" data-testid="calibration-zoom">
                        <canvas
                          ref={zoomCanvas}
                          onClick={zoomTap}
                          className="w-full h-full cursor-crosshair"
                          aria-label="Magnified frame: tap exactly on the landmark"
                        />
                        <button className="absolute top-2 left-2 pitch-button-secondary min-h-11 px-3 text-xs" onClick={() => setCalZoom(null)}>
                          Back
                        </button>
                      </div>
                    )}
                    {overlay && frame && !calibrating && (
                      <svg
                        aria-label="Computer vision detection overlay"
                        className="absolute inset-0 w-full h-full pointer-events-none"
                        viewBox={`0 0 ${result.video.width} ${result.video.height}`}
                      >
                        {(drawn?.players ?? frame.players).map((p) => (
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
                <aside className="space-y-4 min-w-0">
                  {analysis !== null && (
                    <div ref={calibrationRef}>
                      <PitchCalibration
                        jobId={jobId}
                        videoSize={[result.video.width, result.video.height]}
                        time={calibrationTime}
                        click={calClick}
                        active={calibrating}
                        onActive={(on) => (on ? startCalibration() : setCalibrating(false))}
                        onOverlay={setCalOverlay}
                        calibration={calibration}
                        onApplied={loadAnalytics}
                        videoAvailable={footage}
                        onDeleted={matchGone}
                        zoomed={!!calZoom}
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
                      {/* The analysed frame the overlay shows (its stored time), not the decoder's time. */}
                      <MiniPitch analysis={analysis} time={frame?.t ?? time} colours={[colour(0), colour(1)]} />
                    </section>
                  )}
                  <KitGroups
                    jobId={jobId}
                    teams={result.teams}
                    teamsSetBy={result.teamsSetBy}
                    names={names}
                    videoSize={[result.video.width, result.video.height]}
                    onRename={rename}
                    onNamesDone={flushNames}
                    onSwapNames={swapNames}
                    namesLocalOnly={namesLocalOnly}
                    onFixed={async () => {
                      await Promise.all([reloadResult(), loadAnalytics()]);
                    }}
                    onDeleted={matchGone}
                  />
                </aside>
                {/* Inside the grid (full width) so a pinned video stays over the review on a phone. */}
                {analysis && !deleted && (
                  <div ref={reviewRef} className="lg:col-span-2 min-w-0">
                    {reviewOpen ? (
                      <ReviewQueue
                        jobId={jobId}
                        analysis={analysis}
                        names={names}
                        colours={[colour(0), colour(1)]}
                        time={time}
                        onWatch={reviewWatch}
                        onAnalysis={(next) =>
                          // Review responses leave out the per-frame positions (unchanged).
                          setAnalysis((prev) => ({ ...next, positions: next.positions ?? prev?.positions ?? null }))
                        }
                        onDeleted={matchGone}
                        focus={reviewFocus}
                      />
                    ) : (
                      <section className="glass-card p-5 flex flex-wrap items-center justify-between gap-3">
                        <div>
                          <h2 className="text-xl font-semibold">Review moments</h2>
                          <p className="text-sm text-pitch-muted" data-testid="review-summary">
                            {reviewSummary(analysis)} Confirmed goals set the score.
                          </p>
                        </div>
                        <button className="pitch-button-primary" onClick={() => openReview()}>
                          Start reviewing
                        </button>
                      </section>
                    )}
                  </div>
                )}
              </div>
              {analysis && footage && (
                <section ref={labellerRef} className="glass-card p-5 space-y-3">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <h2 className="text-xl font-semibold">Ball accuracy and training</h2>
                      <p className="text-sm text-pitch-muted max-w-2xl">
                        {result.ballSearch === "none"
                          ? "Show Pitchlens where the ball is on frames from across this match. The ball model did not run on this job, so nothing is measured here; the answers train a better ball finder for footage like yours."
                          : "Show Pitchlens where the ball is on frames from across this match. It measures how often the ball was found, and the answers train a better ball finder for footage like yours."}
                      </p>
                    </div>
                    <button className="pitch-button-secondary" onClick={() => {
                        setReviewOpen(false);
                        setLabelling((v) => !v);
                      }}>
                      {labelling ? "Close" : "Label the ball"}
                    </button>
                  </div>
                  {labelling && <BallLabeller jobId={jobId} onDeleted={matchGone} ballModel={result.ballSearch !== "none"} />}
                </section>
              )}
              {analysisState === "missing" && (
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
              {/* Below the report: processing times and another run of the saved
                  footage are for later, not what a phone should open on. */}
              {(perf || footage) && (
                <details className="glass-card p-4 text-sm group" data-testid="analyse-again">
                  <summary className="cursor-pointer font-semibold min-h-11 flex items-center">{footage ? "Analyse again" : "Processing times"}</summary>
                  <div className="space-y-3 pt-2">
                    {perf && <p data-testid="processing-times">{processingTimes(result)}</p>}
                    {perf && typeof perf.totalSeconds === "number" && result.analysedDuration > 0 && <p>{(perf.totalSeconds / result.analysedDuration).toFixed(1)} seconds processing per second of footage. Full matches may take a different amount of time.</p>}
                    {footage && (
                      <section className="space-y-3" aria-label="Analyse saved footage">
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
                          {/* The test banner above already offers the full match. */}
                          {!testOffer && <button className="pitch-button-primary text-sm" disabled={rerunning} onClick={() => rerun("full")}>Analyse full match</button>}
                          {rerunning && <span role="status" className="text-sm">Starting from saved footage…</span>}
                        </div>
                        <label className="flex gap-2 text-sm">
                          <input type="checkbox" disabled={rerunning} checked={ballChoice ?? result.ballSearch !== "none"} onChange={(e) => setBallChoice(e.target.checked)} />
                          Run the ball model (possession, passes, shots)
                        </label>
                      </section>
                    )}
                  </div>
                </details>
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
                <p className="text-xs text-pitch-muted break-all" data-testid="models">
                  Player model: {result.model} · checksum {result.modelSha256}
                  {result.ballModel && <><br />Ball model: {result.ballModel}{result.ballModelSha256 ? ` · checksum ${result.ballModelSha256}` : ""}</>}
                  {!result.ballModel && result.ballModelSha256 && <><br />Ball model checksum: {result.ballModelSha256}</>}
                </p>
              </section>
            </>
          )}
        </div>
      </main>
    </>
  );
}
/** The collapsed review card in the words of the check-a-sample method: shots and
 *  goals one by one, passes and won balls from about ten random checks each. */
function reviewSummary(analysis: Analysis): string {
  const key = analysis.review.pendingKey;
  // Older workers do not split the queue.
  if (key === undefined) return `${analysis.review.pending} automatic moment${analysis.review.pending === 1 ? "" : "s"} to check.`;
  const all = sampleNeeds(analysis);
  const sampled = all.some((t) => t.pending > 0 || t.checked > 0);
  const asks = all
    .filter((t) => !t.done)
    .map((t) => {
      if (t.few) return t.pending === 1 ? `the ${t.one}` : `all ${t.pending} ${t.many}`;
      const n = t.checked ? t.min - t.checked : t.min;
      return `${t.checked ? `${n} more` : n} random ${n === 1 ? t.one : t.many}`;
    });
  if (!key && !asks.length) return sampled ? "Key moments checked; enough sampled to estimate the rest." : "Key moments checked.";
  const list = asks.length > 1 ? `${asks.slice(0, -1).join(", ")} and ${asks[asks.length - 1]}` : asks[0];
  const keyText = `${key} shot${key === 1 ? "" : "s"} or goal${key === 1 ? "" : "s"} to check`;
  if (list) return `${keyText} · check ${list} to estimate the rest.`;
  return `${keyText}.${sampled ? " Enough sampled to estimate the rest." : ""}`;
}

/**
 * What to do after the first analysis, right under the score: name the teams,
 * enter the score, check a sample, set up the pitch (and, optionally, label the
 * ball or share a summary). Everything is derived from the report itself; only
 * whether the card is open is remembered, per match, on this browser.
 */
function NextSteps({
  jobId,
  analysis,
  names,
  onRename,
  onNamesDone,
  namesLocalOnly,
  calibrated,
  ballTracked,
  footage,
  onReview,
  onCalibrate,
  onLabel,
  onShare,
}: {
  jobId: string;
  analysis: Analysis;
  names: string[];
  onRename: (index: number, value: string) => void;
  /** Typing finished (blur): save the names now rather than after the pause. */
  onNamesDone: () => void;
  /** The worker could not save the names: they are on this browser only. */
  namesLocalOnly: boolean;
  calibrated: boolean;
  ballTracked: boolean;
  footage: boolean;
  onReview: (target: "score" | "sample") => void;
  onCalibrate: () => void;
  onLabel: () => void;
  onShare: () => void;
}) {
  const storageKey = `vision-next-steps-${jobId}`;
  const [open, setOpen] = useState(false);
  useEffect(() => {
    try {
      setOpen(localStorage.getItem(storageKey) === "open");
    } catch {}
  }, [storageKey]);
  const toggle = () =>
    setOpen((was) => {
      try {
        localStorage.setItem(storageKey, was ? "closed" : "open");
      } catch {}
      return !was;
    });
  const sample = sampleNeeds(analysis).filter((t) => t.pending > 0 || t.checked > 0);
  const steps = [
    {
      id: "names",
      label: "Name the teams",
      done: names[0].trim() !== "Kit A" && names[1].trim() !== "Kit B",
      detail: "",
      action: () => setOpen(true),
      disabled: false,
    },
    {
      id: "score",
      label: "Enter the final score",
      done: !!analysis.enteredScore,
      detail: "It sets the scoreline; goals are never counted without you.",
      action: () => onReview("score"),
      disabled: false,
    },
    // Nothing is sampled in a player-load job: the ball model did not run.
    ...(ballTracked
      ? [
          {
            id: "sample",
            label: "Check a sample",
            done: sample.every((t) => t.done),
            detail: sample.map((t) => t.progress).join(" · ") || "Nothing to check",
            action: () => onReview("sample"),
            disabled: false,
          },
        ]
      : []),
    {
      id: "pitch",
      label: "Set up the pitch",
      done: calibrated,
      detail: footage ? "Unlocks shots, tackles, heatmaps" : "Needs the footage, which was deleted",
      action: onCalibrate,
      disabled: !footage,
    },
  ];
  const done = steps.filter((s) => s.done).length;
  const next = steps.find((s) => !s.done);
  return (
    <section className="glass-card p-3 sm:p-4 text-sm" aria-label="Next steps" data-testid="next-steps">
      <div className="flex flex-wrap items-center gap-2">
        <button className="flex flex-1 min-w-0 items-center gap-2 min-h-11 text-left font-semibold" aria-expanded={open} onClick={toggle}>
          Next steps · {done} of {steps.length} done
          <ChevronDown size={16} className={`shrink-0 transition-transform ${open ? "rotate-180" : ""}`} />
        </button>
        {!open && next && (
          <button className="pitch-button-primary min-h-11" disabled={next.disabled} onClick={next.action}>
            {next.label}
          </button>
        )}
      </div>
      {open && (
        <ol className="mt-2 divide-y divide-white/10">
          {steps.map((step, i) => (
            <li key={step.id} className="py-2.5 space-y-2" data-step={step.id}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="min-w-0">
                  <p className="font-medium">
                    {i + 1}. {step.label}
                  </p>
                  {step.detail && <p className="text-xs text-pitch-muted">{step.detail}</p>}
                </div>
                {step.done ? (
                  <span className="inline-flex items-center gap-1 text-xs font-semibold text-emerald-300">
                    <CheckCircle2 size={14} /> Done
                  </span>
                ) : step.id !== "names" ? (
                  <button className="pitch-button-secondary min-h-11" disabled={step.disabled} onClick={step.action}>
                    {step.label}
                  </button>
                ) : null}
              </div>
              {step.id === "names" && (
                <div className="grid grid-cols-2 gap-2">
                  {[0, 1].map((t) => (
                    <input
                      key={t}
                      aria-label={`Team ${t === 0 ? "A" : "B"} name`}
                      maxLength={80}
                      value={names[t]}
                      onChange={(e) => onRename(t, e.target.value)}
                      onBlur={onNamesDone}
                      className="pitch-input w-full min-w-0"
                    />
                  ))}
                  {namesLocalOnly && <p className="col-span-2 text-xs text-pitch-muted">Saved on this device only</p>}
                </div>
              )}
            </li>
          ))}
          {footage && (
            <li className="py-2.5 flex flex-wrap items-center justify-between gap-2">
              <div className="min-w-0">
                <p className="font-medium">Optional: label the ball</p>
                <p className="text-xs text-pitch-muted">Measures the ball finder on this match and trains a better one.</p>
              </div>
              <button className="pitch-button-secondary min-h-11" onClick={onLabel}>
                Open the labeller
              </button>
            </li>
          )}
          <li className="py-2.5 flex flex-wrap items-center justify-between gap-2">
            <div className="min-w-0">
              <p className="font-medium">Optional: share a summary</p>
              <p className="text-xs text-pitch-muted">Plain text for the team chat; the link itself opens only on this browser.</p>
            </div>
            <button className="pitch-button-secondary min-h-11" onClick={onShare}>
              Share
            </button>
          </li>
        </ol>
      )}
    </section>
  );
}

/** Which moment names a highlight: a confirmed goal; a confirmed possible goal
 *  or a shot confirmed as a goal; a possible goal; then shots. Within a type,
 *  confirmed comes before proposed. */
function highlightRank(e: AnalysisEvent) {
  const confirmed = e.status === "confirmed";
  if (e.type === "goal") return confirmed ? 0 : 2;
  if (isConfirmedGoal(e)) return 1;
  if (e.type === "goal-candidate") return 2;
  return confirmed ? 3 : 4;
}

const highlightLabel = (e: AnalysisEvent) => (highlightRank(e) <= 1 ? "Goal" : describeEvent(e));

/** "Processing took 12.5s on cpu · players 10.1s · ball 1.2s", from whichever
 *  timings the result has: player-load jobs have no ball time (null or absent),
 *  and older results may lack others. */
function processingTimes(result: VisionResult): string {
  const p = result.performance ?? {};
  const seconds = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? `${v.toFixed(1)}s` : null);
  const total = seconds(p.totalSeconds);
  const parts = [
    `Processing took ${total ?? "an unknown time"}${p.device ? ` on ${p.device}` : ""}`,
    seconds(p.playerInferenceSeconds) && `players ${seconds(p.playerInferenceSeconds)}`,
    result.ballSearch === "none" ? "ball model not run" : seconds(p.ballInferenceSeconds) && `ball ${seconds(p.ballInferenceSeconds)}`,
  ];
  return `${parts.filter(Boolean).join(" · ")}.`;
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

/** A player crop on screen: at least 44 px each way, so a thumb can tap it. */
const CROP_W = 44;
const CROP_H = 64;

/** One player cut from a label frame by the browser: the frame scaled so his box (with a margin) fills the height. */
function cropStyle(jobId: string, crop: TeamCrop, [width, height]: [number, number]): CSSProperties {
  const [x1, y1, x2, y2] = crop.box;
  const margin = 0.15 * (y2 - y1);
  const scale = CROP_H / (y2 - y1 + 2 * margin);
  return {
    width: CROP_W,
    height: CROP_H,
    backgroundImage: `url(${frameImageUrl(jobId, crop.index)})`,
    backgroundSize: `${width * scale}px ${height * scale}px`,
    backgroundPosition: `${CROP_W / 2 - ((x1 + x2) / 2) * scale}px ${-(y1 - margin) * scale}px`,
  };
}

/** The worker takes exactly {index, id} for a tapped player. */
const pickOf = (crop?: TeamCrop): TeamPick | null => (crop ? { index: crop.index, id: crop.id } : null);
const samePlayer = (a: TeamPick | null, b: TeamPick | null) => !!a && !!b && a.index === b.index && a.id === b.id;

/**
 * The detected kits, named by the owner, with a few of each kit's players. When
 * kit clustering picked the wrong colour group (spectators as a kit, say), the
 * owner taps one clear player and says which team he plays for; the worker
 * relabels the match from the stored kit colours, without a new analysis. Kit A
 * stays the engine's Kit A (confirmed goals keep their team), so a crosswise
 * choice swaps the names instead. Without crops (an older analysis, footage no
 * longer kept), only the names are shown.
 */
function KitGroups({
  jobId,
  teams,
  teamsSetBy,
  names,
  videoSize,
  onRename,
  onNamesDone,
  onSwapNames,
  namesLocalOnly,
  onFixed,
  onDeleted,
}: {
  jobId: string;
  teams: VisionResult["teams"];
  teamsSetBy?: "owner";
  names: string[];
  videoSize: [number, number];
  onRename: (index: number, value: string) => void;
  onNamesDone: () => void;
  onSwapNames: () => void;
  namesLocalOnly: boolean;
  /** The worker relabelled the match: load the result and analysis again. */
  onFixed: () => Promise<void>;
  onDeleted: () => void;
}) {
  const [data, setData] = useState<TeamGroups | null>(null);
  /** Bumped after a fix or an undo: the groups changed on the worker. */
  const [version, setVersion] = useState(0);
  /** The crop tapped last: which team is he in? */
  const [asking, setAsking] = useState<TeamCrop | null>(null);
  /** The player standing for each kit: the clearest of each group until the owner chooses. */
  const [picks, setPicks] = useState<[TeamPick | null, TeamPick | null]>([null, null]);
  const [changed, setChanged] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const gone = useRef(onDeleted);
  gone.current = onDeleted;
  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = () =>
      getTeams(jobId).then(
        (next) => {
          if (stop) return;
          setData(next);
          // The worker extracts the label frames from the video in one pass first.
          if (!next.framesReady) timer = setTimeout(load, 3000);
        },
        (e) => {
          if (stop) return;
          // No kit colours to fix from (an older analysis), no footage, or no worker: names only.
          setData(null);
          if (isMatchDeleted(e)) gone.current();
        },
      );
    load();
    return () => {
      stop = true;
      if (timer) clearTimeout(timer);
    };
  }, [jobId, version]);
  const groupsKey = data ? JSON.stringify(data.groups) : "";
  useEffect(() => {
    setPicks([pickOf(data?.groups.A[0]), pickOf(data?.groups.B[0])]);
    setChanged(false);
    setAsking(null);
    // Only new groups reset the choice, not each poll while the frames are prepared.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [groupsKey]);
  const size: [number, number] = [data?.size?.[0] || videoSize[0], data?.size?.[1] || videoSize[1]];
  const name = (t: number) => names[t]?.trim() || `Kit ${"AB"[t]}`;
  function choose(team: 0 | 1) {
    const pick = pickOf(asking ?? undefined);
    if (!pick) return;
    setPicks((prev) => {
      const next: [TeamPick | null, TeamPick | null] = [prev[0], prev[1]];
      next[team] = pick;
      // One player cannot stand for both kits.
      if (next[1 - team]?.id === pick.id) next[1 - team] = null;
      return next;
    });
    setChanged(true);
    setAsking(null);
  }
  async function send(body: Parameters<typeof saveTeams>[1]) {
    setSaving(true);
    setError("");
    try {
      const reply = await saveTeams(jobId, body);
      // Kit A stayed the engine's Kit A: the player said to be in a team is in the other kit.
      if (reply.swapped) onSwapNames();
      await onFixed();
      setVersion((v) => v + 1);
    } catch (e) {
      if (isMatchDeleted(e)) onDeleted();
      else setError(visionErrorMessage(e, "The teams could not be changed."));
    } finally {
      setSaving(false);
    }
  }
  const row = (crops: TeamCrop[], where: string) =>
    !data?.framesReady ? (
      <p className="text-xs text-pitch-muted">Preparing player pictures…</p>
    ) : crops.length === 0 ? (
      <p className="text-xs text-pitch-muted">No clear player</p>
    ) : (
      <div className="flex flex-wrap gap-2">
        {crops.map((crop) => {
          const kit = samePlayer(picks[0], crop) ? 0 : samePlayer(picks[1], crop) ? 1 : null;
          const tapped = samePlayer(asking, crop);
          return (
            <button
              key={`${crop.index}-${crop.id}`}
              type="button"
              aria-label={`Player ${crop.id} in ${where}${kit === null ? "" : `, standing for ${name(kit)}`}`}
              aria-pressed={tapped}
              onClick={() => setAsking(tapped ? null : crop)}
              className={`relative shrink-0 min-h-11 min-w-11 rounded-md border-2 bg-black bg-no-repeat ${tapped ? "border-white" : "border-white/10"}`}
              style={{ ...cropStyle(jobId, crop, size), ...(kit !== null && !tapped ? { borderColor: teams[kit]?.colour } : {}) }}
            >
              {kit !== null && (
                <span className="absolute -top-2 -right-2 rounded-full border-2 bg-pitch-black px-1 text-[10px] font-bold text-pitch-white" style={{ borderColor: teams[kit]?.colour }}>
                  {"AB"[kit]}
                </span>
              )}
            </button>
          );
        })}
      </div>
    );
  return (
    <section className="glass-card p-5 space-y-4">
      <h2 className="font-semibold">Detected kit groups</h2>
      <p className="text-sm text-pitch-muted">
        {data
          ? "Name each team. If a row shows the wrong players, tap one clear player of that team and say which team he plays for."
          : "Match each automatically discovered colour to the correct team."}
      </p>
      {teams.map((team, i) => (
        <div key={team.id} className="space-y-2">
          <label className="flex gap-3 items-center">
            <span className="w-6 h-6 rounded-full border border-white/40 shrink-0" style={{ background: team.colour }} />
            <input
              aria-label={`Name for kit ${i === 0 ? "A" : "B"}`}
              maxLength={80}
              value={names[i]}
              onChange={(e) => onRename(i, e.target.value)}
              onBlur={onNamesDone}
              className="pitch-input w-full min-w-0"
            />
          </label>
          {data && row(data.groups[i === 0 ? "A" : "B"], name(i))}
        </div>
      ))}
      {data && (
        <div className="space-y-2">
          <h3 className="text-sm font-medium">Unsorted players</h3>
          {row(data.groups.unsorted, "unsorted players")}
        </div>
      )}
      {asking && (
        <div role="group" aria-label={`Which team is player ${asking.id} in?`} className="grid grid-cols-2 gap-2">
          {([0, 1] as const).map((t) => (
            <button key={t} type="button" className="pitch-button-secondary min-h-11" onClick={() => choose(t)}>
              <span className="w-3 h-3 rounded-full shrink-0" style={{ background: teams[t]?.colour }} />
              This is {name(t)}
            </button>
          ))}
        </div>
      )}
      {data && changed && (
        <button
          type="button"
          className="pitch-button-primary min-h-11 w-full"
          disabled={saving || !picks[0] || !picks[1]}
          onClick={() => picks[0] && picks[1] && send({ kitA: picks[0], kitB: picks[1] })}
        >
          {saving ? "Fixing teams…" : !picks[0] || !picks[1] ? "Choose a player for each team" : "Apply team fix"}
        </button>
      )}
      {error && (
        <p role="alert" className="text-sm text-red-300">
          {error}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <button type="button" className="pitch-button-ghost min-h-11" onClick={onSwapNames}>
          Swap names
        </button>
        {teamsSetBy === "owner" && (
          <button type="button" className="pitch-button-ghost min-h-11" disabled={saving} onClick={() => send({ reset: true })}>
            Undo team fix
          </button>
        )}
      </div>
      {namesLocalOnly && (
        <p className="text-xs text-pitch-muted" data-testid="names-local-only">
          Saved on this device only
        </p>
      )}
    </section>
  );
}
