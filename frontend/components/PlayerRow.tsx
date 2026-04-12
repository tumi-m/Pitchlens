"use client";

import { Users } from 'lucide-react';
import { motion } from 'framer-motion';

interface PlayerRowProps {
  num: number;
  name: string;
  rating: number;
  index?: number;
}

export default function PlayerRow({ num, name, rating, index = 0 }: PlayerRowProps) {
  const ratingColor =
    rating >= 8.0
      ? 'bg-accent/20 text-accent border-accent/20'
      : rating >= 7.0
      ? 'bg-primary/20 text-primary border-primary/20'
      : 'bg-surfaceHover text-secondary border-white/5';

  return (
    <motion.div
      initial={{ opacity: 0, x: -12 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.35, delay: index * 0.08 }}
      className="flex items-center justify-between p-3 border-b border-white/5 hover:bg-white/[0.03] transition-colors cursor-pointer group"
    >
      <div className="flex items-center space-x-3">
        <span className="text-secondary/50 font-mono text-xs w-4">{num}</span>
        <div className="w-8 h-8 rounded-full bg-gradient-to-br from-surfaceHover to-surface flex items-center justify-center text-secondary border border-white/10 group-hover:border-white/20 transition-all group-hover:shadow-glow-primary/20">
          <Users className="w-4 h-4" />
        </div>
        <span className="text-sm font-medium text-white/90">{name}</span>
      </div>
      <div className={`px-2.5 py-1 rounded-md text-xs font-bold border ${ratingColor}`}>
        {rating.toFixed(1)}
      </div>
    </motion.div>
  );
}
