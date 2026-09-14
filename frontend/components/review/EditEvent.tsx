"use client";
import { useState } from "react";
import { EVENT_TYPES, validateEvent } from "@/lib/review/portable";
import type { ReviewEvent } from "@/lib/review/types";

export function EditEvent({
  event,
  duration,
  names,
  onSave,
  onCancel,
}: {
  event: ReviewEvent;
  duration: number;
  names: { home: string; away: string };
  onSave: (event: ReviewEvent) => boolean;
  onCancel: () => void;
}) {
  const [timestamp, setTimestamp] = useState(String(event.timestamp));
  const [team, setTeam] = useState(event.team);
  const [type, setType] = useState(event.type);
  const [note, setNote] = useState(event.note);
  const [error, setError] = useState("");
  return (
    <form
      aria-label="Edit event"
      className="space-y-3 rounded-xl border border-pitch-indigo-soft/40 p-3 mt-3"
      onSubmit={(e) => {
        e.preventDefault();
        setError("");
        try {
          if (!timestamp.trim()) throw new Error("Enter a timestamp.");
          const next = validateEvent(
            { ...event, timestamp: Number(timestamp), team, type, note },
            duration,
          );
          if (onSave(next)) onCancel();
        } catch (err) {
          setError(err instanceof Error ? err.message : "Invalid event.");
        }
      }}
    >
      <label className="block text-sm">
        Time (seconds)
        <input
          aria-label="Event timestamp in seconds"
          type="number"
          min={0}
          max={duration}
          step="any"
          value={timestamp}
          onChange={(e) => setTimestamp(e.target.value)}
          className="pitch-input w-full mt-1"
          required
        />
      </label>
      <label className="block text-sm">
        Team
        <select
          aria-label="Event team"
          value={team}
          onChange={(e) => setTeam(e.target.value as ReviewEvent["team"])}
          className="pitch-input w-full mt-1"
        >
          <option value="home">{names.home}</option>
          <option value="away">{names.away}</option>
        </select>
      </label>
      <label className="block text-sm">
        Event
        <select
          aria-label="Event type"
          value={type}
          onChange={(e) => setType(e.target.value as ReviewEvent["type"])}
          className="pitch-input w-full mt-1"
        >
          {EVENT_TYPES.map((type) => (
            <option key={type} value={type}>
              {type}
            </option>
          ))}
        </select>
      </label>
      <label className="block text-sm">
        Note
        <textarea
          aria-label="Edit event note"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          maxLength={300}
          className="pitch-input w-full mt-1"
        />
      </label>
      {error && (
        <p role="alert" className="text-red-300 text-sm">
          {error}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <button type="submit" className="pitch-button-primary">
          Save event
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="pitch-button-secondary"
        >
          Cancel edit
        </button>
      </div>
    </form>
  );
}
