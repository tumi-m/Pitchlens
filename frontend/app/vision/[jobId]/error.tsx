"use client";
import Link from "next/link";
import { Navbar } from "@/components/ui/Navbar";

/** One unexpected field in a report must not blank the whole page. */
export default function ReportError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <>
      <Navbar />
      <main className="pt-24 pb-16 px-4">
        <div role="alert" className="max-w-xl mx-auto glass-card p-6 space-y-4">
          <h1 className="text-xl font-semibold">This report could not be displayed</h1>
          <p className="text-sm text-pitch-muted">
            Part of this analysis could not be shown. Try again, or go back to your matches.
          </p>
          <div className="flex flex-wrap gap-3">
            <button type="button" className="pitch-button-primary" onClick={reset}>
              Try again
            </button>
            <Link href="/dashboard" className="pitch-button-secondary">
              Your matches
            </Link>
          </div>
        </div>
      </main>
    </>
  );
}
