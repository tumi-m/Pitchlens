'use client';
import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { motion } from 'framer-motion';
import { Plus, Clock, CheckCircle2, AlertCircle, Loader2, BarChart3 } from 'lucide-react';
import { Navbar } from '@/components/ui/Navbar';
import { useAuthContext } from '@/components/auth/AuthProvider';
import { useUserMatches } from '@/lib/hooks/useMatch';
import { cn } from '@/lib/utils/cn';
import { format } from 'date-fns';
import { AuthModal } from '@/components/auth/AuthModal';
import type { Match } from '@/lib/types';

export default function DashboardIndexPage() {
  const { user, loading: authLoading } = useAuthContext();
  const { matches, loading } = useUserMatches(user?.uid);
  const [authOpen, setAuthOpen] = useState(false);

  if (authLoading) return <LoadingScreen />;

  if (!user) {
    return (
      <>
        <Navbar />
        <main className="min-h-screen pt-24 flex flex-col items-center justify-center gap-6 px-4">
          <div className="text-center max-w-md">
            <div className="w-16 h-16 bg-pitch-indigo-soft/20 rounded-2xl flex items-center justify-center mx-auto mb-6">
              <BarChart3 size={32} className="text-pitch-indigo-glow" />
            </div>
            <h1 className="text-3xl font-bold text-pitch-white mb-3">Your Match Dashboard</h1>
            <p className="text-pitch-muted mb-6">Sign in to view your match analytics and upload new footage.</p>
            <button onClick={() => setAuthOpen(true)} className="pitch-button-primary">Sign In to Continue</button>
          </div>
          <AuthModal isOpen={authOpen} onClose={() => setAuthOpen(false)} />
        </main>
      </>
    );
  }

  return (
    <>
      <Navbar />
      <main className="min-h-screen pt-20 pb-16 px-4">
        <div className="max-w-6xl mx-auto space-y-8">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-3xl font-bold text-pitch-white">Your Matches</h1>
              <p className="text-pitch-muted mt-1">{matches.length} match{matches.length !== 1 ? 'es' : ''} analysed</p>
            </div>
            <Link href="/upload" className="pitch-button-primary gap-2">
              <Plus size={16} /> New Match
            </Link>
          </div>

          {loading ? (
            <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
              {Array.from({ length: 3 }).map((_, i) => (
                <div key={i} className="glass-card p-5 h-40 animate-pulse" />
              ))}
            </div>
          ) : matches.length === 0 ? (
            <EmptyState />
          ) : (
            <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
              {matches.map((match, i) => (
                <motion.div
                  key={match.id}
                  initial={{ opacity: 0, y: 16 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: i * 0.06 }}
                >
                  <MatchCard match={match} />
                </motion.div>
              ))}
            </div>
          )}
        </div>
      </main>
    </>
  );
}

function MatchCard({ match }: { match: Match }) {
  const statusConfig = {
    uploading: { icon: Clock, color: 'text-yellow-400', bg: 'bg-yellow-500/10', label: 'Uploading' },
    processing: { icon: Loader2, color: 'text-blue-400', bg: 'bg-blue-500/10', label: 'Processing' },
    completed: { icon: CheckCircle2, color: 'text-green-400', bg: 'bg-green-500/10', label: 'Completed' },
    error: { icon: AlertCircle, color: 'text-red-400', bg: 'bg-red-500/10', label: 'Error' },
  };
  const { icon: Icon, color, bg, label } = statusConfig[match.status];

  return (
    <Link href={`/dashboard/${match.id}`} className="block glass-card p-5 hover:border-pitch-indigo-glow/30 transition-all group">
      <div className="flex items-start justify-between mb-4">
        <div className={cn('w-8 h-8 rounded-lg flex items-center justify-center', bg)}>
          <Icon size={16} className={cn(color, match.status === 'processing' && 'animate-spin')} />
        </div>
        <span className={cn('text-xs font-medium', color)}>{label}</span>
      </div>

      <h3 className="font-semibold text-pitch-white group-hover:text-pitch-green transition-colors line-clamp-1">
        {match.title}
      </h3>

      {match.stats && (
        <div className="flex items-center gap-2 mt-2">
          <span className="text-2xl font-black text-pitch-white">{match.stats.score.home}</span>
          <span className="text-pitch-muted">–</span>
          <span className="text-2xl font-black text-pitch-white">{match.stats.score.away}</span>
          <div className="ml-auto flex gap-2 text-xs text-pitch-muted">
            <span>xG {match.stats.shots.home.xG.toFixed(1)}–{match.stats.shots.away.xG.toFixed(1)}</span>
          </div>
        </div>
      )}

      <div className="mt-3 flex items-center justify-between">
        <p className="text-pitch-muted text-xs">
          {match.createdAt?.toDate ? format(match.createdAt.toDate(), 'dd MMM yyyy') : match.createdAt?.seconds ? format(new Date(match.createdAt.seconds * 1000), 'dd MMM yyyy') : format(new Date(), 'dd MMM yyyy')}
        </p>
        {match.status === 'completed' && (
          <span className="text-pitch-green text-xs font-medium group-hover:underline">View Analysis →</span>
        )}
      </div>
    </Link>
  );
}

function EmptyState() {
  return (
    <div className="text-center py-20">
      <div className="w-20 h-20 bg-pitch-indigo-soft/10 rounded-2xl flex items-center justify-center mx-auto mb-6">
        <BarChart3 size={36} className="text-pitch-indigo-glow/40" />
      </div>
      <h2 className="text-pitch-white font-semibold text-xl mb-2">No matches yet</h2>
      <p className="text-pitch-muted mb-6">Upload your first match to start seeing the hidden geometry of your game.</p>
      <Link href="/upload" className="pitch-button-primary">Upload First Match</Link>
    </div>
  );
}

function LoadingScreen() {
  return (
    <>
      <Navbar />
      <main className="min-h-screen pt-20 flex items-center justify-center">
        <Loader2 className="animate-spin text-pitch-indigo-glow" size={40} />
      </main>
    </>
  );
}
