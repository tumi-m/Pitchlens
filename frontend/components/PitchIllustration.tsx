"use client";

import { motion } from 'framer-motion';

interface PitchIllustrationProps {
  className?: string;
  animated?: boolean;
  /** Show moving ball dot */
  showBall?: boolean;
  /** Show player tracking dots */
  showPlayers?: boolean;
}

const PLAYER_DOTS = [
  { x: 120, y: 120, team: 'home', delay: 0 },
  { x: 180, y: 85, team: 'home', delay: 0.3 },
  { x: 190, y: 160, team: 'home', delay: 0.6 },
  { x: 260, y: 110, team: 'home', delay: 0.2 },
  { x: 270, y: 165, team: 'home', delay: 0.5 },
  { x: 380, y: 120, team: 'away', delay: 0.1 },
  { x: 330, y: 90, team: 'away', delay: 0.4 },
  { x: 340, y: 160, team: 'away', delay: 0.7 },
  { x: 420, y: 110, team: 'away', delay: 0.2 },
  { x: 430, y: 165, team: 'away', delay: 0.5 },
];

export default function PitchIllustration({
  className = '',
  animated = true,
  showBall = true,
  showPlayers = true,
}: PitchIllustrationProps) {
  return (
    <svg
      viewBox="0 0 560 250"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
    >
      <defs>
        {/* Grass gradient */}
        <linearGradient id="pitch-bg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#0A1628" />
          <stop offset="100%" stopColor="#061020" />
        </linearGradient>
        {/* Line glow */}
        <filter id="line-glow">
          <feGaussianBlur stdDeviation="1.5" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
        <filter id="dot-glow">
          <feGaussianBlur stdDeviation="2" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
        <filter id="ball-glow">
          <feGaussianBlur stdDeviation="3" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
        <clipPath id="pitch-clip">
          <rect x="18" y="18" width="524" height="214" rx="4" />
        </clipPath>
      </defs>

      {/* Background */}
      <rect x="18" y="18" width="524" height="214" rx="4" fill="url(#pitch-bg)" />

      {/* Subtle stripe pattern */}
      {[0,1,2,3,4,5,6].map(i => (
        <rect
          key={i}
          x={18 + i * 75}
          y="18"
          width="37"
          height="214"
          fill={i % 2 === 0 ? "#ffffff08" : "transparent"}
          clipPath="url(#pitch-clip)"
        />
      ))}

      {/* Pitch outline */}
      <rect
        x="24" y="24" width="512" height="202" rx="2"
        stroke="#1E3A5F" strokeWidth="1.5" fill="none"
        filter="url(#line-glow)"
      />

      {/* Centre line */}
      <line x1="280" y1="24" x2="280" y2="226"
        stroke="#1E3A5F" strokeWidth="1.5" filter="url(#line-glow)" />

      {/* Centre circle */}
      <circle cx="280" cy="125" r="42"
        stroke="#1E3A5F" strokeWidth="1.5" fill="none"
        filter="url(#line-glow)" />
      <circle cx="280" cy="125" r="3"
        fill="#1E3A5F" />

      {/* Left penalty area */}
      <rect x="24" y="72" width="88" height="106" rx="1"
        stroke="#1E3A5F" strokeWidth="1.5" fill="none"
        filter="url(#line-glow)" />
      {/* Left goal area */}
      <rect x="24" y="95" width="44" height="60" rx="1"
        stroke="#1E3A5F" strokeWidth="1.5" fill="none"
        filter="url(#line-glow)" />
      {/* Left goal */}
      <rect x="14" y="105" width="10" height="40" rx="1"
        stroke="#2D5A8E" strokeWidth="1.5" fill="#0A1628"
        filter="url(#line-glow)" />

      {/* Right penalty area */}
      <rect x="448" y="72" width="88" height="106" rx="1"
        stroke="#1E3A5F" strokeWidth="1.5" fill="none"
        filter="url(#line-glow)" />
      {/* Right goal area */}
      <rect x="492" y="95" width="44" height="60" rx="1"
        stroke="#1E3A5F" strokeWidth="1.5" fill="none"
        filter="url(#line-glow)" />
      {/* Right goal */}
      <rect x="536" y="105" width="10" height="40" rx="1"
        stroke="#2D5A8E" strokeWidth="1.5" fill="#0A1628"
        filter="url(#line-glow)" />

      {/* Left penalty spot */}
      <circle cx="88" cy="125" r="2" fill="#1E3A5F" />
      {/* Right penalty spot */}
      <circle cx="472" cy="125" r="2" fill="#1E3A5F" />

      {/* Corner arcs */}
      <path d="M 24 24 Q 32 24 32 32" stroke="#1E3A5F" strokeWidth="1.5" fill="none" />
      <path d="M 536 24 Q 528 24 528 32" stroke="#1E3A5F" strokeWidth="1.5" fill="none" />
      <path d="M 24 226 Q 32 226 32 218" stroke="#1E3A5F" strokeWidth="1.5" fill="none" />
      <path d="M 536 226 Q 528 226 528 218" stroke="#1E3A5F" strokeWidth="1.5" fill="none" />

      {/* Player tracking dots */}
      {showPlayers && PLAYER_DOTS.map((dot, i) => (
        <motion.g key={i}
          initial={animated ? { opacity: 0, scale: 0 } : { opacity: 1, scale: 1 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ delay: dot.delay + 0.4, duration: 0.4, type: 'spring' }}
        >
          {/* Glow ring */}
          <circle
            cx={dot.x} cy={dot.y} r="7"
            fill={dot.team === 'home' ? '#4F8CF620' : '#EF444420'}
            filter="url(#dot-glow)"
          />
          {/* Player dot */}
          <circle
            cx={dot.x} cy={dot.y} r="5"
            fill={dot.team === 'home' ? '#4F8CF6' : '#EF4444'}
          />
          {/* Direction indicator (small triangle) */}
          <circle
            cx={dot.x} cy={dot.y} r="2"
            fill="white" opacity="0.6"
          />
        </motion.g>
      ))}

      {/* Ball */}
      {showBall && (
        <motion.g
          initial={animated ? { opacity: 0 } : { opacity: 1 }}
          animate={animated ? {
            opacity: 1,
            cx: [280, 310, 295, 320, 300],
            cy: [125, 115, 135, 120, 130],
          } : { opacity: 1 }}
          transition={{ duration: 4, repeat: Infinity, ease: 'easeInOut', delay: 0.8 }}
        >
          <motion.circle
            cx={280} cy={125} r="6"
            fill="white"
            filter="url(#ball-glow)"
            animate={animated ? {
              cx: [280, 320, 290, 330, 280],
              cy: [125, 110, 140, 115, 125],
            } : {}}
            transition={{ duration: 5, repeat: Infinity, ease: 'easeInOut' }}
          />
          {/* Ball pentagon pattern */}
          <motion.circle
            cx={280} cy={125} r="6"
            stroke="#00000030" strokeWidth="1" fill="none"
            animate={animated ? {
              cx: [280, 320, 290, 330, 280],
              cy: [125, 110, 140, 115, 125],
            } : {}}
            transition={{ duration: 5, repeat: Infinity, ease: 'easeInOut' }}
          />
        </motion.g>
      )}

      {/* Outer border with glow */}
      <rect
        x="18" y="18" width="524" height="214" rx="4"
        stroke="#1E3A5F" strokeWidth="1.5" fill="none"
      />
    </svg>
  );
}
