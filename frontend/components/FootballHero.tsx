"use client";

/**
 * FootballHero — decorative football imagery for the landing page.
 * All SVG, no external assets — zero copyright concerns.
 */

import { motion } from 'framer-motion';

/** Top-down 5-a-side pitch — large watermark version */
export function PitchWatermark({ className = '' }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 900 560"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
    >
      {/* Pitch surface stripes */}
      {[0,1,2,3,4,5,6,7,8,9,10].map(i => (
        <rect key={i} x={i * 82} y="0" width="41" height="560"
          fill={i % 2 === 0 ? 'rgba(255,255,255,0.018)' : 'transparent'} />
      ))}

      {/* Outer boundary */}
      <rect x="20" y="20" width="860" height="520" rx="4"
        stroke="rgba(255,255,255,0.12)" strokeWidth="2" fill="none" />

      {/* Centre line */}
      <line x1="450" y1="20" x2="450" y2="540"
        stroke="rgba(255,255,255,0.12)" strokeWidth="2" />

      {/* Centre circle */}
      <circle cx="450" cy="280" r="80"
        stroke="rgba(255,255,255,0.12)" strokeWidth="2" fill="none" />
      <circle cx="450" cy="280" r="4"
        fill="rgba(255,255,255,0.15)" />

      {/* Left penalty area */}
      <rect x="20" y="140" width="160" height="280" rx="2"
        stroke="rgba(255,255,255,0.12)" strokeWidth="2" fill="none" />
      {/* Left goal area */}
      <rect x="20" y="195" width="70" height="170" rx="2"
        stroke="rgba(255,255,255,0.12)" strokeWidth="2" fill="none" />
      {/* Left goal */}
      <rect x="0" y="220" width="20" height="120" rx="2"
        stroke="rgba(255,255,255,0.18)" strokeWidth="2" fill="none" />

      {/* Right penalty area */}
      <rect x="720" y="140" width="160" height="280" rx="2"
        stroke="rgba(255,255,255,0.12)" strokeWidth="2" fill="none" />
      {/* Right goal area */}
      <rect x="810" y="195" width="70" height="170" rx="2"
        stroke="rgba(255,255,255,0.12)" strokeWidth="2" fill="none" />
      {/* Right goal */}
      <rect x="880" y="220" width="20" height="120" rx="2"
        stroke="rgba(255,255,255,0.18)" strokeWidth="2" fill="none" />

      {/* Penalty spots */}
      <circle cx="130" cy="280" r="5" fill="rgba(255,255,255,0.15)" />
      <circle cx="770" cy="280" r="5" fill="rgba(255,255,255,0.15)" />

      {/* Corner arcs */}
      <path d="M 20 20 Q 38 20 38 38" stroke="rgba(255,255,255,0.12)" strokeWidth="2" fill="none" />
      <path d="M 880 20 Q 862 20 862 38" stroke="rgba(255,255,255,0.12)" strokeWidth="2" fill="none" />
      <path d="M 20 540 Q 38 540 38 522" stroke="rgba(255,255,255,0.12)" strokeWidth="2" fill="none" />
      <path d="M 880 540 Q 862 540 862 522" stroke="rgba(255,255,255,0.12)" strokeWidth="2" fill="none" />
    </svg>
  );
}

/** Animated soccer ball — the hero icon */
export function SoccerBall({ className = '', size = 80 }: { className?: string; size?: number }) {
  const r = size / 2;
  return (
    <motion.svg
      width={size} height={size} viewBox={`0 0 ${size} ${size}`}
      className={className}
      animate={{ rotate: [0, 360] }}
      transition={{ duration: 12, repeat: Infinity, ease: 'linear' }}
    >
      <defs>
        <radialGradient id="ball-grad" cx="38%" cy="32%" r="60%">
          <stop offset="0%" stopColor="#ffffff" />
          <stop offset="40%" stopColor="#c8d8f0" />
          <stop offset="100%" stopColor="#8fb0d8" />
        </radialGradient>
        <filter id="ball-shadow">
          <feDropShadow dx="0" dy="4" stdDeviation="8" floodColor="#4F8CF6" floodOpacity="0.5" />
        </filter>
      </defs>
      {/* Ball sphere */}
      <circle cx={r} cy={r} r={r - 2} fill="url(#ball-grad)" filter="url(#ball-shadow)" />
      {/* Pentagon patches */}
      <polygon
        points={`${r},${r*0.22} ${r*1.24},${r*0.58} ${r*1.15},${r*1.00} ${r*0.85},${r*1.00} ${r*0.76},${r*0.58}`}
        fill="#1a2d4a" stroke="#2d4a6e" strokeWidth="1" opacity="0.85"
      />
      <polygon
        points={`${r},${r*1.78} ${r*1.24},${r*1.42} ${r*1.15},${r*1.00} ${r*0.85},${r*1.00} ${r*0.76},${r*1.42}`}
        fill="#1a2d4a" stroke="#2d4a6e" strokeWidth="1" opacity="0.85"
      />
      <polygon
        points={`${r*0.22},${r} ${r*0.56},${r*0.76} ${r*0.85},${r*1.00} ${r*0.85},${r*1.24} ${r*0.44},${r*1.24}`}
        fill="#1a2d4a" stroke="#2d4a6e" strokeWidth="1" opacity="0.85"
      />
      <polygon
        points={`${r*1.78},${r} ${r*1.44},${r*0.76} ${r*1.15},${r*1.00} ${r*1.15},${r*1.24} ${r*1.56},${r*1.24}`}
        fill="#1a2d4a" stroke="#2d4a6e" strokeWidth="1" opacity="0.85"
      />
      {/* Highlight */}
      <ellipse cx={r * 0.68} cy={r * 0.55} rx={r * 0.18} ry={r * 0.10}
        fill="white" opacity="0.35" transform={`rotate(-30 ${r*0.68} ${r*0.55})`} />
      {/* Outer ring */}
      <circle cx={r} cy={r} r={r - 2} fill="none" stroke="rgba(255,255,255,0.2)" strokeWidth="1" />
    </motion.svg>
  );
}

/** Stadium spotlight rays — 4 beams from corners */
export function StadiumLights({ className = '' }: { className?: string }) {
  return (
    <svg viewBox="0 0 800 500" fill="none" className={className} xmlns="http://www.w3.org/2000/svg">
      <defs>
        <radialGradient id="ray-tl" cx="0%" cy="0%" r="100%">
          <stop offset="0%" stopColor="#4F8CF6" stopOpacity="0.15" />
          <stop offset="100%" stopColor="#4F8CF6" stopOpacity="0" />
        </radialGradient>
        <radialGradient id="ray-tr" cx="100%" cy="0%" r="100%">
          <stop offset="0%" stopColor="#10B981" stopOpacity="0.12" />
          <stop offset="100%" stopColor="#10B981" stopOpacity="0" />
        </radialGradient>
        <radialGradient id="ray-bl" cx="0%" cy="100%" r="100%">
          <stop offset="0%" stopColor="#10B981" stopOpacity="0.10" />
          <stop offset="100%" stopColor="#10B981" stopOpacity="0" />
        </radialGradient>
        <radialGradient id="ray-br" cx="100%" cy="100%" r="100%">
          <stop offset="0%" stopColor="#4F8CF6" stopOpacity="0.12" />
          <stop offset="100%" stopColor="#4F8CF6" stopOpacity="0" />
        </radialGradient>
      </defs>
      {/* Cone rays from each corner */}
      <polygon points="0,0 300,250 0,200" fill="url(#ray-tl)" />
      <polygon points="800,0 500,250 800,200" fill="url(#ray-tr)" />
      <polygon points="0,500 300,250 0,300" fill="url(#ray-bl)" />
      <polygon points="800,500 500,250 800,300" fill="url(#ray-br)" />
      {/* Corner light sources */}
      {[[8,8],[792,8],[8,492],[792,492]].map(([x,y],i) => (
        <motion.circle key={i} cx={x} cy={y} r="8"
          fill={i % 2 === 0 ? '#4F8CF6' : '#10B981'}
          animate={{ opacity: [0.6,1,0.6], r: [7,9,7] }}
          transition={{ duration: 2, repeat: Infinity, delay: i * 0.4 }}
        />
      ))}
    </svg>
  );
}

/** Animated player silhouette running with ball */
export function PlayerSilhouette({ className = '' }: { className?: string }) {
  return (
    <motion.svg
      viewBox="0 0 120 180"
      fill="none"
      className={className}
      animate={{ x: [0, 4, 0, -4, 0] }}
      transition={{ duration: 1.8, repeat: Infinity, ease: 'easeInOut' }}
    >
      {/* Body */}
      <ellipse cx="60" cy="55" rx="16" ry="20" fill="rgba(79,140,246,0.25)" />
      {/* Head */}
      <circle cx="60" cy="28" r="14" fill="rgba(79,140,246,0.25)" />
      {/* Arms */}
      <path d="M46 60 Q32 72 36 84" stroke="rgba(79,140,246,0.3)" strokeWidth="7" strokeLinecap="round" fill="none" />
      <path d="M74 60 Q88 68 86 80" stroke="rgba(79,140,246,0.3)" strokeWidth="7" strokeLinecap="round" fill="none" />
      {/* Legs */}
      <path d="M52 74 Q44 100 40 120" stroke="rgba(79,140,246,0.3)" strokeWidth="8" strokeLinecap="round" fill="none" />
      <path d="M68 74 Q76 98 80 118" stroke="rgba(79,140,246,0.3)" strokeWidth="8" strokeLinecap="round" fill="none" />
      {/* Ball */}
      <motion.circle cx="88" cy="128" r="12"
        fill="rgba(16,185,129,0.3)" stroke="rgba(16,185,129,0.5)" strokeWidth="1.5"
        animate={{ cy: [128, 122, 128] }}
        transition={{ duration: 0.6, repeat: Infinity, ease: 'easeInOut' }}
      />
    </motion.svg>
  );
}
