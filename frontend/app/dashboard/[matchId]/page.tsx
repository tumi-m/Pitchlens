'use client';
import { ReviewRoom } from '@/components/review/ReviewRoom';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { Loader2, AlertCircle, ChevronLeft } from 'lucide-react';
import { Navbar } from '@/components/ui/Navbar';
import { useMatch } from '@/lib/hooks/useMatch';

export default function DashboardPage() {
  const { matchId } = useParams<{ matchId: string }>();
  const { match, loading } = useMatch(matchId);

  if (loading) return <LoadingScreen />;
  if (!match) return <NotFound />;

  if (match.review) return <ReviewRoom key={match.id} match={match} />;

  return <LegacyMatchNotice title={match.title} />;
}

function LegacyMatchNotice({ title }: { title?: string }) {
  return (
    <>
      <Navbar />
      <main className="min-h-screen pt-20 pb-16 px-4">
        <div className="max-w-2xl mx-auto space-y-6">
          <Link
            href="/dashboard"
            className="flex items-center gap-1 text-pitch-muted hover:text-pitch-white text-sm transition-colors"
          >
            <ChevronLeft size={16} /> All Matches
          </Link>
          <div role="status" className="glass-card p-6 flex items-start gap-4">
            <AlertCircle className="text-amber-300 shrink-0 mt-0.5" size={20} />
            <div className="space-y-4">
              {title && <h1 className="text-xl font-bold text-pitch-white">{title}</h1>}
              <p className="text-pitch-muted text-sm">
                This match was created by an earlier Pitchlens version that generated simulated
                statistics. They are not shown.
              </p>
              <div className="flex flex-wrap gap-3">
                <Link href="/upload" className="pitch-button-primary text-sm">
                  Upload a match video
                </Link>
                <Link href="/dashboard" className="pitch-button-secondary text-sm">
                  Back to Dashboard
                </Link>
              </div>
            </div>
          </div>
        </div>
      </main>
    </>
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

function NotFound() {
  return (
    <>
      <Navbar />
      <main className="min-h-screen pt-20 flex flex-col items-center justify-center gap-4">
        <p className="text-pitch-white text-xl font-semibold">Match not found</p>
        <Link href="/dashboard" className="pitch-button-secondary">Back to Dashboard</Link>
      </main>
    </>
  );
}
