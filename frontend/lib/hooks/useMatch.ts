'use client';
import { useState, useEffect } from 'react';
import { subscribeToMatch, subscribeToUserMatches } from '@/lib/firebase/firestore';
import type { Match } from '@/lib/types';

export function useMatch(matchId: string) {
  const [match, setMatch] = useState<Match | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!matchId) return;
    const unsub = subscribeToMatch(matchId, (m) => {
      setMatch(m);
      setLoading(false);
    });
    return unsub;
  }, [matchId]);

  return { match, loading, error };
}

export function useUserMatches(userId: string | undefined) {
  const [matches, setMatches] = useState<Match[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!userId) { setLoading(false); return; }
    const unsub = subscribeToUserMatches(userId, (m) => {
      setMatches(m);
      setLoading(false);
    });
    return unsub;
  }, [userId]);

  return { matches, loading };
}
