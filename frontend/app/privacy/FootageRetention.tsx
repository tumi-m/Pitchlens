"use client";
import { useEffect, useState } from "react";
import { retentionLabel } from "@/lib/review/vision";

/** How long this site's analysis server keeps uploaded footage, as its /health says
 *  (the proxy serves it without an owner key). Unreachable: no number is claimed. */
export function FootageRetention() {
  // undefined: not known (loading, or the server did not answer).
  const [hours, setHours] = useState<number | null | undefined>(undefined);
  useEffect(() => {
    let stop = false;
    fetch("/api/vision/health", { cache: "no-store" })
      .then((r) => r.json())
      .then((health: { retentionHours?: number | null }) => {
        const value = health?.retentionHours;
        if (!stop && (value === null || (typeof value === "number" && value > 0))) setHours(value);
      })
      .catch(() => {});
    return () => {
      stop = true;
    };
  }, []);
  const label = retentionLabel(hours);
  return (
    <span data-testid="privacy-retention">
      {label
        ? `Uploaded footage is deleted after ${label}.`
        : hours === null
          ? "Uploaded footage is kept until you delete it."
          : "How long this server keeps uploaded footage could not be checked just now; you can delete it yourself at any time (below)."}
    </span>
  );
}
