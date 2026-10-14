"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { Crosshair, Loader2, RotateCcw, Undo2, CheckCircle2, AlertTriangle } from "lucide-react";
import {
  PITCH_PRESETS,
  PitchTemplate,
  Calibration,
  CalibrationFit,
  landmarks,
  previewCalibration,
  saveCalibration,
  fetchCalibration,
  fetchVenues,
  saveVenue,
  applyVenue,
  Venue,
} from "@/lib/review/analysis";
import { VisionError, clockTime, isMatchDeleted } from "@/lib/review/vision";
import { PitchSvg } from "@/components/vision/PitchGraphics";

export type CalibrationOverlay = {
  points: { name: string; x: number; y: number; label: string }[];
  linePoints: { line: string; x: number; y: number }[];
  lines: ([number, number] | null)[][] | null;
};

const LINES: { name: string; label: string }[] = [
  { name: "far-touchline", label: "Far touchline" },
  { name: "near-touchline", label: "Near touchline" },
  { name: "halfway-line", label: "Halfway line" },
  { name: "left-goal-line", label: "Left goal line" },
  { name: "right-goal-line", label: "Right goal line" },
];

/** A pitch setup whose progress has not moved for this long is reported as stuck. */
const STALL_MS = 10 * 60_000;

const QUALITY: Record<string, { label: string; colour: string }> = {
  good: { label: "Good fit", colour: "#22c55e" },
  check: { label: "Usable, check the lines", colour: "#f59e0b" },
  poor: { label: "Poor fit", colour: "#ef4444" },
  unverified: { label: "Cannot be checked yet", colour: "#94a3b8" },
};

export function PitchCalibration({
  jobId,
  videoSize,
  time,
  click,
  active,
  onActive,
  onOverlay,
  calibration,
  onApplied,
  onStep,
  videoAvailable = true,
  onDeleted,
  zoomed = false,
}: {
  jobId: string;
  videoSize: [number, number];
  /** The analysed frame's stored time when one is on screen (the playback time otherwise). */
  time: number;
  /** Latest click on the video in video pixels; n increments per click. */
  click: { x: number; y: number; n: number; moved?: boolean } | null;
  active: boolean;
  onActive: (active: boolean) => void;
  onOverlay: (overlay: CalibrationOverlay | null) => void;
  calibration: Calibration | null;
  onApplied: () => void;
  /** Move the paused video by this many seconds (the click layer covers the player's controls). */
  onStep: (seconds: number) => void;
  /** False once retention deleted the footage: landmarks cannot be clicked and a saved venue could not be checked. */
  videoAvailable?: boolean;
  /** The worker no longer has this match. */
  onDeleted?: () => void;
  /** The video shows a magnified crop: the next tap places the point. */
  zoomed?: boolean;
}) {
  const saved = calibration?.request;
  const [presetKey, setPresetKey] = useState<string>("five-a-side");
  const [template, setTemplate] = useState<PitchTemplate>(PITCH_PRESETS["five-a-side"].template);
  const [walls, setWalls] = useState(true);
  const [distortion, setDistortion] = useState<"auto" | "none" | "on">("auto");
  const [mode, setMode] = useState<"landmark" | "line">("landmark");
  const [landmarkName, setLandmarkName] = useState("corner-far-left");
  const [lineName, setLineName] = useState("far-touchline");
  const [points, setPoints] = useState<{ name: string; x: number; y: number }[]>([]);
  const [linePoints, setLinePoints] = useState<{ line: string; x: number; y: number }[]>([]);
  const [frameTime, setFrameTime] = useState<number | null>(null);
  const [preview, setPreview] = useState<{ fit: CalibrationFit; lines: ([number, number] | null)[][] } | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  /** Shows a failed request's message, or hands a deleted match to the page. */
  const fail = (e: unknown, message: string) => {
    if (isMatchDeleted(e) && onDeleted) onDeleted();
    else setError(message);
  };
  const [busy, setBusy] = useState<"" | "preview" | "save">("");
  const [progress, setProgress] = useState<number | null>(null);
  const handled = useRef(0);
  const marks = useMemo(() => landmarks(template), [template]);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const [venues, setVenues] = useState<Venue[]>([]);
  const [venueId, setVenueId] = useState("");
  const [venueName, setVenueName] = useState("");
  const [venueNote, setVenueNote] = useState("");
  useEffect(() => {
    fetchVenues()
      .then((list) => {
        setVenues(list);
        if (list.length) setVenueId(list[0].id);
      })
      .catch(() => setVenues([]));
  }, []);

  /** Follows the run on the worker. Re-aligning a moving camera through a full
   *  match can take half an hour on a CPU, so there is no fixed limit: it gives up
   *  only when the progress has not moved for 10 minutes. A restarting worker
   *  (network error, 5xx, 429) is retried. */
  async function waitForCalibration() {
    let last: number | null = null;
    let movedAt = Date.now();
    let failures = 0;
    for (;;) {
      await new Promise((r) => setTimeout(r, failures ? Math.min(15_000, 1500 * 2 ** failures) : 1500));
      // Leaving the page stops the polling (and skips the callbacks).
      if (!mounted.current) throw new DOMException("Left the page", "AbortError");
      if (Date.now() - movedAt > STALL_MS)
        throw new Error("The pitch setup has not moved on for 10 minutes. Reload the page to check on it.");
      let job: Calibration["job"];
      try {
        job = (await fetchCalibration(jobId, undefined, false)).job;
        failures = 0;
      } catch (e) {
        if (e instanceof VisionError && e.status < 500 && e.status !== 429) throw e;
        failures++;
        continue;
      }
      if (job?.state === "processing") {
        const progress = job.progress ?? 0;
        setProgress(progress);
        if (progress !== last) {
          last = progress;
          movedAt = Date.now();
        }
      } else if (job?.state === "failed") throw new Error(job.error || "Calibration failed");
      else if (job?.state === "done") return;
    }
  }

  async function useVenue() {
    setBusy("save");
    setError("");
    try {
      await applyVenue(jobId, venueId);
      setProgress(0);
      await waitForCalibration();
      setProgress(null);
      onApplied();
    } catch (e) {
      if (!mounted.current) return;
      setProgress(null);
      fail(e, e instanceof Error ? e.message : "The saved venue could not be applied");
    } finally {
      setBusy("");
    }
  }

  async function storeVenue() {
    setVenueNote("");
    setError("");
    try {
      const venue = await saveVenue(jobId, venueName.trim());
      setVenues((list) => [venue, ...list.filter((v) => v.id !== venue.id)]);
      setVenueNote(`Saved as "${venue.name}". Future matches from this camera can use it without clicks.`);
      setVenueName("");
    } catch (e) {
      fail(e, e instanceof Error ? e.message : "The venue could not be saved");
    }
  }

  // Start from the saved calibration so a correction does not mean starting over.
  useEffect(() => {
    if (!saved) return;
    setTemplate({ ...PITCH_PRESETS["five-a-side"].template, ...saved.template });
    setPresetKey("custom");
    setWalls(!!saved.walls);
    if (saved.distortion) setDistortion(saved.distortion);
    setPoints(saved.points || []);
    setLinePoints(saved.lines || []);
    setFrameTime(saved.t ?? null);
  }, [saved]);

  useEffect(() => {
    if (!active) {
      onOverlay(null);
      return;
    }
    onOverlay({
      points: points.map((p) => ({ ...p, label: String(points.indexOf(p) + 1) })),
      linePoints,
      lines: preview?.lines ?? null,
    });
  }, [active, points, linePoints, preview, onOverlay]);

  useEffect(() => {
    if (!active || !click || click.n === handled.current) return;
    handled.current = click.n;
    if (click.moved) {
      setError("Moved to the nearest analysed frame: the pitch setup has to use one. Click the landmark again.");
      return;
    }
    if (frameTime !== null && Math.abs(time - frameTime) > 0.08) {
      setError(`Clicks must all be on one frame (${clockTime(frameTime)}). Go back to it, or clear and start again.`);
      return;
    }
    setError("");
    if (frameTime === null) setFrameTime(time);
    setPreview(null);
    if (mode === "landmark") {
      setPoints((current) => {
        const next = current.filter((p) => p.name !== landmarkName).concat({ name: landmarkName, x: click.x, y: click.y });
        // Move on to the next landmark that has not been clicked yet.
        const used = new Set(next.map((p) => p.name));
        const following = marks.find((m) => !used.has(m.name));
        if (following) setLandmarkName(following.name);
        return next;
      });
    } else {
      setLinePoints((current) => current.concat({ line: lineName, x: click.x, y: click.y }));
    }
  }, [active, click, frameTime, time, mode, landmarkName, lineName, marks]);

  /** A new pitch may lack landmarks already clicked (a futsal court has no box
   *  corners): drop those clicks and pick a landmark the new pitch has, so the
   *  next click is never saved under a name the worker does not know. */
  function changeTemplate(next: PitchTemplate) {
    setTemplate(next);
    setPreview(null);
    const available = landmarks(next);
    const names = new Set(available.map((m) => m.name));
    const kept = points.filter((p) => names.has(p.name));
    const removed = points.length - kept.length;
    if (removed) setPoints(kept);
    if (!names.has(landmarkName)) {
      const used = new Set(kept.map((p) => p.name));
      setLandmarkName((available.find((m) => !used.has(m.name)) ?? available[0]).name);
    }
    // Nothing left on the chosen frame: the frame buttons work again.
    if (!kept.length && !linePoints.length) setFrameTime(null);
    setNotice(
      removed
        ? `${removed} point${removed === 1 ? " was" : "s were"} removed: this pitch has no such landmark${removed === 1 ? "" : "s"}.`
        : "",
    );
  }

  function choosePreset(key: string) {
    setPresetKey(key);
    if (PITCH_PRESETS[key]) changeTemplate(PITCH_PRESETS[key].template);
    else setPreview(null);
  }

  function setDim(key: keyof PitchTemplate, value: string) {
    const n = Number(value);
    changeTemplate({ ...template, [key]: value === "" ? null : Number.isFinite(n) ? n : template[key] });
    setPresetKey("custom");
  }

  const request = () => ({
    template: { ...template, walls },
    points,
    lines: linePoints,
    t: frameTime ?? time,
    distortion,
    walls,
  });

  async function check() {
    setBusy("preview");
    setError("");
    try {
      const out = await previewCalibration(jobId, request());
      setPreview({ fit: out.fit, lines: out.lines });
    } catch (e) {
      fail(e, e instanceof Error ? e.message : "The fit could not be checked");
      setPreview(null);
    } finally {
      setBusy("");
    }
  }

  async function apply() {
    setBusy("save");
    setError("");
    try {
      await saveCalibration(jobId, request());
      setProgress(0);
      // Line re-alignment through the match runs on the worker; poll it.
      await waitForCalibration();
      setProgress(null);
      onActive(false);
      onApplied();
    } catch (e) {
      if (!mounted.current) return;
      setProgress(null);
      fail(e, e instanceof VisionError && e.code === "access" ? "Enter the access code on the upload page first." : e instanceof Error ? e.message : "Calibration failed");
    } finally {
      setBusy("");
    }
  }

  const selected = marks.find((m) => m.name === landmarkName);
  const lineEnds: Record<string, [[number, number], [number, number]]> = {
    "far-touchline": [[0, 0], [template.length, 0]],
    "near-touchline": [[0, template.width], [template.length, template.width]],
    "halfway-line": [[template.length / 2, 0], [template.length / 2, template.width]],
    "left-goal-line": [[0, 0], [0, template.width]],
    "right-goal-line": [[template.length, 0], [template.length, template.width]],
  };
  const quality = preview ? QUALITY[preview.fit.quality] : null;
  const ready = calibration?.state === "ready";
  // A setup applied before a reload (or set up from a saved venue) still running on the worker.
  const running = calibration?.job?.state === "processing" && busy !== "save";

  if (!active)
    return (
      <section className="glass-card p-5 space-y-3" aria-label="Pitch setup">
        <div className="flex items-center justify-between gap-2">
          <h2 className="font-semibold">Pitch setup</h2>
          {ready && (
            <span className="text-xs px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-200">
              Calibrated · {calibration?.coverage ?? 0}% of frames
            </span>
          )}
        </div>
        <p className="text-sm text-pitch-muted">
          {ready
            ? `Positions are mapped onto a ${calibration?.template?.length} × ${calibration?.template?.width} m pitch. Shots, heatmaps and average positions use it.`
            : "Click a few pitch landmarks on one frame (two minutes). This unlocks shots, heatmaps, average positions and territory."}
        </p>
        {calibration?.job?.state === "failed" && (
          <p className="text-sm text-amber-200">Last attempt failed: {calibration.job.error}</p>
        )}
        {running && (
          <p className="text-sm text-pitch-muted flex items-center gap-2" role="status" data-testid="calibration-running">
            <Loader2 size={15} className="animate-spin shrink-0" />
            Applying the pitch setup to the match · {Math.round(calibration?.job?.progress ?? 0)}%. The report updates when it finishes.
          </p>
        )}
        {ready && calibration?.static === false && (calibration?.coverage ?? 0) < 60 && (
          <p className="text-sm text-amber-200">
            The camera moves, and only part of the match could be followed from your clicks. Positions outside those parts are left out, not guessed.
          </p>
        )}
        {calibration?.venue && (
          <p className="text-xs text-pitch-muted">
            {calibration.auto ? "Set up automatically from saved venue" : "From saved venue"} &quot;{calibration.venue.name}&quot;
            {calibration.venue.lineScore !== null ? ` · lines matched ${Math.round(calibration.venue.lineScore * 100)}%` : ""}
          </p>
        )}
        {!ready && venues.length > 0 && videoAvailable && (
          <div className="rounded-xl border border-white/10 p-3 space-y-2">
            <p className="text-sm font-semibold">Use a saved venue</p>
            <p className="text-xs text-pitch-muted">Same fixed camera as before? Apply its pitch setup without clicking; it is checked against the painted lines.</p>
            <div className="flex gap-2">
              {/* min-w-0: a long venue name must not widen the page past a phone screen. */}
              <select aria-label="Saved venue" className="pitch-input flex-1 min-w-0 w-full" value={venueId} onChange={(e) => setVenueId(e.target.value)}>
                {venues.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name} · {v.template.length}×{v.template.width} m
                  </option>
                ))}
              </select>
              <button className="pitch-button-secondary" disabled={!venueId || !!busy || running} onClick={useVenue}>
                {busy === "save" ? <Loader2 size={15} className="animate-spin" /> : null}
                {busy === "save" && progress !== null ? `${Math.round(progress)}%` : "Apply"}
              </button>
            </div>
          </div>
        )}
        {ready && !calibration?.venue && (
          <div className="rounded-xl border border-white/10 p-3 space-y-2">
            <p className="text-sm font-semibold">Save as a venue</p>
            <p className="text-xs text-pitch-muted">For a fixed camera: later matches from it reuse this setup automatically.</p>
            <div className="flex gap-2">
              <input aria-label="Venue name" maxLength={80} placeholder="e.g. Tekkerz Court 2" className="pitch-input flex-1 min-w-0 w-full" value={venueName} onChange={(e) => setVenueName(e.target.value)} />
              <button className="pitch-button-secondary" disabled={!venueName.trim()} onClick={storeVenue}>
                Save
              </button>
            </div>
          </div>
        )}
        {venueNote && <p className="text-xs text-emerald-200">{venueNote}</p>}
        {error && !active && (
          <p role="alert" className="text-sm text-red-300">
            {error}
          </p>
        )}
        {!videoAvailable && (
          <p className="text-sm text-pitch-muted" id="pitch-setup-unavailable">
            Pitch setup needs the footage, which was deleted after the retention period.
          </p>
        )}
        <button
          className="pitch-button-secondary w-full"
          disabled={running || !videoAvailable}
          aria-describedby={videoAvailable ? undefined : "pitch-setup-unavailable"}
          onClick={() => onActive(true)}
        >
          <Crosshair size={16} /> {ready ? "Adjust pitch setup" : "Set up the pitch"}
        </button>
      </section>
    );

  const lineLabel = LINES.find((l) => l.name === lineName)?.label ?? "the line";
  const allPlaced = mode === "landmark" && marks.every((m) => points.some((p) => p.name === m.name));
  const undo = () => {
    setPreview(null);
    if (mode === "line" && linePoints.length) setLinePoints((l) => l.slice(0, -1));
    else setPoints((p) => p.slice(0, -1));
  };
  return (
    <section className="glass-card p-5 space-y-4" aria-label="Pitch setup">
      {/* First in the panel, and data-reveal: on a phone the page scrolls this strip to
          just below the pinned video, so what the next tap places is always in view. */}
      <div
        className="flex items-center gap-2 rounded-xl border border-pitch-green/40 bg-pitch-green/10 p-2 pl-3 text-sm"
        data-reveal
        data-testid="calibration-next"
      >
        <p className="flex-1 min-w-0" aria-live="polite">
          {allPlaced
            ? "Every landmark is placed · check the fit below"
            : zoomed
              ? `Place ${mode === "landmark" ? selected?.label ?? "the landmark" : `a point on the ${lineLabel.toLowerCase()}`} · tap exactly on it`
              : `Next: ${mode === "landmark" ? selected?.label ?? "a landmark" : `a point on the ${lineLabel.toLowerCase()}`} · tap it on the video`}
        </p>
        <button className="pitch-button-secondary min-h-11 px-3 shrink-0" disabled={!points.length && !linePoints.length} onClick={undo}>
          <Undo2 size={15} /> Undo
        </button>
        <button className="pitch-button-secondary min-h-11 px-3 shrink-0" onClick={() => onActive(false)}>
          Close
        </button>
      </div>
      <h2 className="font-semibold">Pitch setup</h2>
      <div className="flex flex-wrap items-center gap-1 text-sm" role="group" aria-label="Choose the frame">
        <span className="text-pitch-muted mr-1">Frame {clockTime(time)}</span>
        {(
          [
            [-5, "−5 s"],
            [-0.01, "◀ frame"],
            [0.01, "frame ▶"],
            [5, "+5 s"],
          ] as const
        ).map(([delta, label]) => (
          <button
            key={label}
            className="px-2 py-1 rounded-md bg-white/5 hover:bg-white/10 disabled:opacity-40"
            disabled={frameTime !== null}
            title={frameTime !== null ? "Clear the clicks to choose another frame" : `Move ${delta} s`}
            onClick={() => onStep(delta)}
          >
            {label}
          </button>
        ))}
      </div>
      <div role="tablist" aria-label="Click type" className="flex gap-1 p-1 rounded-lg bg-white/5 text-sm">
        {(
          [
            ["landmark", "Landmarks"],
            ["line", "Points on a line"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            role="tab"
            aria-selected={mode === key}
            onClick={() => setMode(key)}
            className={`flex-1 py-1.5 rounded-md ${mode === key ? "bg-pitch-indigo-soft/50" : "text-pitch-muted"}`}
          >
            {label}
          </button>
        ))}
      </div>
      {mode === "landmark" ? (
        <label className="block text-sm">
          Next click places
          <select aria-label="Landmark to place" className="pitch-input w-full mt-1" value={landmarkName} onChange={(e) => setLandmarkName(e.target.value)}>
            {marks.map((m) => (
              <option key={m.name} value={m.name}>
                {points.some((p) => p.name === m.name) ? "✓ " : ""}
                {m.label}
              </option>
            ))}
          </select>
        </label>
      ) : (
        <label className="block text-sm">
          Next click lies on
          <select aria-label="Line to trace" className="pitch-input w-full mt-1" value={lineName} onChange={(e) => setLineName(e.target.value)}>
            {LINES.map((l) => (
              <option key={l.name} value={l.name}>
                {l.label} ({linePoints.filter((p) => p.line === l.name).length})
              </option>
            ))}
          </select>
        </label>
      )}
      <PitchSvg template={template} label="Where the selected landmark is on the pitch">
        {mode === "landmark" && selected && (
          <circle cx={selected.x} cy={selected.y} r={Math.max(0.8, template.length / 40)} fill="#f8ef3d" stroke="#111" strokeWidth={0.2} />
        )}
        {mode === "line" && lineEnds[lineName] && (
          <line
            x1={lineEnds[lineName][0][0]}
            y1={lineEnds[lineName][0][1]}
            x2={lineEnds[lineName][1][0]}
            y2={lineEnds[lineName][1][1]}
            stroke="#f8ef3d"
            strokeWidth={Math.max(0.4, template.length / 80)}
          />
        )}
        {points.map((p) => {
          const m = marks.find((x) => x.name === p.name);
          return m ? <circle key={p.name} cx={m.x} cy={m.y} r={Math.max(0.4, template.length / 90)} fill="#22c55e" /> : null;
        })}
      </PitchSvg>
      <p className="text-xs text-pitch-muted" aria-live="polite">
        {points.length} landmark{points.length === 1 ? "" : "s"} · {linePoints.length} line point{linePoints.length === 1 ? "" : "s"}
        {frameTime !== null ? ` · frame ${clockTime(frameTime)}` : " · click on the paused video"}
      </p>
      {notice && (
        <p className="text-xs text-amber-200" role="status">
          {notice}
        </p>
      )}
      <button
        className="pitch-button-secondary w-full"
        onClick={() => {
          setPoints([]);
          setLinePoints([]);
          setFrameTime(null);
          setPreview(null);
          setError("");
          setNotice("");
          setLandmarkName(marks[0].name);
        }}
      >
        <RotateCcw size={15} /> Clear
      </button>
      <ol className="text-sm text-pitch-muted list-decimal pl-5 space-y-1">
        <li>Pause on a frame where you can see several pitch lines.</li>
        <li>Pick a landmark above, then tap exactly on it in the video (on a phone the first tap zooms in). Aim for 6 or more, spread out.</li>
        <li>Low cameras rarely see the near corners: add points along any straight line you can see.</li>
      </ol>
      <div className="grid grid-cols-2 gap-2 text-sm">
        <label className="col-span-2">
          Pitch type
          <select aria-label="Pitch type" className="pitch-input w-full mt-1" value={presetKey} onChange={(e) => choosePreset(e.target.value)}>
            {Object.entries(PITCH_PRESETS).map(([key, p]) => (
              <option key={key} value={key}>
                {p.label}
              </option>
            ))}
            <option value="custom">Custom size</option>
          </select>
        </label>
        {(
          [
            ["length", "Length (m)"],
            ["width", "Width (m)"],
            ["goalWidth", "Goal width (m)"],
            ["centreRadius", "Centre circle radius (m)"],
          ] as const
        ).map(([key, label]) => (
          <label key={key}>
            {label}
            <input
              aria-label={label}
              type="number"
              step="0.1"
              min="0"
              className="pitch-input w-full mt-1"
              value={template[key] ?? ""}
              onChange={(e) => setDim(key, e.target.value)}
            />
          </label>
        ))}
        <label className="col-span-2 flex gap-2 items-center">
          <input type="checkbox" checked={walls} onChange={(e) => setWalls(e.target.checked)} />
          Walled cage (the ball cannot go out of play)
        </label>
        <p className="col-span-2 text-xs text-pitch-muted">
          Unsure of the size? Pick the closest preset: heatmaps and zones stay right, distances become approximate.
        </p>
      </div>
      <details className="text-sm">
        <summary className="cursor-pointer text-pitch-muted">Lens (advanced)</summary>
        <select aria-label="Lens distortion" className="pitch-input w-full mt-2" value={distortion} onChange={(e) => setDistortion(e.target.value as typeof distortion)}>
          <option value="auto">Detect wide-angle distortion automatically</option>
          <option value="none">Straight lens (no distortion)</option>
          <option value="on">Always correct distortion</option>
        </select>
      </details>
      {error && (
        <p role="alert" className="text-sm text-red-300">
          {error}
        </p>
      )}
      <button className="pitch-button-secondary w-full" disabled={points.length < 4 || !!busy} onClick={check}>
        {busy === "preview" ? <Loader2 size={16} className="animate-spin" /> : <CheckCircle2 size={16} />} Check fit
      </button>
      {preview && quality && (
        <div className="rounded-xl border border-white/10 p-3 space-y-2 text-sm" aria-live="polite">
          <div className="flex items-center justify-between">
            <span className="font-semibold" style={{ color: quality.colour }}>
              {quality.label}
            </span>
            <span className="text-pitch-muted">
              {preview.fit.rmsPixels.toFixed(1)} px average error
            </span>
          </div>
          <p className="text-xs text-pitch-muted">
            The projected lines are drawn on the video: they should sit on the painted lines everywhere, not just near your clicks.
            {preview.fit.fieldOfView ? ` Implied lens: about ${Math.round(preview.fit.fieldOfView)}° wide` : ""}
            {preview.fit.cameraHeight ? `, camera about ${preview.fit.cameraHeight.toFixed(1)} m up.` : "."}
          </p>
          {preview.fit.warnings.map((w) => (
            <p key={w} className="text-xs text-amber-200 flex gap-1.5">
              <AlertTriangle size={13} className="shrink-0 mt-0.5" />
              {w}
            </p>
          ))}
          <ul className="text-xs text-pitch-muted max-h-32 overflow-auto">
            {preview.fit.residuals.map((r, i) => (
              <li key={r.name} className="flex justify-between">
                <span>
                  {i + 1}. {marks.find((m) => m.name === r.name)?.label || r.name}
                </span>
                <span>
                  {r.pixels.toFixed(1)} px{r.leftOut !== null ? ` · ${r.leftOut.toFixed(1)} px left out` : ""}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
      <button
        className="pitch-button-primary w-full py-3"
        disabled={!preview || preview.fit.quality === "poor" || !!busy}
        onClick={apply}
      >
        {busy === "save" ? (
          <>
            <Loader2 size={16} className="animate-spin" /> Applying to the match{progress !== null ? ` · ${Math.round(progress)}%` : "…"}
          </>
        ) : (
          "Apply to the whole match"
        )}
      </button>
    </section>
  );
}
