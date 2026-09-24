"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { Navbar } from "@/components/ui/Navbar";
import {
  VisionResult,
  VisionJob,
  visionJson,
  VisionError,
  clockTime,
} from "@/lib/review/vision";
import { Loader2 } from "lucide-react";
import { MatchCentre } from "@/components/vision/MatchCentre";
import { matchStats } from "@/lib/review/visionStats";

export function VisionReport({ jobId }: { jobId: string }) {
  const [job, setJob] = useState<VisionJob | null>(null);
  const [result, setResult] = useState<VisionResult | null>(null);
  const [error, setError] = useState("");
  const [time, setTime] = useState(0);
  const [overlay, setOverlay] = useState(true);
  const [names, setNames] = useState(["Kit A", "Kit B"]);
  const [filter, setFilter] = useState("all");
  const player = useRef<HTMLVideoElement>(null);
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
  const frame = useMemo(() => {
    if (!result || !result.frames.length) return null;
    let l = 0,
      r = result.frames.length - 1;
    while (l < r) {
      const m = Math.ceil((l + r) / 2);
      if (result.frames[m].t <= time) l = m;
      else r = m - 1;
    }
    const f = result.frames[l];
    return Math.abs(f.t - time) <= (1 / result.sampleFps) * 1.5 ? f : null;
  }, [result, time]);
  const events = useMemo(
    () =>
      result?.metrics.events.filter(
        (e) => filter === "all" || e.type === filter,
      ) || [],
    [result, filter],
  );
  function seek(t: number) {
    if (player.current) {
      player.current.currentTime = t;
      player.current.pause();
      player.current.scrollIntoView({ behavior: "smooth", block: "center" });
      setTime(t);
    }
  }
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
          {error && (
            <p role="alert" className="text-red-300 glass-card p-4">
              {error}
            </p>
          )}
          {job && job.status !== "completed" && (
            <section className="glass-card p-6 space-y-4" aria-live="polite">
              <h2 className="text-xl flex gap-3 items-center">
                {["processing", "uploading"].includes(job.status) && (
                  <Loader2 className="animate-spin text-pitch-green" />
                )}
                {job.stage}
              </h2>
              <progress
                value={job.progress}
                max={100}
                className="w-full accent-green-500"
              />
              <p className="text-sm text-pitch-muted">
                {job.progress}%
                {job.processedSeconds !== undefined
                  ? ` · ${clockTime(job.processedSeconds)} processed`
                  : ""}
                {job.etaSeconds
                  ? ` · about ${clockTime(job.etaSeconds)} remaining`
                  : ""}
              </p>
              {["processing", "uploading"].includes(job.status) && (
                <button
                  className="pitch-button-secondary"
                  onClick={() =>
                    visionJson(`jobs/${jobId}/cancel`, {
                      method: "POST",
                    }).catch((e) => setError(e.message))
                  }
                >
                  Cancel analysis
                </button>
              )}
              {["failed", "interrupted", "cancelled"].includes(job.status) && (
                <Link href="/upload" className="pitch-button-primary">
                  Try another analysis
                </Link>
              )}
            </section>
          )}
          {result && (
            <>
              <MatchCentre
                result={result}
                stats={stats!}
                names={names}
                colours={[colour(0), colour(1)]}
                onSeek={seek}
              />
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
                      controls
                      playsInline
                      preload="metadata"
                      className="w-full h-full"
                      src={job?.videoDeleted ? undefined : `/api/vision/jobs/${jobId}/video`}
                      onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)}
                      onSeeked={(e) => setTime(e.currentTarget.currentTime)}
                      onError={() =>
                        !job?.videoDeleted &&
                        setError(
                          "Video playback failed. This browser may not support its codec. Use H.264 MP4.",
                        )
                      }
                    />
                    {overlay && frame && (
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
                            />
                            <text
                              x={frame.ball.x + 9}
                              y={frame.ball.y}
                              fill="#f8ef3d"
                              stroke="#111"
                              strokeWidth=".3"
                              fontSize="10"
                            >
                              ball
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
                    <p className="text-pitch-muted">
                      {clockTime(time)} · {frame?.players.length ?? 0} visible
                      tracks ·{" "}
                      {frame?.ball ? "ball detected" : "ball not observed"}
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
        const start = (result.analysedDuration * i) / 80,
          end = (result.analysedDuration * (i + 1)) / 80;
        const frames = result.frames.filter((f) => f.t >= start && f.t < end);
        return {
          start,
          rate: frames.length
            ? frames.filter((f) => f.ball).length / frames.length
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
        <span>0:00</span>
        <span>Click a bar to inspect</span>
        <span>{clockTime(result.analysedDuration)}</span>
      </div>
    </section>
  );
}
