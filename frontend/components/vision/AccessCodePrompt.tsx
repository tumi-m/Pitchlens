"use client";
import { useEffect, useState } from "react";
import { KeyRound } from "lucide-react";
import {
  ACCESS_CODE_CHARACTERS,
  ACCESS_EVENT,
  accessCodeSendable,
  cleanAccessCode,
  setVisionAccessCode,
} from "@/lib/review/vision";

/**
 * Asks for the site's access code wherever an action needs it (review, pitch
 * setup, labelling, re-runs), not only on the upload page. Matters when the
 * owner turns the code back on after an open test phase: earlier testers have
 * no code saved and would otherwise be locked out of their own matches.
 */
export function AccessCodePrompt() {
  const [open, setOpen] = useState(false);
  const [code, setCode] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    function onRequired() {
      // The upload page has its own code field.
      if (document.querySelector("[data-access-field]")) return;
      setSaved(false);
      setCode("");
      setOpen(true);
    }
    window.addEventListener(ACCESS_EVENT, onRequired);
    return () => window.removeEventListener(ACCESS_EVENT, onRequired);
  }, []);

  if (!open) return null;
  const clean = cleanAccessCode(code);
  // Request headers carry Latin-1 only: say so here instead of failing every request.
  const unsendable = !!clean && !accessCodeSendable(clean);
  return (
    <div className="fixed inset-x-0 bottom-0 z-50 p-4 pb-[calc(1rem+env(safe-area-inset-bottom))]" role="dialog" aria-label="Access code needed">
      <form
        className="mx-auto max-w-md glass-card p-4 space-y-3 border border-pitch-green/40"
        onSubmit={(e) => {
          e.preventDefault();
          if (!clean || !setVisionAccessCode(clean)) return;
          setSaved(true);
          setOpen(false);
        }}
      >
        <p className="font-semibold flex items-center gap-2">
          <KeyRound size={16} className="text-pitch-green" /> This site needs its access code
        </p>
        <p className="text-sm text-pitch-muted">Enter the code from the Pitchlens owner, then repeat what you were doing.</p>
        <input
          aria-label="Site access code"
          type="password"
          autoComplete="off"
          className="pitch-input w-full"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          autoFocus
        />
        {unsendable && (
          <p role="alert" className="text-xs text-red-300">
            {ACCESS_CODE_CHARACTERS}
          </p>
        )}
        <div className="flex gap-2 justify-end">
          <button type="button" className="pitch-button-secondary" onClick={() => setOpen(false)}>
            Not now
          </button>
          <button type="submit" className="pitch-button-primary" disabled={!clean || unsendable}>
            Save code
          </button>
        </div>
        {saved && <p className="text-xs text-pitch-muted">Saved.</p>}
      </form>
    </div>
  );
}
