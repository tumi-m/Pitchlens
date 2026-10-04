"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Check, EyeOff, SkipForward, ChevronLeft, ZoomIn } from "lucide-react";
import type { BallLabelState, BallModelState, Rate } from "@/lib/review/analysis";
import { fetchBallLabels, fetchBallModel, frameImageUrl, sendBallLabel, trainBallModel } from "@/lib/review/analysis";
import { clockTime } from "@/lib/review/vision";

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
 * visible. The answers measure the ball finder on this match and become
 * training data for a better one.
 */
export function BallLabeller({ jobId }: { jobId: string }) {
  const [state, setState] = useState<BallLabelState | null>(null);
  const [position, setPosition] = useState(0);
  const [zoom, setZoom] = useState<{ x: number; y: number } | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const image = useRef<HTMLImageElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    fetchBallLabels(jobId)
      .then((data) => {
        setState(data);
        const first = data.frames.findIndex((f) => !(String(f.index) in data.labels));
        setPosition(first >= 0 ? first : 0);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Labels could not be loaded"));
  }, [jobId]);

  const frame = state?.frames[position];
  const label = frame && state ? state.labels[String(frame.index)] : undefined;
  const width = state?.size?.[0] || 640;
  const height = state?.size?.[1] || 360;
  const size = useMemo(() => [width, height] as const, [width, height]);
  const done = state ? state.frames.filter((f) => String(f.index) in state.labels).length : 0;

  const advance = useCallback(
    (next: BallLabelState) => {
      // Next unlabelled frame after this one (wrapping), else stay.
      const n = next.frames.length;
      for (let step = 1; step <= n; step++) {
        const i = (position + step) % n;
        if (!(String(next.frames[i].index) in next.labels)) return setPosition(i);
      }
      setPosition(Math.min(n - 1, position + 1));
    },
    [position],
  );

  const save = useCallback(
    async (body: { index: number; x: number; y: number } | { index: number; visible: false }) => {
      if (saving) return;
      setSaving(true);
      setError("");
      try {
        const next = await sendBallLabel(jobId, body);
        setState(next);
        setZoom(null);
        setLoaded(false);
        advance(next);
      } catch (e) {
        setError(e instanceof Error ? e.message : "The label could not be saved");
      } finally {
        setSaving(false);
      }
    },
    [jobId, saving, advance],
  );

  const accept = useCallback(() => {
    if (frame?.guess) save({ index: frame.index, x: frame.guess.x, y: frame.guess.y });
  }, [frame, save]);
  const absent = useCallback(() => frame && save({ index: frame.index, visible: false }), [frame, save]);
  const move = useCallback(
    (step: number) => {
      if (!state) return;
      setZoom(null);
      setLoaded(false);
      setPosition((p) => Math.min(state.frames.length - 1, Math.max(0, p + step)));
    },
    [state],
  );

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const target = e.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
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
    if (!zoom || !img || !out || !loaded) return;
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
    const scale = w / box.width;
    const x = x0 + (e.clientX - box.left) * scale;
    const y = y0 + (e.clientY - box.top) * scale;
    if (frame) save({ index: frame.index, x: Math.round(x * 10) / 10, y: Math.round(y * 10) / 10 });
  }

  if (error && !state) return <p className="text-sm text-red-300">{error}</p>;
  if (!state) return <p className="text-sm text-pitch-muted">Loading frames…</p>;
  if (!state.videoAvailable)
    return <p className="text-sm text-pitch-muted">The footage of this match is no longer stored, so its frames cannot be labelled.</p>;
  const m = state.metrics;
  const circle = (x: number, y: number, colour: string) => (
    <span
      className="absolute rounded-full pointer-events-none"
      style={{ left: `${(x / size[0]) * 100}%`, top: `${(y / size[1]) * 100}%`, width: 22, height: 22, marginLeft: -11, marginTop: -11, border: `2px solid ${colour}` }}
    />
  );

  return (
    <div className="space-y-3" data-testid="ball-labeller">
      <div className="grid sm:grid-cols-3 gap-2 text-sm">
        <div className="rounded-lg bg-white/5 p-3">
          <p className="text-[11px] uppercase tracking-widest text-pitch-muted">Ball found when visible</p>
          <p className="text-xl font-bold" data-testid="ball-recall">{pct(m.recall)}</p>
          <p className="text-[11px] text-pitch-muted">{m.recall.n ? `${range(m.recall)} · ${m.recall.n} frames you saw it` : "label frames to measure"}</p>
        </div>
        <div className="rounded-lg bg-white/5 p-3">
          <p className="text-[11px] uppercase tracking-widest text-pitch-muted">Ball positions that were right</p>
          <p className="text-xl font-bold">{pct(m.precision)}</p>
          <p className="text-[11px] text-pitch-muted">{m.precision.n ? `${range(m.precision)} · within ${m.tolerancePixels} px` : "—"}</p>
        </div>
        <div className="rounded-lg bg-white/5 p-3">
          <p className="text-[11px] uppercase tracking-widest text-pitch-muted">Labelled</p>
          <p className="text-xl font-bold">
            {done} / {state.frames.length}
          </p>
          <p className="text-[11px] text-pitch-muted">every answer also becomes training data</p>
        </div>
      </div>

      {frame && (
        <div className="space-y-2">
          <div className="flex items-center justify-between text-sm">
            <span>
              <span className="font-mono text-pitch-green mr-2">{clockTime(frame.t)}</span>
              Frame {position + 1} of {state.frames.length}
              {label ? (label.visible ? " · you marked the ball" : " · you marked: not visible") : frame.guess ? " · yellow = Pitchlens' guess" : " · Pitchlens found no ball"}
            </span>
            <span className="text-[11px] text-pitch-muted hidden sm:inline">Enter accept · click the ball · N not visible · → skip</span>
          </div>
          <div className="relative rounded-lg overflow-hidden border border-white/10 bg-black">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              ref={image}
              src={frameImageUrl(jobId, frame.index)}
              alt={`Analysed frame at ${clockTime(frame.t)}`}
              className="w-full h-auto cursor-crosshair select-none"
              onLoad={() => setLoaded(true)}
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
      <BallTraining labelled={done} />
    </div>
  );
}

const pctOf = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${Math.round(v * 100)}%`);

/** Fine-tune the ball finder on every match labelled from this browser (GPU, about $1). */
export function BallTraining({ labelled }: { labelled: number }) {
  const [model, setModel] = useState<BallModelState | null>(null);
  const [error, setError] = useState("");
  const running = model?.training.state === "running";

  const refresh = useCallback(() => {
    fetchBallModel()
      .then(setModel)
      .catch((e) => setError(e instanceof Error ? e.message : "Training status unavailable"));
  }, []);
  useEffect(refresh, [refresh]);
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
  return (
    <div className="rounded-lg border border-white/10 p-4 space-y-2 text-sm" data-testid="ball-training">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="font-semibold">Train a better ball finder</p>
          <p className="text-pitch-muted text-xs max-w-xl">
            Uses the frames you labelled in every match from this browser. Some matches are held back to check the
            result; the new finder is switched on only if it finds more balls there without more false ones.
            Label at least two matches first ({labelled} frames labelled here).
          </p>
        </div>
        <button className="pitch-button-primary" onClick={start} disabled={running || labelled < 30}>
          {running ? "Training…" : "Train on my labels"}
        </button>
      </div>
      {running && <p className="text-pitch-muted">{model?.training.stage}</p>}
      {model?.training.state === "failed" && <p className="text-amber-200">{model.training.stage}</p>}
      {last && (
        <p className="text-pitch-muted" data-testid="ball-training-result">
          Last run: ball found {pctOf(last.baseline.recall)} → {pctOf(last.candidate.recall)} of visible balls on{" "}
          {last.candidate.frames} held-back frames ({last.split === "by-match" ? "other matches" : "later in the same match"}).{" "}
          {last.kept ? "Switched on for new analyses." : "Not better, so the current finder stays."}
        </p>
      )}
      {error && <p className="text-red-300">{error}</p>}
    </div>
  );
}
