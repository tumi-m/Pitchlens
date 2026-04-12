"use client";

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Activity, Upload, BarChart3 } from 'lucide-react';
import { motion } from 'framer-motion';

const navLinks = [
  { href: '/upload', label: 'Upload', icon: Upload },
  { href: '/dashboard', label: 'Dashboard', icon: BarChart3 },
];

export default function Navbar() {
  const pathname = usePathname();

  return (
    <motion.nav
      initial={{ y: -20, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      transition={{ duration: 0.5, ease: 'easeOut' }}
      className="fixed top-0 left-0 right-0 z-50 glass-nav"
    >
      <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
        {/* Logo */}
        <Link href="/" className="flex items-center space-x-2.5 group">
          <div className="relative">
            <Activity className="w-6 h-6 text-accent transition-transform group-hover:scale-110" />
            <div className="absolute inset-0 blur-md bg-accent/30 opacity-0 group-hover:opacity-100 transition-opacity" />
          </div>
          <span className="text-lg font-black tracking-tight">
            Pitch<span className="text-primary">lens</span>
          </span>
        </Link>

        {/* Navigation Links */}
        <div className="flex items-center space-x-1">
          {navLinks.map(({ href, label, icon: Icon }) => {
            const isActive = pathname?.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                className={`relative flex items-center space-x-2 px-4 py-2 rounded-lg text-sm font-medium transition-all duration-200 ${
                  isActive
                    ? 'text-white bg-white/[0.06]'
                    : 'text-secondary hover:text-white hover:bg-white/[0.04]'
                }`}
              >
                <Icon className="w-4 h-4" />
                <span>{label}</span>
                {isActive && (
                  <motion.div
                    layoutId="nav-indicator"
                    className="absolute bottom-0 left-2 right-2 h-[2px] bg-accent rounded-full"
                    transition={{ type: 'spring', stiffness: 350, damping: 30 }}
                  />
                )}
              </Link>
            );
          })}
        </div>
      </div>
    </motion.nav>
  );
}
