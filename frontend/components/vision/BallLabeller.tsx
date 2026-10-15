"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Check, EyeOff, SkipForward, ChevronLeft, ZoomIn } from "lucide-react";
import type { BallLabelState, BallModelState, Rate } from "@/lib/review/analysis";
import { fetchBallLabels, fetchBallModel, frameImageUrl, sendBallLabel, trainBallModel } from "@/lib/review/analysis";
import { clockTime, isMatchDeleted, setTrainingConsent, VisionError, visionJson } from "@/lib/review/vision";
import type { VisionHealth } from "@/lib/review/vision";

const ZOOM = 5; // magnification of the precise-click view

function pct(rate: Rate) {
  return rate.value === null ? "—" : `${Math.round(rate.value * 100)}%`;
}

function range(rate: Rate) {
  return rate.interval95 ? `${Math.round(rate.interval95[0] * 100)}–${Math.round(rate.interval95[1] * 100)}%` : "";
}

/**
 * Ground truth for the ball, from the person who knows the match. Frames are
 * spread over the whole match; for each, confirm Pitchlens' guess, click the
 * ball (a first click zooms in, a second places it exactly), or mark it not
 * visible. The answers measure the ball finder on this match; they train a
 * better one only if the owner ticks the training opt-in (off by default,
 * withdrawable: POST /jobs/{id}/training-consent).
 */
export function BallLabeller({
  jobId,
  onDeleted,
  ballModel = true,
}: {
  jobId: string;
  /** The worker no longer has this match. */
  onDeleted?: () => void;
  /** False on a player-load job: no ball model ran, so there is no accuracy to measure. */
  ballModel?: boolean;
}) {
  const [state, setState] = useState<BallLabelState | null>(null);
  const [position, setPosition] = useState(0);
  const [zoom, setZoom] = useState<{ x: number; y: number } | null>(null);
  const [error, setError] = useState("");
  // Loading the labels failed for a reason that passes (worker restarting): asking again.
  const [retrying, setRetrying] = useState(false);
  const [saving, setSaving] = useState(false);
  // The training opt-in while its change is being saved (null: as the worker said).
  const [consent, setConsent] = useState<boolean | null>(null);
  const [consentError, setConsentError] = useState("");
  // Which frame image has finished loading (the zoom draws only from that one).
  const [loadedSrc, setLoadedSrc] = useState("");
  const image = useRef<HTMLImageElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const root = useRef<HTMLDivElement>(null);
  const ready = !!state?.framesReady;
  // Take keyboard focus when the frames appear: otherwise the button that opened
  // the labeller keeps it, and Enter (accept the guess) would press that button.
  useEffect(() => {
    if (ready) root.current?.focus({ preventScroll: true });
  }, [ready]);
  const positionRef = useRef(0);
  positionRef.current = position;
  // Latest callback without restarting the label polling when the page re-renders.
  const gone = useRef(onDeleted);
  gone.current = onDeleted;

  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let first = true;
    let failures = 0;
    const load = () =>
      fetchBallLabels(jobId)
        .then((data) => {
          if (stop) return;
          failures = 0;
          setError("");
          setRetrying(false);
          setState(data);
          if (first) {
            first = false;
            const start = data.frames.findIndex((f) => !(String(f.index) in data.labels));
            setPosition(start >= 0 ? start : 0);
          }
          // The worker extracts the frames from the video in one pass first.
          if (data.videoAvailable && !data.framesReady) timer = setTimeout(load, 3000);
        })
        .catch((e) => {
          if (stop) return;
          if (isMatchDeleted(e) && gone.current) {
            gone.current();
            return;
          }
          setError(e instanceof Error ? e.message : "Labels could not be loaded");
          // A refusal (not found, forbidden) stays; a restarting worker (network
          // error, 5xx, 429) comes back, so keep asking with a growing pause.
          const retry = !(e instanceof VisionError && e.status < 500 && e.status !== 429);
          setRetrying(retry);
          if (!retry) return;
          failures++;
          timer = setTimeout(load, Math.min(30_000, 3000 * 2 ** (failures - 1)));
        });
    load();
    return () => {
      stop = true;
      if (timer) clearTimeout(timer);
    };
  }, [jobId]);

  const frame = state?.frames[position];
  const label = frame && state ? state.labels[String(frame.index)] : undefined;
  const width = state?.size?.[0] || 640;
  const height = state?.size?.[1] || 360;
  const size = useMemo(() => [width, height] as const, [width, height]);
  const done = state ? state.frames.filter((f) => String(f.index) in state.labels).length : 0;
  const src = frame ? frameImageUrl(jobId, frame.index) : "";
  const loaded = !!src && loadedSrc.endsWith(src);
  // Fetch the next few unlabelled frames ahead, so answering does not wait for
  // a download each time (150 frames on a phone connection).
  useEffect(() => {
    if (!state?.framesReady) return;
    const ahead = state.frames.slice(position + 1).filter((f) => !(String(f.index) in state.labels)).slice(0, 3);
    // Not cancelled on change: a finished download stays in the browser cache.
    for (const f of ahead) new Image().src = frameImageUrl(jobId, f.index);
  }, [state, position, jobId]);

  const save = useCallback(
    async (body: { index: number; x: number; y: number } | { index: number; visible: false }) => {
      if (saving || !state) return;
      const from = state.frames.findIndex((f) => f.index === body.index);
      setSaving(true);
      setError("");
      try {
        const next = await sendBallLabel(jobId, body);
        setState((prev) => (prev ? { ...next, framesReady: prev.framesReady || next.framesReady } : next));
        // Move on from the frame that was labelled, and only if the reviewer is
        // still on it (they may have stepped elsewhere while it saved).
        const n = next.frames.length;
        let target = Math.min(n - 1, from + 1);
        for (let step = 1; step <= n; step++) {
          const i = (from + step) % n;
          if (!(String(next.frames[i].index) in next.labels)) {
            target = i;
            break;
          }
        }
        if (positionRef.current === from) {
          setZoom(null);
          setPosition(target);
        }
      } catch (e) {
        if (isMatchDeleted(e) && gone.current) gone.current();
        else setError(e instanceof Error ? e.message : "The label could not be saved");
      } finally {
        setSaving(false);
      }
    },
    [jobId, saving, state],
  );

  async function changeConsent(agreed: boolean) {
    setConsent(agreed);
    setConsentError("");
    try {
      const saved = await setTrainingConsent(jobId, agreed);
      setState((prev) => (prev ? { ...prev, trainingConsent: saved.trainingConsent } : prev));
    } catch (e) {
      if (isMatchDeleted(e) && gone.current) gone.current();
      else setConsentError(e instanceof Error ? e.message : "Your choice could not be saved");
    } finally {
      // Back to the worker's value: the new one once saved, the old one after a failure.
      setConsent(null);
    }
  }

  const accept = useCallback(() => {
    if (frame?.guess) save({ index: frame.index, x: frame.guess.x, y: frame.guess.y });
  }, [frame, save]);
  const absent = useCallback(() => frame && save({ index: frame.index, visible: false }), [frame, save]);
  const move = useCallback(
    (step: number) => {
      if (!state) return;
      setZoom(null);
      setPosition((p) => Math.min(state.frames.length - 1, Math.max(0, p + step)));
    },
    [state],
  );

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.ctrlKey || e.metaKey || e.altKey || e.repeat || e.defaultPrevented) return;
      const target = e.target as HTMLElement | null;
      if (target && (["INPUT", "TEXTAREA", "SELECT", "VIDEO"].includes(target.tagName) || target.isContentEditable)) return;
      if (target?.tagName === "BUTTON" && (e.key === " " || e.key === "Enter")) return;
      if (e.key === "Enter" || e.key === "y") accept();
      else if (e.key === "n") absent();
      else if (e.key === "s" || e.key === "ArrowRight") move(1);
      else if (e.key === "ArrowLeft") move(-1);
      else if (e.key === "Escape") setZoom(null);
      else return;
      e.preventDefault();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [accept, absent, move]);

  // Draw the magnified region around the first click.
  useEffect(() => {
    const img = image.current;
    const out = canvas.current;
    if (!zoom || !img || !out || !loaded || !img.complete || !img.naturalWidth) return;
    const w = size[0] / ZOOM;
    const h = size[1] / ZOOM;
    const x0 = Math.min(Math.max(0, zoom.x - w / 2), size[0] - w);
    const y0 = Math.min(Math.max(0, zoom.y - h / 2), size[1] - h);
    out.width = 960;
    out.height = Math.round((960 * h) / w);
    const ctx = out.getContext("2d");
    if (!ctx) return;
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(img, x0, y0, w, h, 0, 0, out.width, out.height);
    out.dataset.x0 = String(x0);
    out.dataset.y0 = String(y0);
    out.dataset.w = String(w);
  }, [zoom, loaded, size]);

  function frameClick(e: React.MouseEvent<HTMLImageElement>) {
    const box = e.currentTarget.getBoundingClientRect();
    setZoom({ x: ((e.clientX - box.left) / box.width) * size[0], y: ((e.clientY - box.top) / box.height) * size[1] });
  }

  function zoomClick(e: React.MouseEvent<HTMLCanvasElement>) {
    const el = e.currentTarget;
    const box = el.getBoundingClientRect();
    const x0 = Number(el.dataset.x0);
    const y0 = Number(el.dataset.y0);
    const w = Number(el.dataset.w);
    if (![x0, y0, w].every(Number.isFinite) || !box.width) return;  // not drawn yet
    const scale = w / box.width;
    const x = x0 + (e.clientX - box.left) * scale;
    const y = y0 + (e.clientY - box.top) * scale;
    if (frame) save({ index: frame.index, x: Math.round(x * 10) / 10, y: Math.round(y * 10) / 10 });
  }

  if (error && !state) return <p className="text-sm text-red-300">{error}{retrying ? " Checking again shortly." : ""}</p>;
  if (!state) return <p className="text-sm text-pitch-muted">Loading frames…</p>;
  if (!state.videoAvailable)
    return <p className="text-sm text-pitch-muted">The footage of this match is no longer stored, so its frames cannot be labelled.</p>;
  if (!state.framesReady)
    return (
      <div className="space-y-1">
        <p className="text-sm text-pitch-muted" data-testid="ball-frames-preparing">
          Preparing {state.frames.length} frames from your video. This reads the whole match once and takes a minute or two.
        </p>
        {error && <p className="text-sm text-amber-200">{error}{retrying ? " Checking again shortly." : ""}</p>}
      </div>
    );
  const m = state.metrics;
  // A player-load job never ran the ball model (the job says so, or the worker's
  // metrics do): nothing to score, so "—" rather than 0%; labels can still train.
  const unmeasured = !ballModel || m.measured === false;
  const circle = (x: number, y: number, colour: string) => (
    <span
      className="absolute rounded-full pointer-events-none"
      style={{ left: `${(x / size[0]) * 100}%`, top: `${(y / size[1]) * 100}%`, width: 22, height: 22, marginLeft: -11, marginTop: -11, border: `2px solid ${colour}` }}
    />
  );

  return (
    <div ref={root} tabIndex={-1} className="space-y-3 outline-none" data-testid="ball-labeller">
      <div className="grid sm:grid-cols-3 gap-2 text-sm">
        <div className="rounded-lg bg-white/5 p-3">
          <p className="text-[11px] uppercase tracking-widest text-pitch-muted">Ball found when visible</p>
          <p className="text-xl font-bold" data-testid="ball-recall">{unmeasured ? "—" : pct(m.recall)}</p>
          <p className="text-[11px] text-pitch-muted">
            {unmeasured ? "not measured" : m.recall.n ? `${range(m.recall)} · ${m.recall.n} frames you saw it` : "label frames to measure"}
          </p>
        </div>
        <div className="rounded-lg bg-white/5 p-3">
          <p className="text-[11px] uppercase tracking-widest text-pitch-muted">Ball positions that were right</p>
          <p className="text-xl font-bold" data-testid="ball-precision">{unmeasured ? "—" : pct(m.precision)}</p>
          <p className="text-[11px] text-pitch-muted">{!unmeasured && m.precision.n ? `${range(m.precision)} · within ${m.tolerancePixels} px` : "—"}</p>
        </div>
        <div className="rounded-lg bg-white/5 p-3">
          <p className="text-[11px] uppercase tracking-widest text-pitch-muted">Labelled</p>
          <p className="text-xl font-bold">
            {done} / {state.frames.length}
          </p>
          <p className="text-[11px] text-pitch-muted">Your answers measure ball accuracy for this match.</p>
        </div>
      </div>
      {unmeasured && (
        <p className="text-sm text-pitch-muted" data-testid="ball-not-run">
          The ball model was not run on this job; your labels can still train the ball finder if you allow it below.
        </p>
      )}

      {frame && (
        <div className="space-y-2">
          <div className="flex items-center justify-between text-sm">
            <span>
              <span className="font-mono text-pitch-green mr-2">{clockTime(frame.t)}</span>
              Frame {position + 1} of {state.frames.length}
              {label
                ? label.visible
                  ? " · you marked the ball"
                  : " · you marked: not visible"
                : frame.guess
                  ? " · yellow = Pitchlens' guess"
                  : unmeasured
                    ? " · ball not searched in this job"
                    : " · Pitchlens found no ball"}
            </span>
            <span className="text-[11px] text-pitch-muted hidden sm:inline">Enter accept · click the ball · N not visible · → skip</span>
          </div>
          <div className="relative rounded-lg overflow-hidden border border-white/10 bg-black">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              ref={image}
              key={src}
              src={src}
              alt={`Analysed frame at ${clockTime(frame.t)}`}
              className="w-full h-auto cursor-crosshair select-none"
              onLoad={(e) => setLoadedSrc(e.currentTarget.src)}
              onClick={frameClick}
              draggable={false}
            />
            {frame.guess && circle(frame.guess.x, frame.guess.y, "#facc15")}
            {label && label.visible && circle(label.x, label.y, "#22c55e")}
            {zoom && circle(zoom.x, zoom.y, "#38bdf8")}
          </div>
          {zoom && (
            <div className="space-y-1">
              <p className="text-xs text-pitch-muted flex items-center gap-1.5">
                <ZoomIn size={13} /> Click exactly on the ball (Esc to cancel)
              </p>
              <canvas ref={canvas} onClick={zoomClick} className="w-full rounded-lg border border-sky-400/40 cursor-crosshair" data-testid="ball-zoom" />
            </div>
          )}
          <div className="flex flex-wrap gap-2">
            <button className="pitch-button-secondary" onClick={() => move(-1)} disabled={position === 0}>
              <ChevronLeft size={16} /> Back
            </button>
            <button className="pitch-button-primary" onClick={accept} disabled={!frame.guess || saving}>
              <Check size={16} /> Guess is right
            </button>
            <button className="pitch-button-secondary" onClick={absent} disabled={saving}>
              <EyeOff size={16} /> Ball not visible
            </button>
            <button className="pitch-button-secondary" onClick={() => move(1)}>
              <SkipForward size={16} /> Skip
            </button>
          </div>
          {error && <p className="text-sm text-red-300">{error}</p>}
        </div>
      )}
      <div className="space-y-1">
        <label className="flex items-start gap-2 text-xs text-pitch-muted max-w-2xl cursor-pointer">
          <input
            type="checkbox"
            className="mt-0.5"
            data-testid="training-consent"
            checked={consent ?? state.trainingConsent === true}
            disabled={consent !== null}
            onChange={(e) => changeConsent(e.target.checked)}
          />
          <span>
            Let Pitchlens use these labelled frames to train the ball finder used for every analysis on this site.
            Frames are sent to our GPU provider, Modal, for training. You can withdraw at any time: this match is then
            left out of future training runs, but a model already trained keeps what it learned.
          </span>
        </label>
        {consentError && <p className="text-xs text-red-300">{consentError}</p>}
      </div>
      <BallTraining labelled={done} />
    </div>
  );
}

const pctOf = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${Math.round(v * 100)}%`);

/** Labelled frames a run needs: the worker holds back about 30% of a match and
 *  wants 30 visible balls there (MIN_TEST_VISIBLE in balltrain.py). */
const MIN_TRAINING_LABELS = 150;

/** Fine-tune the ball finder on every match labelled from this browser and allowed for training (GPU, about $1). */
export function BallTraining({ labelled }: { labelled: number }) {
  const [model, setModel] = useState<BallModelState | null>(null);
  const [error, setError] = useState("");
  // Whether this site may start training (an access code is set and the worker
  // allows it); otherwise the button could only fail.
  const [siteTraining, setSiteTraining] = useState<boolean | null>(null);
  const running = model?.training.state === "running";

  const refresh = useCallback(() => {
    fetchBallModel()
      .then(setModel)
      .catch((e) => setError(e instanceof Error ? e.message : "Training status unavailable"));
  }, []);
  useEffect(refresh, [refresh]);
  useEffect(() => {
    let stop = false;
    visionJson<VisionHealth>("health")
      .then((h) => !stop && setSiteTraining(h.siteTraining === true))
      .catch(() => !stop && setSiteTraining(false));
    return () => {
      stop = true;
    };
  }, []);
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(refresh, 5000);
    return () => clearInterval(timer);
  }, [running, refresh]);

  async function start() {
    setError("");
    try {
      await trainBallModel();
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Training could not start");
    }
  }

  const last = model?.training.run ?? model?.runs[model.runs.length - 1];
  // Most sites cannot start a run (an open site never can): no button that only
  // leads to a refusal. The site (/health) and the worker's status must both allow it.
  const allowed = siteTraining === true && model?.siteTraining !== false;
  const refused = siteTraining === false || model?.siteTraining === false;
  return (
    <div className="rounded-lg border border-white/10 p-4 space-y-2 text-sm" data-testid="ball-training">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="font-semibold">Train a better ball finder</p>
          {allowed ? (
            <p className="text-pitch-muted text-xs max-w-xl">
              Uses the frames you labelled in every match from this browser that you allowed for training. Part of
              them is held back to check the result; the new finder is switched on only if it finds more balls there
              without more false ones.
              {` One match is enough (about ${MIN_TRAINING_LABELS} labelled frames); more matches give a fairer check (${labelled} labelled here).`}
            </p>
          ) : refused ? (
            <p className="text-pitch-muted text-xs max-w-xl">Labels from matches you allow for training are used in the next ball-model update.</p>
          ) : null}
        </div>
        {allowed ? (
          <button className="pitch-button-primary" onClick={start} disabled={running || labelled < MIN_TRAINING_LABELS}>
            {running ? "Training…" : "Train on my labels"}
          </button>
        ) : refused ? (
          <p className="text-xs text-pitch-muted max-w-[14rem]" data-testid="training-operator">
            Training is run by the site operator.
          </p>
        ) : null}
      </div>
      {running && <p className="text-pitch-muted">{model?.training.stage}</p>}
      {model?.training.state === "failed" && <p className="text-amber-200">{model.training.stage}</p>}
      {last && last.baseline && last.candidate ? (
        <p className="text-pitch-muted" data-testid="ball-training-result">
          Last run: ball found {pctOf(last.baseline.recall)} → {pctOf(last.candidate.recall)} of visible balls on{" "}
          {last.candidate.frames} held-back frames ({last.split === "by-match" ? "other matches" : "later in the same match"}).{" "}
          {last.kept ? "Switched on for new analyses." : "Not better, so the current finder stays."}
        </p>
      ) : last ? (
        <p className="text-pitch-muted" data-testid="ball-training-result">
          Last run: {last.message ?? "stopped before both models were scored."} The current finder stays.
        </p>
      ) : null}
      {error && <p className="text-red-300">{error}</p>}
    </div>
  );
}
