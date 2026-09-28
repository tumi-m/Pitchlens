"use client";
import { motion } from "framer-motion";
import type { Analysis, PitchTemplate } from "@/lib/review/analysis";
import { pitchLines } from "@/lib/review/analysis";

const PAD = 1.5; // metres of margin around the pitch drawing

/** A pitch drawn to scale in metres (x right along the length, y down across the width). */
export function PitchSvg({
  template,
  children,
  className = "w-full h-auto",
  label,
}: {
  template: PitchTemplate;
  children?: React.ReactNode;
  className?: string;
  label?: string;
}) {
  const { length: L, width: W } = template;
  const lines = pitchLines(template, 0.5);
  return (
    <svg
      viewBox={`${-PAD} ${-PAD} ${L + 2 * PAD} ${W + 2 * PAD}`}
      className={className}
      role="img"
      aria-label={label}
    >
      <rect x={-PAD} y={-PAD} width={L + 2 * PAD} height={W + 2 * PAD} fill="#0f2a1c" rx={1} />
      <rect x={0} y={0} width={L} height={W} fill="#14532d" opacity={0.55} />
      {Array.from({ length: 8 }, (_, i) => (
        <rect key={i} x={(L / 8) * i} y={0} width={L / 16} height={W} fill="#ffffff" opacity={0.025} />
      ))}
      {lines.map((line, i) => (
        <polyline
          key={i}
          points={line.map(([x, y]) => `${x},${y}`).join(" ")}
          fill="none"
          stroke="rgba(255,255,255,0.55)"
          strokeWidth={Math.max(0.12, L / 400)}
        />
      ))}
      {/* goals */}
      {[0, L].map((x) => (
        <rect
          key={x}
          x={x === 0 ? -0.8 : L}
          y={W / 2 - template.goalWidth / 2}
          width={0.8}
          height={template.goalWidth}
          fill="none"
          stroke="rgba(255,255,255,0.8)"
          strokeWidth={Math.max(0.12, L / 400)}
        />
      ))}
      {children}
    </svg>
  );
}

/** Where a team spent the match, normalised so the team attacks to the right. */
export function Heatmap({
  template,
  grid,
  colour,
  mirror = false,
  label,
}: {
  template: PitchTemplate;
  grid: number[][] | null | undefined;
  colour: string;
  mirror?: boolean;
  label: string;
}) {
  if (!grid) return null;
  const ny = grid.length;
  const nx = grid[0]?.length || 1;
  const max = Math.max(1e-9, ...grid.flat());
  const cw = template.length / nx;
  const ch = template.width / ny;
  return (
    <PitchSvg template={template} label={label}>
      <defs>
        <filter id={`blur-${label.replace(/\W/g, "")}`}>
          <feGaussianBlur stdDeviation={Math.min(cw, ch) * 0.45} />
        </filter>
      </defs>
      <g filter={`url(#blur-${label.replace(/\W/g, "")})`}>
        {grid.flatMap((row, j) =>
          row.map((v, i) => {
            if (!v) return null;
            const x = mirror ? template.length - (i + 1) * cw : i * cw;
            const y = mirror ? template.width - (j + 1) * ch : j * ch;
            return <rect key={`${i}-${j}`} x={x} y={y} width={cw} height={ch} fill={colour} opacity={Math.min(0.9, 0.12 + (v / max) * 0.8)} />;
          }),
        )}
      </g>
    </PitchSvg>
  );
}

const OUTCOME_STYLE: Record<string, { fill: string; label: string }> = {
  "goal-candidate": { fill: "#facc15", label: "Possible goal" },
  goal: { fill: "#facc15", label: "Goal" },
  "on-target": { fill: "#22c55e", label: "On target" },
  saved: { fill: "#38bdf8", label: "Saved" },
  blocked: { fill: "#a78bfa", label: "Blocked" },
  "off-target": { fill: "#ef4444", label: "Off target" },
};

/** Shots for both teams: the first team shoots right, the second left (as on Sofascore). */
export function ShotMap({
  analysis,
  colours,
  names,
  onSeek,
}: {
  analysis: Analysis;
  colours: [string, string];
  names: string[];
  onSeek: (t: number) => void;
}) {
  const template = analysis.template;
  const shots = analysis.stats.shotMap;
  if (!template || !shots) return null;
  const r = Math.max(0.45, template.length / 70);
  return (
    <div>
      <PitchSvg template={template} label="Shot map">
        {shots.map((s, i) => {
          const x = s.team === 0 ? s.x : template.length - s.x;
          const y = s.team === 0 ? s.y : template.width - s.y;
          const style = OUTCOME_STYLE[s.outcome || ""] || OUTCOME_STYLE["off-target"];
          return (
            <motion.g
              key={s.id}
              initial={{ scale: 0, opacity: 0 }}
              whileInView={{ scale: 1, opacity: 1 }}
              viewport={{ once: true }}
              transition={{ delay: 0.1 + i * 0.03 }}
              style={{ cursor: "pointer" }}
              onClick={() => onSeek(Math.max(0, s.t - 2))}
            >
              <title>{`${names[s.team]} · ${style.label}${s.status === "confirmed" ? " · confirmed" : s.status === "rejected" ? " · rejected" : " · to review"}`}</title>
              <circle cx={x} cy={y} r={r} fill={style.fill} stroke={colours[s.team]} strokeWidth={r * 0.45} opacity={s.status === "confirmed" ? 1 : 0.6} strokeDasharray={s.status === "confirmed" ? undefined : `${r * 0.5} ${r * 0.35}`} />
            </motion.g>
          );
        })}
      </PitchSvg>
      <div className="flex flex-wrap gap-3 justify-center text-[11px] text-pitch-muted mt-2">
        {["on-target", "saved", "blocked", "off-target", "goal-candidate"].map((k) => (
          <span key={k} className="flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 rounded-full" style={{ background: OUTCOME_STYLE[k].fill }} />
            {OUTCOME_STYLE[k].label}
          </span>
        ))}
        <span>Dashed = not yet reviewed</span>
      </div>
    </div>
  );
}

/** Average position of each tracked player, both teams attacking right in the normalised view. */
export function AveragePositions({
  analysis,
  colours,
}: {
  analysis: Analysis;
  colours: [string, string];
}) {
  const template = analysis.template;
  const players = analysis.stats.averagePositions;
  if (!template || !players?.length) return null;
  const r = Math.max(0.5, template.length / 60);
  return (
    <PitchSvg template={template} label="Average positions">
      {players.slice(0, 24).map((p) => {
        // Team B drawn attacking left so both shapes face each other.
        const x = p.team === 0 ? p.x : template.length - p.x;
        const y = p.team === 0 ? p.y : template.width - p.y;
        return (
          <g key={`${p.team}-${p.player}`}>
            <title>{`Track ${p.player} · ${Math.round(p.seconds)} s observed`}</title>
            <circle cx={x} cy={y} r={r} fill={colours[p.team]} stroke="#0b0f1a" strokeWidth={r * 0.2} />
            <text x={x} y={y + r * 0.35} textAnchor="middle" fontSize={r * 0.95} fill="#0b0f1a" fontWeight={700}>
              {p.player}
            </text>
          </g>
        );
      })}
    </PitchSvg>
  );
}

/** Top-down replay of the current frame (players and ball on the pitch). */
export function MiniPitch({
  analysis,
  time,
  colours,
}: {
  analysis: Analysis;
  time: number;
  colours: [string, string];
}) {
  const template = analysis.template;
  const positions = analysis.positions;
  if (!template || !positions?.length) return null;
  let l = 0;
  let h = positions.length - 1;
  while (l < h) {
    const m = Math.ceil((l + h) / 2);
    if (positions[m][0] <= time) l = m;
    else h = m - 1;
  }
  const [t, players, ball] = positions[l];
  const close = Math.abs(t - time) < 1;
  const r = Math.max(0.45, template.length / 75);
  return (
    <PitchSvg template={template} label="Live pitch view">
      {close && players ? (
        <>
          {players.map(([id, team, x, y]) => (
            <circle key={id} cx={x} cy={y} r={r} fill={team === 0 || team === 1 ? colours[team] : "#94a3b8"} stroke="#0b0f1a" strokeWidth={r * 0.2} />
          ))}
          {ball && <circle cx={ball[0]} cy={ball[1]} r={r * 0.6} fill="#f8ef3d" stroke="#111" strokeWidth={r * 0.15} strokeDasharray={ball[2] ? `${r * 0.3} ${r * 0.2}` : undefined} />}
        </>
      ) : (
        <text x={template.length / 2} y={template.width / 2} textAnchor="middle" fontSize={template.length / 30} fill="rgba(255,255,255,0.6)">
          No pitch position for this moment
        </text>
      )}
    </PitchSvg>
  );
}
