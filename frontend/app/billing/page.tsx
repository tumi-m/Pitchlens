"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Navbar } from "@/components/ui/Navbar";
import { setVisionAccessCode, visionAccessCode, visionJson } from "@/lib/review/vision";

type Summary = {
  mode: "test";
  configured: boolean;
  currency: "ZAR";
  priceCents: number | null;
  balance: number;
  orders: { reference: string; amount: number; state: string; created: number }[];
};
const money = (cents: number) => new Intl.NumberFormat("en-ZA", { style: "currency", currency: "ZAR" }).format(cents / 100);
const message = (error: unknown) => error instanceof Error ? error.message : "The payment service could not be reached.";

export default function BillingPage() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [email, setEmail] = useState("");
  const [access, setAccess] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [returnedReference, setReturnedReference] = useState("");
  const running = useRef(false);
  const initialized = useRef(false);
  // Retain the same request ID on network errors: retrying cannot create two orders.
  const attempt = useRef<{ email: string; id: string } | null>(null);

  const reload = useCallback(async () => {
    const data = await visionJson<Summary>("billing/summary");
    setSummary(data);
  }, []);

  const verify = useCallback(async (reference: string) => {
    if (running.current) return;
    running.current = true;
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await visionJson<{ state: string }>("billing/verify", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reference }),
      });
      setNotice(result.state === "paid"
        ? "Test payment verified. Your sandbox credit is recorded."
        : "Payment is not confirmed yet. Check again shortly; no credit has been added by this check.");
      if (result.state === "paid") {
        attempt.current = null;
        setReturnedReference("");
        window.history.replaceState(null, "", "/billing");
      }
      await reload();
    } catch (cause) { setError(message(cause)); }
    finally { running.current = false; setBusy(false); }
  }, [reload]);

  useEffect(() => {
    if (initialized.current) return;
    initialized.current = true;
    setAccess(visionAccessCode());
    const reference = new URLSearchParams(window.location.search).get("reference");
    if (reference && /^pltest-[a-f0-9]{32}$/.test(reference)) {
      setReturnedReference(reference);
    }
    // Load before verification so a slower initial response cannot overwrite a
    // newly credited balance. A callback alone is never proof of payment.
    void reload().then(() => {
      if (reference && /^pltest-[a-f0-9]{32}$/.test(reference)) return verify(reference);
    }).catch((cause) => setError(message(cause)));
  }, [reload, verify]);

  async function checkout(event: React.FormEvent) {
    event.preventDefault();
    if (running.current || !summary?.configured) return;
    running.current = true;
    setBusy(true); setError(""); setNotice("");
    const normalized = email.trim().toLowerCase();
    if (attempt.current?.email !== normalized) attempt.current = { email: normalized, id: crypto.randomUUID() };
    try {
      const result = await visionJson<{ url: string }>("billing/checkout", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: normalized, requestId: attempt.current.id }),
      });
      const destination = new URL(result.url);
      if (destination.protocol !== "https:" || destination.host !== "checkout.paystack.com" || destination.username || destination.password)
        throw new Error("The checkout destination could not be verified.");
      window.location.assign(destination.toString());
    } catch (cause) {
      setError(message(cause));
      await reload().catch(() => {});
      running.current = false; setBusy(false);
    }
  }

  return (
    <><Navbar /><main className="max-w-3xl mx-auto px-4 pt-28 pb-16 space-y-6">
      <Link href="/dashboard" className="text-sm text-pitch-muted hover:text-pitch-white">← Your matches</Link>
      <header className="space-y-3">
        <p className="text-pitch-green text-xs uppercase tracking-widest">South Africa · ZAR · Test mode</p>
        <h1 className="text-3xl font-bold text-pitch-white">Payment sandbox</h1>
        <p className="text-pitch-muted">Test Paystack checkout and payment confirmation. Live payments are disabled.
          Sandbox credits have no monetary value and do not purchase or unlock match analysis.</p>
      </header>
      {error && <div role="alert" className="rounded-xl border border-red-400/30 bg-red-400/10 p-4 text-red-200">{error}</div>}
      {notice && <p role="status" className="rounded-xl border border-pitch-green/30 p-4 text-pitch-white">{notice}</p>}
      {!summary && !error && <p role="status" className="text-pitch-muted">Loading payment setup…</p>}
      {!summary && error && <button onClick={() => { setError(""); void reload().catch((cause) => setError(message(cause))); }} className="pitch-button-primary">Retry payment setup</button>}
      {summary && <>
        <section className="glass-card p-6 space-y-4">
          <div className="flex justify-between items-center gap-4">
            <h2 className="text-lg font-semibold">Sandbox balance</h2>
            <p className="text-2xl font-bold" data-testid="sandbox-balance">{summary.balance} test {summary.balance === 1 ? "credit" : "credits"}</p>
          </div>
          <p className="text-sm text-pitch-muted">This test history belongs to this browser. Keep the same browser storage when returning from checkout.</p>
          {!summary.configured ? <p role="status" className="rounded-lg bg-pitch-indigo-soft/20 p-4 text-pitch-muted">
            Awaiting merchant setup. The operator must configure a Paystack test key, callback URL and test amount before checkout is available.
          </p> : <form onSubmit={checkout} className="space-y-4">
            <p className="font-medium">One sandbox credit · {money(summary.priceCents!)} test amount</p>
            <label className="block text-sm">Test email
              <input type="email" autoComplete="email" required maxLength={254} value={email} onChange={(e) => setEmail(e.target.value)}
                className="pitch-input w-full mt-2" placeholder="you@example.com" disabled={busy} />
            </label>
            <button className="pitch-button-primary" type="submit" disabled={busy}>{busy ? "Checking payment…" : "Open Paystack test checkout"}</button>
            <p className="text-xs text-pitch-muted">Use Paystack’s test payment details on its hosted page. No card details are entered or stored in Pitchlens.</p>
          </form>}
        </section>
        <details className="glass-card p-5" open={!!error}>
          <summary className="cursor-pointer text-sm font-medium">Pilot access code</summary>
          <label className="block text-sm text-pitch-muted mt-3">Required for payment tests on the hosted pilot
            <input type="password" autoComplete="off" value={access} onChange={(e) => { setAccess(e.target.value); setVisionAccessCode(e.target.value); }}
              className="pitch-input w-full mt-2" aria-label="Pilot access code" />
          </label>
        </details>
        {returnedReference && <button disabled={busy || !summary.configured} onClick={() => void verify(returnedReference)} className="pitch-button-primary">Check returned payment</button>}
        <section className="glass-card p-6 space-y-4">
          <h2 className="text-lg font-semibold">Test payment history</h2>
          {summary.orders.length === 0 ? <p className="text-sm text-pitch-muted">No test payments in this browser yet.</p> : <ul className="divide-y divide-white/10">
            {summary.orders.map((order) => <li key={order.reference} className="py-4 space-y-2">
              <div className="flex flex-wrap justify-between gap-3">
                <p>{money(order.amount)} · {order.state === "paid" ? "Verified test payment" : "Not confirmed"}</p>
                {order.state !== "paid" && <button disabled={busy || !summary.configured} onClick={() => void verify(order.reference)} className="text-sm text-pitch-green disabled:opacity-50">Check payment</button>}
              </div>
              <p className="text-xs text-pitch-muted break-all">{order.reference}</p>
              <p className="text-xs text-pitch-muted">{new Date(order.created * 1000).toLocaleString("en-ZA", { timeZone: "Africa/Johannesburg" })} SAST</p>
            </li>)}
          </ul>}
        </section>
      </>}
    </main></>
  );
}
