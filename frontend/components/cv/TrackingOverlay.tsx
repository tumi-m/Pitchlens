"use client";

import { frameAt, type TrackingPayload } from "@/lib/cv/types";

export function TrackingOverlay({
  payload,
  time,
  width,
  height,
}: {
  payload: TrackingPayload;
  time: number;
  width: number;
  height: number;
}) {
  const frame = frameAt(payload, time);
  if (!frame) return null;
  const sx = width / Math.max(1, payload.video.width);
  const sy = height / Math.max(1, payload.video.height);
  return (
    <svg className="absolute inset-0 w-full h-full pointer-events-none" viewBox={`0 0 ${width} ${height}`} aria-hidden>
      {frame.players.map((p) => {
        const color = p.team === "home" ? "#ef4444" : p.team === "away" ? "#3b82f6" : "#d1d5db";
        return (
          <g key={p.id}>
            <rect x={p.bx * sx} y={p.by * sy} width={p.bw * sx} height={p.bh * sy} fill="none" stroke={color} strokeWidth={2} />
            <text x={p.bx * sx} y={p.by * sy - 4} fill={color} fontSize={12} fontFamily="sans-serif">
              {p.team[0].toUpperCase()}{p.id}
            </text>
          </g>
        );
      })}
      {frame.ball && (
        <circle
          cx={(frame.ball.bx + frame.ball.bw / 2) * sx}
          cy={(frame.ball.by + frame.ball.bh / 2) * sy}
          r={Math.max(4, (frame.ball.bw * sx) / 2)}
          fill="#f59e0b"
          stroke="#111"
          strokeWidth={1}
        />
      )}
    </svg>
  );
}
