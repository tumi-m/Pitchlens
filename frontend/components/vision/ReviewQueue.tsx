"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Check, X, ArrowLeftRight, ChevronLeft, ChevronRight, Plus } from "lucide-react";
import type { Analysis, AnalysisEvent, ReviewDecision } from "@/lib/review/analysis";
import { describeEvent, sendReview } from "@/lib/review/analysis";
import { clockTime } from "@/lib/review/vision";

const PRIORITY: Record<string, number> = { "goal-candidate": 0, shot: 1, interception: 2, tackle: 3, pass: 4, out: 5 };

export function ReviewQueue({
  jobId,
  analysis,
  names,
  colours,
  time,
  onWatch,
  onAnalysis,
}: {
  jobId: string;
  analysis: Analysis;
  names: string[];
  colours: [string, string];
  time: number;
  /** Seek to t and play a short clip around it. */
  onWatch: (t: number, until: number) => void;
  onAnalysis: (analysis: Analysis) => void;
}) {
  const [filter, setFilter] = useState<"key" | "all" | "pending">("key");
  // The selection follows the event, not a position: decisions change the list.
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [team, setTeam] = useState<0 | 1>(0);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const savingRef = useRef(false);
  const items = useMemo(() => {
    const list = analysis.events.filter((e) => {
      if (filter === "key") return e.type !== "pass" && e.type !== "out";
      if (filter === "pending") return e.status === "proposed";
      return true;
    });
    return list.sort((a, b) => (filter === "key" ? (PRIORITY[a.type] ?? 9) - (PRIORITY[b.type] ?? 9) || a.t - b.t : a.t - b.t));
  }, [analysis, filter]);
  const found = selectedId === null ? -1 : items.findIndex((e) => e.id === selectedId);
  const index = found >= 0 ? found : 0;
  const current: AnalysisEvent | undefined = items[index];
  const reviewed = analysis.events.filter((e) => e.status !== "proposed").length;
  const move = useCallback(
    (step: number) => {
      if (!items.length) return;
      const next = Math.min(items.length - 1, Math.max(0, index + step));
      setSelectedId(items[next].id);
    },
    [items, index],
  );

  const decide = useCallback(
    async (decisions: ReviewDecision[], advance = true) => {
      if (savingRef.current) return;
      savingRef.current = true;
      setSaving(true);
      setError("");
      // Where to go next, decided on the list the reviewer is looking at.
      const nextId = advance ? items[index + 1]?.id ?? null : current?.id ?? null;
      try {
        const out = await sendReview(jobId, decisions, analysis.review.decisions);
        onAnalysis(out.analysis);
        // Under "Not reviewed" the decided moment leaves the list: the next one
        // takes its place, so stay on the same position rather than skipping.
        if (advance) setSelectedId(filter === "pending" ? nextId : nextId ?? current?.id ?? null);
      } catch (e) {
        setError(e instanceof Error ? e.message : "The decision could not be saved");
      } finally {
        savingRef.current = false;
        setSaving(false);
      }
    },
    [jobId, onAnalysis, items, index, current, filter, analysis.review.decisions],
  );

  /** The moment as the reviewer sees it, so the decision survives re-analysis. */
  const seen = (e: AnalysisEvent) => ({ type: e.type, t: e.t, team: e.team, ...(e.outcome ? { outcome: e.outcome } : {}) });

  const watch = useCallback(
    (e: AnalysisEvent | undefined) => {
      if (e) onWatch(Math.max(0, e.t - 2.5), (e.tEnd ?? e.t) + 2.5);
    },
    [onWatch],
  );

  useEffect(() => {
    watch(current);
    // Only when the selected item changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current?.id]);

  const add = useCallback(
    (type: string, outcome?: string) => decide([{ action: "add", type, t: Math.round(time * 100) / 100, team, ...(outcome ? { outcome } : {}) }], false),
    [decide, time, team],
  );

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      // Never hijack browser shortcuts (Ctrl/Cmd+R reload, Cmd+G find, ...).
      if (e.ctrlKey || e.metaKey || e.altKey || e.repeat) return;
      const target = e.target as HTMLElement;
      if (target && (["INPUT", "SELECT", "TEXTAREA", "VIDEO"].includes(target.tagName) || target.isContentEditable)) return;
      if (target?.tagName === "BUTTON" && (e.key === " " || e.key === "Enter")) return;
      if (saving) return;
      const key = e.key.toLowerCase();
      if (key === "a" && current) decide([{ action: "accept", eventId: current.id, event: seen(current) }]);
      else if (key === "r" && current) decide([{ action: "reject", eventId: current.id, event: seen(current) }]);
      else if (key === "t" && current && current.team !== null)
        decide([{ action: "team", eventId: current.id, event: seen(current), value: current.team === 0 ? 1 : 0 }], false);
      else if (key === "j" || key === "arrowright") move(1);
      else if (key === "k" || key === "arrowleft") move(-1);
      else if (key === "1") setTeam(0);
      else if (key === "2") setTeam(1);
      else if (key === "g") add("goal");
      else if (key === "s") add("shot", "on-target");
      else if (key === " " && current) {
        e.preventDefault();
        watch(current);
      } else return;
      e.preventDefault();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [current, move, decide, add, watch, saving]);

  const direction = analysis.directions?.segments?.[0]?.team0Attacks;
  const [score, setScore] = useState<[string, string]>([
    analysis.enteredScore ? String(analysis.enteredScore[0]) : "",
    analysis.enteredScore ? String(analysis.enteredScore[1]) : "",
  ]);
  const scoreValid = score.every((v) => /^\d{1,2}$/.test(v));
  // The worker's count: it merges a confirmed shot-with-goal and a goal moment.
  const confirmedGoals = [0, 1].map((team) => analysis.stats.teams[team]?.goals?.value ?? 0);
  return (
    <section className="glass-card p-5 space-y-4" aria-label="Review moments">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-xl font-semibold">Review moments</h2>
        <span className="text-sm text-pitch-muted">
          {reviewed} of {analysis.events.length} reviewed
        </span>
      </div>
      <p className="text-sm text-pitch-muted">
        Confirm what the camera saw, reject mistakes, and add anything it missed. Confirmed goals set the score; every
        decision is kept, so you can undo it later.
      </p>
      <div className="rounded-xl border border-white/10 p-4 space-y-2">
        <p className="text-sm font-semibold">Final score</p>
        <p className="text-xs text-pitch-muted">
          Type the score you know. It sets the scoreline; possible goals are listed first so you can find each one.
        </p>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-2 text-sm">
            <span style={{ color: colours[0] }}>{names[0]}</span>
            <input aria-label={`Goals for ${names[0]}`} inputMode="numeric" className="pitch-input w-14 text-center" value={score[0]} onChange={(e) => setScore([e.target.value.trim(), score[1]])} />
          </label>
          <span className="text-pitch-muted">–</span>
          <label className="flex items-center gap-2 text-sm">
            <input aria-label={`Goals for ${names[1]}`} inputMode="numeric" className="pitch-input w-14 text-center" value={score[1]} onChange={(e) => setScore([score[0], e.target.value.trim()])} />
            <span style={{ color: colours[1] }}>{names[1]}</span>
          </label>
          <button
            className="pitch-button-secondary ml-auto"
            disabled={!scoreValid || saving}
            onClick={() => decide([{ action: "score", value: [Number(score[0]), Number(score[1])] }], false)}
          >
            Save score
          </button>
        </div>
        {analysis.enteredScore && (
          <p className="text-xs text-pitch-muted">
            Goals located in the video: {confirmedGoals[0]} of {analysis.enteredScore[0]} · {confirmedGoals[1]} of {analysis.enteredScore[1]}
          </p>
        )}
      </div>
      {analysis.calibrated && (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-pitch-muted">In the first half {names[0]} attacked</span>
          {(["left", "right"] as const).map((d) => (
            <button
              key={d}
              className={`px-3 py-1 rounded-full border ${direction === d ? "border-pitch-green text-pitch-green" : "border-white/15 text-pitch-muted"}`}
              onClick={() => decide([{ action: "direction", value: d }], false)}
              disabled={saving}
            >
              {d === "left" ? "← left" : "right →"}
            </button>
          ))}
          {analysis.directions && (
            <span className="text-xs text-pitch-muted">
              {analysis.directions.source === "reviewer"
                ? "set by you"
                : `detected from team positions (${Math.round(analysis.directions.confidence * 100)}% of minutes agree) · please check`}
            </span>
          )}
        </div>
      )}
      <div className="flex gap-1 p-1 rounded-lg bg-white/5 text-sm" role="tablist" aria-label="Which moments">
        {(
          [
            ["key", "Shots & key moments"],
            ["pending", "Not reviewed"],
            ["all", "Everything"],
          ] as const
        ).map(([key, label]) => (
          <button key={key} role="tab" aria-selected={filter === key} onClick={() => { setFilter(key); setSelectedId(null); }} className={`flex-1 py-1.5 rounded-md ${filter === key ? "bg-pitch-indigo-soft/50" : "text-pitch-muted"}`}>
            {label}
          </button>
        ))}
      </div>
      {current ? (
        <div className="rounded-xl border border-white/10 p-4 space-y-3">
          <div className="flex items-center justify-between gap-3">
            <button aria-label="Previous moment" className="p-2 rounded-lg hover:bg-white/10" onClick={() => move(-1)}>
              <ChevronLeft size={18} />
            </button>
            <button className="flex-1 text-left" onClick={() => watch(current)}>
              <span className="font-mono text-pitch-green mr-2">{clockTime(current.t)}</span>
              <span className="font-semibold">{describeEvent(current)}</span>
              {current.team !== null && (
                <span className="ml-2 text-sm" style={{ color: colours[current.team] }}>
                  {names[current.team]}
                </span>
              )}
              <span className="block text-xs text-pitch-muted">
                {current.status === "confirmed" ? "Confirmed" : current.status === "rejected" ? "Rejected" : `Automatic · confidence ${Math.round(current.confidence * 100)}%`}
                {current.ballSeen !== undefined ? ` · ball seen ${Math.round(current.ballSeen * 100)}% of the transfer` : ""}
                {current.source === "reviewer" ? " · added by you" : ""}
              </span>
            </button>
            <button aria-label="Next moment" className="p-2 rounded-lg hover:bg-white/10" onClick={() => move(1)}>
              <ChevronRight size={18} />
            </button>
          </div>
          <div className="grid grid-cols-3 gap-2">
            <button className="pitch-button-secondary" disabled={saving} onClick={() => decide([{ action: "accept", eventId: current.id, event: seen(current) }])}>
              <Check size={15} /> Confirm <kbd className="text-[10px] opacity-60">A</kbd>
            </button>
            <button className="pitch-button-secondary" disabled={saving} onClick={() => decide([{ action: "reject", eventId: current.id, event: seen(current) }])}>
              <X size={15} /> Reject <kbd className="text-[10px] opacity-60">R</kbd>
            </button>
            <button className="pitch-button-secondary" disabled={saving || current.team === null} onClick={() => current.team !== null && decide([{ action: "team", eventId: current.id, event: seen(current), value: current.team === 0 ? 1 : 0 }], false)}>
              <ArrowLeftRight size={15} /> Other team <kbd className="text-[10px] opacity-60">T</kbd>
            </button>
          </div>
          {current.type === "shot" && (
            <label className="block text-sm">
              Shot outcome
              <select
                aria-label="Shot outcome"
                className="pitch-input w-full mt-1"
                value={current.outcome || ""}
                onChange={(e) => decide([{ action: "outcome", eventId: current.id, event: seen(current), value: e.target.value }], false)}
              >
                <option value="unresolved" disabled>
                  Outcome unknown · choose one
                </option>
                <option value="on-target">On target</option>
                <option value="saved">Saved</option>
                <option value="blocked">Blocked</option>
                <option value="off-target">Off target</option>
                <option value="goal-candidate">Goal</option>
              </select>
            </label>
          )}
          {current.type === "goal-candidate" && (
            <p className="text-xs text-pitch-muted">Confirming a possible goal counts it in the score.</p>
          )}
          <p className="text-xs text-pitch-muted text-center">
            {index + 1} / {items.length} · J/K or ←/→ to move · space to replay
          </p>
        </div>
      ) : (
        <p className="text-sm text-pitch-muted py-4 text-center">Nothing in this list.</p>
      )}
      <div className="rounded-xl border border-dashed border-white/15 p-4 space-y-3">
        <p className="text-sm font-semibold flex items-center gap-2">
          <Plus size={15} /> Add a moment at {clockTime(time)}
        </p>
        <div className="flex gap-2 text-sm" role="radiogroup" aria-label="Team for added moment">
          {([0, 1] as const).map((t) => (
            <button key={t} role="radio" aria-checked={team === t} onClick={() => setTeam(t)} className={`flex-1 py-1.5 rounded-full border ${team === t ? "font-bold text-[#0b0f1a]" : "border-white/15 text-pitch-muted"}`} style={team === t ? { background: colours[t], borderColor: colours[t] } : undefined}>
              {names[t]} <kbd className="text-[10px] opacity-60">{t + 1}</kbd>
            </button>
          ))}
        </div>
        <div className="grid grid-cols-3 gap-2">
          <button className="pitch-button-secondary" disabled={saving} onClick={() => add("goal")}>
            Goal <kbd className="text-[10px] opacity-60">G</kbd>
          </button>
          <button className="pitch-button-secondary" disabled={saving} onClick={() => add("shot", "on-target")}>
            Shot <kbd className="text-[10px] opacity-60">S</kbd>
          </button>
          <button className="pitch-button-secondary" disabled={saving} onClick={() => add("pass", "complete")}>
            Pass
          </button>
        </div>
      </div>
      {error && (
        <p role="alert" className="text-sm text-red-300">
          {error}
        </p>
      )}
    </section>
  );
}
