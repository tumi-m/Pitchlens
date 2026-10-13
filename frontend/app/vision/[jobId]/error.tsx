"use client";
import Link from "next/link";

/** A report that fails to render shows this instead of a blank page; the match
 *  itself is safe on the worker, so trying again or going back loses nothing. */
export default function ReportError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <main className="min-h-screen flex items-center justify-center px-4">
      <section role="alert" className="glass-card p-6 max-w-md space-y-3 text-center">
        <h1 className="text-xl font-semibold">This report could not be shown</h1>
        <p className="text-sm text-pitch-muted">
          Something in the page failed while drawing this match. Your analysis is still saved.
        </p>
        <div className="flex flex-wrap justify-center gap-2">
          <button className="pitch-button-primary" onClick={() => reset()}>
            Try again
          </button>
          <Link className="pitch-button-secondary" href="/dashboard">
            Back to your matches
          </Link>
        </div>
      </section>
    </main>
  );
}
