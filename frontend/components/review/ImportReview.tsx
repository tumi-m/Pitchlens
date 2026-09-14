"use client";
import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { MAX_IMPORT_BYTES, parseReviewExport } from "@/lib/review/portable";
import { saveMatchLocally } from "@/lib/firebase/firestore";

export function ImportReview({ userId }: { userId: string }) {
  const input = useRef<HTMLInputElement>(null);
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function importFile(file?: File) {
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      if (file.size > MAX_IMPORT_BYTES)
        throw new Error("Review files must be 3 MB or smaller.");
      const parsed = parseReviewExport(await file.text());
      const id = `local_${crypto.randomUUID()}`;
      // Never trust IDs, ownership, status, summary totals or remote URLs from an import.
      saveMatchLocally(id, {
        ...parsed,
        userId,
        status: "completed",
        duration: parsed.review.duration,
        processingProgress: 100,
        videoUrls: [],
      });
      router.push(`/dashboard/${id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Import failed.");
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }
  return (
    <div className="space-y-2">
      <input
        ref={input}
        type="file"
        accept=".json,application/json"
        aria-label="Import review file"
        className="sr-only"
        onChange={(e) => void importFile(e.target.files?.[0])}
        disabled={busy}
      />
      <button
        className="pitch-button-secondary"
        onClick={() => input.current?.click()}
        disabled={busy}
      >
        {busy ? "Importing…" : "Import review"}
      </button>
      {error && (
        <p role="alert" className="text-red-300 text-sm max-w-sm">
          {error}
        </p>
      )}
    </div>
  );
}
