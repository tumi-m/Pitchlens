"use client";

import { frameAt, type TrackingPayload } from "@/lib/cv/types";

export function RadarPitch({ payload, time }: { payload: TrackingPayload; time: number }) {
  const frame = frameAt(payload, time);
  const L = payload.pitch.length || 42;
  const W = payload.pitch.width || 25;
  return (
    <div className="space-y-2">
      <div className="flex justify-between text-xs text-pitch-muted">
        <span>{payload.calibrated ? "Pitch metres" : "Image-space radar (uncalibrated)"}</span>
        <span>
          {payload.possession.home ?? "\u2014"}% / {payload.possession.away ?? "\u2014"}% possession of known time
        </span>
      </div>
      <svg viewBox={`0 0 ${L} ${W}`} className="w-full rounded-xl bg-emerald-900">
        <rect x="0" y="0" width={L} height={W} fill="#14532d" />
        <rect x="0.4" y="0.4" width={L - 0.8} height={W - 0.8} fill="none" stroke="white" strokeWidth="0.15" />
        <line x1={L / 2} y1="0.4" x2={L / 2} y2={W - 0.4} stroke="white" strokeWidth="0.12" />
        <circle cx={L / 2} cy={W / 2} r={Math.min(L, W) * 0.08} fill="none" stroke="white" strokeWidth="0.12" />
        {frame?.players.map((p) => (
          <circle key={p.id} cx={p.x} cy={p.y} r={L * 0.012} fill={p.team === "home" ? "#ef4444" : p.team === "away" ? "#60a5fa" : "#e5e7eb"} />
        ))}
        {frame?.ball && frame.ball.x != null && frame.ball.y != null && (
          <circle cx={frame.ball.x} cy={frame.ball.y} r={L * 0.008} fill="#fbbf24" />
        )}
      </svg>
      <p className="text-xs text-pitch-muted">{payload.possession.note}</p>
    </div>
  );
}
