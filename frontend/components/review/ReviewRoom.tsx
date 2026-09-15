"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AnalyseSavedVideo } from "@/components/vision/AnalyseSavedVideo";
import { EditEvent } from "./EditEvent";
import { RelinkVideo } from "./RelinkVideo";
import { ChevronLeft, Download, Trash2, Pencil } from "lucide-react";
import { Navbar } from "@/components/ui/Navbar";
import { loadVideo, deleteVideo } from "@/lib/review/videoStore";
import { saveMatchLocally, removeLocalMatch } from "@/lib/firebase/firestore";
import { summariseEvents, ReviewEvent, VideoReview } from "@/lib/review/types";
import type { Match } from "@/lib/types";
import toast from "react-hot-toast";

const time = (s: number) =>
  `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
export function ReviewRoom({ match }: { match: Match }) {
  const router = useRouter();
  const review = match.review!;
  const player = useRef<HTMLVideoElement>(null);
  const [url, setUrl] = useState("");
  const [videoRevision, setVideoRevision] = useState(0);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [removedEvent, setRemovedEvent] = useState<ReviewEvent | null>(null);
  const [videoError, setVideoError] = useState("");
  const [timestamp, setTimestamp] = useState(0);
  const [team, setTeam] = useState<"home" | "away">("home");
  const [note, setNote] = useState("");
  const [notes, setNotes] = useState(review.notes);
  const [frameIndex, setFrameIndex] = useState(0);
  const [deleting, setDeleting] = useState(false);
  const [exportText, setExportText] = useState("");
  const [exportUrl, setExportUrl] = useState("");
  useEffect(
    () => () => {
      if (exportUrl) URL.revokeObjectURL(exportUrl);
    },
    [exportUrl],
  );
  useEffect(() => {
    setUrl("");
    setVideoError("");
    let cancelled = false;
    let objectUrl = "";
    loadVideo(match.id)
      .then((blob) => {
        if (cancelled) return;
        if (!blob) {
          setVideoError(
            "The saved video is no longer available on this device. Your tags are still saved.",
          );
          return;
        }
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      })
      .catch(() =>
        setVideoError(
          "Unable to open saved video. Your tags are still available.",
        ),
      );
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [match.id, videoRevision]);
  function save(next: VideoReview) {
    try {
      saveMatchLocally(match.id, { review: next });
      return true;
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Save failed");
      return false;
    }
  }
  function tag(type: ReviewEvent["type"]) {
    if (review.events.length >= 10000) {
      toast.error("This review has reached its 10,000-tag limit.");
      return;
    }
    const event: ReviewEvent = {
      id: crypto.randomUUID(),
      timestamp: Math.min(
        review.duration,
        Math.max(0, player.current?.currentTime ?? timestamp),
      ),
      team,
      type,
      note: note.trim(),
      source: "manual",
    };
    if (
      save({
        ...review,
        events: [...review.events, event].sort(
          (a, b) => a.timestamp - b.timestamp,
        ),
      })
    ) {
      setNote("");
      toast.success(`${type} tagged at ${time(event.timestamp)}`);
    }
  }
  function seek(t: number) {
    if (player.current) {
      player.current.currentTime = t;
      player.current.pause();
      setTimestamp(t);
    }
  }
  function download() {
    const payload = {
      schemaVersion: 1,
      title: match.title,
      homeTeam: match.homeTeamName,
      awayTeam: match.awayTeamName,
      exportedAt: new Date().toISOString(),
      review: { ...review, notes },
      summary: summariseEvents(review.events),
      limitations:
        "Manual tags are observations, not exhaustive match totals. Goals count as shot attempts. No automated possession, xG or pass completion is measured. Video is not included.",
    };
    const text = JSON.stringify(payload, null, 2);
    setExportText(text);
    setExportUrl(
      URL.createObjectURL(new Blob([text], { type: "application/json" })),
    );
  }

  async function remove() {
    if (
      !window.confirm(
        "Delete this review and its saved video from this device? Export first if you need a backup.",
      )
    )
      return;
    setDeleting(true);
    try {
      await deleteVideo(match.id);
      removeLocalMatch(match.id);
      router.push("/dashboard");
    } catch {
      toast.error("Could not delete this review. Please retry.");
      setDeleting(false);
    }
  }
  const summary = summariseEvents(review.events);
  const frame = review.frames[frameIndex];
  return (
    <>
      <Navbar />
      <main className="pt-24 pb-16 px-4">
        <div className="max-w-7xl mx-auto space-y-7">
          <AnalyseSavedVideo id={match.id} title={match.title} />
          <header className="flex flex-wrap justify-between gap-4">
            <div>
              <Link
                href="/dashboard"
                className="flex items-center gap-1 text-pitch-muted text-sm mb-3"
              >
                <ChevronLeft size={15} />
                Your matches
              </Link>
              <h1 className="text-3xl font-bold">{match.title}</h1>
              <p className="text-pitch-muted text-sm mt-2">
                {time(review.duration)} · {review.width} × {review.height} ·
                Saved on this device
              </p>
            </div>
            <div className="flex gap-2 items-center">
              <button onClick={download} className="pitch-button-secondary">
                <Download size={16} />
                Export review
              </button>
              <button
                aria-label="Delete review"
                onClick={remove}
                disabled={deleting}
                className="pitch-button-ghost"
              >
                <Trash2 size={17} />
              </button>
            </div>
          </header>
          {review.importedAt && (
            <p className="rounded-xl bg-amber-500/10 text-amber-200 p-4 text-sm">
              Imported review. Tags, notes and frame detections came from the
              supplied file and have not been independently verified.
            </p>
          )}
          {exportText && (
            <section
              className="glass-card p-5 space-y-3"
              aria-label="Export your review"
            >
              <h2 className="font-semibold">Your review is ready to export</h2>
              <p className="text-sm text-pitch-muted">
                Download a JSON backup, or copy it if downloads are unavailable
                in your browser. The video is not included.
              </p>
              <div className="flex flex-wrap gap-2">
                <a
                  href={exportUrl}
                  download={`pitchlens-${match.id}.json`}
                  className="pitch-button-primary"
                >
                  Download JSON
                </a>
                <button
                  className="pitch-button-secondary"
                  onClick={async () => {
                    try {
                      await navigator.clipboard.writeText(exportText);
                      toast.success("Review copied");
                    } catch {
                      toast.error(
                        "Select the review text below and copy it manually.",
                      );
                    }
                  }}
                >
                  Copy review
                </button>
                <button
                  className="pitch-button-ghost"
                  onClick={() => {
                    setExportText("");
                    setExportUrl("");
                  }}
                >
                  Close export
                </button>
              </div>
              <details>
                <summary className="text-sm text-pitch-muted cursor-pointer">
                  Preview exported data
                </summary>
                <textarea
                  aria-label="Review JSON"
                  readOnly
                  value={exportText}
                  rows={8}
                  className="pitch-input w-full font-mono text-xs mt-3"
                />
              </details>
            </section>
          )}
          {videoError && (
            <RelinkVideo
              matchId={match.id}
              review={review}
              onLinked={() => setVideoRevision((n) => n + 1)}
            />
          )}
          <div className="grid lg:grid-cols-[minmax(0,1fr)_340px] gap-6">
            <section className="space-y-4 min-w-0">
              <div className="bg-black rounded-2xl overflow-hidden aspect-video flex items-center justify-center">
                {url ? (
                  <video
                    ref={player}
                    src={url}
                    controls
                    playsInline
                    preload="metadata"
                    className="w-full h-full"
                    onTimeUpdate={(e) =>
                      setTimestamp(e.currentTarget.currentTime)
                    }
                    onError={() =>
                      setVideoError(
                        "Video playback failed. Try converting the source to H.264 MP4.",
                      )
                    }
                  />
                ) : (
                  <p className="text-pitch-muted p-8">
                    {videoError || "Opening your video…"}
                  </p>
                )}
              </div>
              {url && videoError && (
                <p role="alert" className="text-red-300">
                  {videoError}
                </p>
              )}
              <div className="glass-card p-5 space-y-4">
                <div className="flex justify-between gap-3">
                  <h2 className="font-semibold">Tag a moment</h2>
                  <span className="font-mono text-pitch-green">
                    {time(timestamp)}
                  </span>
                </div>
                <p className="text-sm text-pitch-muted">
                  Pause at the moment, choose the team, then add a tag. A goal
                  also counts as a shot attempt.
                </p>
                <div className="flex gap-2">
                  {(["home", "away"] as const).map((side) => (
                    <button
                      key={side}
                      aria-pressed={team === side}
                      onClick={() => setTeam(side)}
                      className={
                        team === side
                          ? "pitch-button-primary"
                          : "pitch-button-secondary"
                      }
                    >
                      {side === "home"
                        ? match.homeTeamName
                        : match.awayTeamName}
                    </button>
                  ))}
                </div>
                <input
                  aria-label="Event note"
                  value={note}
                  maxLength={300}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder="Optional note — player, build-up, coaching point"
                  className="pitch-input w-full"
                />
                <div className="flex flex-wrap gap-2">
                  {(
                    ["goal", "shot", "save", "pass", "foul", "corner"] as const
                  ).map((type) => (
                    <button
                      key={type}
                      disabled={!url}
                      onClick={() => tag(type)}
                      className="pitch-button-secondary capitalize"
                    >
                      {type}
                    </button>
                  ))}
                </div>
              </div>
              <div className="glass-card p-5">
                <h2 className="font-semibold mb-3">Coach’s notes</h2>
                <textarea
                  aria-label="Coach’s notes"
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  maxLength={10000}
                  rows={4}
                  placeholder="What should the team work on next?"
                  className="pitch-input w-full"
                />
                <button
                  onClick={() => {
                    if (save({ ...review, notes }))
                      toast.success("Notes saved");
                  }}
                  className="pitch-button-secondary mt-3"
                >
                  Save notes
                </button>
                {notes !== review.notes && (
                  <span className="text-xs text-amber-300 ml-3">
                    Unsaved changes
                  </span>
                )}
              </div>
            </section>
            <aside className="space-y-5">
              <div className="glass-card p-5">
                <h2 className="font-semibold mb-2">Tagged observations</h2>
                <p className="text-pitch-muted text-xs mb-5">
                  Counts from your tags, not final match totals. No tags means
                  nothing has been recorded yet.
                </p>
                {summary.map((s) => (
                  <div key={s.team} className="mb-5 last:mb-0">
                    <p className="font-medium mb-2">
                      {s.team === "home"
                        ? match.homeTeamName
                        : match.awayTeamName}
                    </p>
                    <div className="grid grid-cols-3 gap-2">
                      {(
                        [
                          "goals",
                          "shots",
                          "saves",
                          "passes",
                          "fouls",
                          "corners",
                        ] as const
                      ).map((key) => (
                        <div
                          key={key}
                          className="bg-pitch-black/40 rounded-lg p-2"
                        >
                          <p className="text-xl font-bold">{s[key]}</p>
                          <p className="text-xs text-pitch-muted capitalize">
                            {key}
                          </p>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
              <div className="glass-card p-5">
                <h2 className="font-semibold mb-4">
                  Timeline{" "}
                  <span className="text-pitch-muted">
                    {review.events.length}
                  </span>
                </h2>
                {!review.events.length && (
                  <p className="text-sm text-pitch-muted">
                    Your tagged moments appear here. Click a timestamp to jump
                    back to the footage.
                  </p>
                )}
                {removedEvent && (
                  <button
                    className="pitch-button-secondary mb-3"
                    onClick={() => {
                      if (review.events.length >= 10000) {
                        toast.error("Tag limit reached.");
                        return;
                      }
                      if (
                        save({
                          ...review,
                          events: [
                            ...review.events.filter(
                              (e) => e.id !== removedEvent.id,
                            ),
                            removedEvent,
                          ].sort((a, b) => a.timestamp - b.timestamp),
                        })
                      )
                        setRemovedEvent(null);
                    }}
                  >
                    Undo last removal
                  </button>
                )}
                <ol className="space-y-3 max-h-[520px] overflow-y-auto">
                  {review.events.map((event) => (
                    <li
                      key={event.id}
                      className="border-b border-pitch-indigo-soft/20 pb-3"
                    >
                      <div className="flex gap-2 items-center">
                        <button
                          onClick={() => seek(event.timestamp)}
                          className="text-pitch-green font-mono text-sm"
                        >
                          {time(event.timestamp)}
                        </button>
                        <span className="text-sm capitalize flex-1">
                          {event.type} ·{" "}
                          {event.team === "home"
                            ? match.homeTeamName
                            : match.awayTeamName}
                        </span>
                        <button
                          aria-label={`Edit ${event.type} at ${time(event.timestamp)}`}
                          onClick={() => setEditingId(event.id)}
                          className="text-pitch-muted hover:text-pitch-white"
                        >
                          <Pencil size={14} />
                        </button>
                        <button
                          aria-label={`Remove ${event.type} at ${time(event.timestamp)}`}
                          onClick={() => {
                            if (
                              save({
                                ...review,
                                events: review.events.filter(
                                  (e) => e.id !== event.id,
                                ),
                              })
                            ) {
                              setRemovedEvent(event);
                              setEditingId(null);
                            }
                          }}
                          className="text-pitch-muted hover:text-red-400"
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                      {editingId === event.id && (
                        <EditEvent
                          key={event.id}
                          event={event}
                          duration={review.duration}
                          names={{
                            home: match.homeTeamName,
                            away: match.awayTeamName,
                          }}
                          onCancel={() => setEditingId(null)}
                          onSave={(edited) =>
                            save({
                              ...review,
                              events: review.events
                                .map((e) => (e.id === edited.id ? edited : e))
                                .sort((a, b) => a.timestamp - b.timestamp),
                            })
                          }
                        />
                      )}
                      {event.note && (
                        <p className="text-xs text-pitch-muted mt-2 break-words">
                          {event.note}
                        </p>
                      )}
                    </li>
                  ))}
                </ol>
              </div>
            </aside>
          </div>
          {review.aiStatus !== "not-requested" && (
            <section className="glass-card p-6 space-y-4">
              <div>
                <h2 className="text-xl font-semibold">AI frame inspection</h2>
                <p className="text-pitch-muted text-sm mt-2">
                  {review.aiMessage}
                </p>
              </div>
              {frame && (
                <>
                  <div className="flex flex-wrap gap-2">
                    {review.frames.map((f, i) => (
                      <button
                        key={f.timestamp}
                        onClick={() => setFrameIndex(i)}
                        className={
                          i === frameIndex
                            ? "pitch-button-primary"
                            : "pitch-button-secondary"
                        }
                      >
                        {time(f.timestamp)}
                      </button>
                    ))}
                  </div>
                  <div className="max-w-3xl relative">
                    <img
                      src={frame.image}
                      alt={`Sample frame at ${time(frame.timestamp)}`}
                      className="w-full rounded-xl"
                    />
                    <svg
                      viewBox={`0 0 ${frame.width} ${frame.height}`}
                      className="absolute inset-0 w-full h-full"
                      aria-label="Detected object boxes"
                    >
                      {frame.predictions.map((p, i) => (
                        <g key={i}>
                          <rect
                            x={p.x - p.width / 2}
                            y={p.y - p.height / 2}
                            width={p.width}
                            height={p.height}
                            fill="none"
                            stroke="#4ade80"
                            strokeWidth={2}
                          />
                          <text
                            x={Math.max(0, p.x - p.width / 2)}
                            y={Math.max(12, p.y - p.height / 2 - 3)}
                            fill="white"
                            stroke="black"
                            strokeWidth={0.3}
                            fontSize={12}
                          >
                            {p.class} {Math.round(p.confidence * 100)}%
                          </text>
                        </g>
                      ))}
                    </svg>
                  </div>
                  <p className="text-sm text-pitch-muted">
                    {frame.predictions.length} detections in this frame.
                    Confidence is model confidence, not a guarantee of
                    correctness.
                  </p>
                </>
              )}
            </section>
          )}
        </div>
      </main>
    </>
  );
}
