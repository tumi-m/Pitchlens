"use client";

import { motion } from 'framer-motion';

interface StatBarProps {
  label: string;
  home: number | string;
  away: number | string;
  index?: number;
}

export default function StatBar({ label, home, away, index = 0 }: StatBarProps) {
  const pHome = typeof home === 'number' ? home : parseFloat(home as string);
  const pAway = typeof away === 'number' ? away : parseFloat(away as string);
  const total = pHome + pAway;
  const homePct = total === 0 ? 50 : (pHome / total) * 100;
  const awayPct = total === 0 ? 50 : (pAway / total) * 100;

  const displayHome = typeof home === 'string' && home.includes('%') ? home : home.toString();
  const displayAway = typeof away === 'string' && away.includes('%') ? away : away.toString();

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay: index * 0.06 }}
      className="mb-6"
    >
      <div className="flex justify-between items-center mb-2">
        <span className="text-xl font-black text-white w-12">{displayHome}</span>
        <span className="text-sm font-semibold text-secondary uppercase tracking-widest">{label}</span>
        <span className="text-xl font-black text-white w-12 text-right">{displayAway}</span>
      </div>
      <div className="flex w-full h-2 rounded overflow-hidden space-x-1 bg-surfaceHover">
        <motion.div
          className="bg-primary h-full rounded-r"
          initial={{ width: 0 }}
          animate={{ width: `${homePct}%` }}
          transition={{ duration: 0.8, delay: 0.2 + index * 0.06, ease: 'easeOut' }}
        />
        <motion.div
          className="bg-danger h-full rounded-l"
          initial={{ width: 0 }}
          animate={{ width: `${awayPct}%` }}
          transition={{ duration: 0.8, delay: 0.2 + index * 0.06, ease: 'easeOut' }}
        />
      </div>
    </motion.div>
  );
}
