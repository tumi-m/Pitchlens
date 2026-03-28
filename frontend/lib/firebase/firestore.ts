import {
  collection,
  doc,
  setDoc,
  getDoc,
  getDocs,
  updateDoc,
  deleteDoc,
  onSnapshot,
  query,
  where,
  orderBy,
  limit,
  serverTimestamp,
  Timestamp,
} from 'firebase/firestore';
import { getFunctions, httpsCallable } from 'firebase/functions';
import { db } from './config';
import type { Match, Team, UserProfile } from '@/lib/types';

// ── LocalStorage fallback for when Firebase is unavailable ────────────────
const LS_KEY = 'pitchlens_matches';

function getLocalMatches(): Record<string, Match> {
  if (typeof window === 'undefined') return {};
  try {
    return JSON.parse(localStorage.getItem(LS_KEY) || '{}');
  } catch { return {}; }
}

function setLocalMatch(id: string, data: Partial<Match>) {
  if (typeof window === 'undefined') return;
  const all = getLocalMatches();
  all[id] = { ...(all[id] || {}), ...data, id } as Match;
  localStorage.setItem(LS_KEY, JSON.stringify(all));
}

function getLocalMatch(id: string): Match | null {
  return getLocalMatches()[id] || null;
}

function getAllLocalMatches(userId?: string): Match[] {
  const all = Object.values(getLocalMatches());
  if (userId) return all.filter((m) => m.userId === userId);
  return all;
}

/** Public: synchronously write match skeleton to localStorage (instant, never blocks) */
export function saveMatchLocally(id: string, data: Partial<Match>) {
  const now = { seconds: Math.floor(Date.now() / 1000), nanoseconds: 0 };
  setLocalMatch(id, { ...data, createdAt: now, updatedAt: now } as any);
}

/** Detect if Firebase is actually configured (not demo) */
function isFirebaseConfigured(): boolean {
  const key = process.env.NEXT_PUBLIC_FIREBASE_API_KEY;
  return !!key && key !== 'demo-api-key' && key.length > 10;
}

// ── Users ──────────────────────────────────────────────────────────────────
export async function getUserProfile(userId: string): Promise<UserProfile | null> {
  if (!isFirebaseConfigured()) return null;
  const snap = await getDoc(doc(db, 'users', userId));
  return snap.exists() ? ({ id: snap.id, ...snap.data() } as UserProfile) : null;
}

export async function updateUserPreferences(
  userId: string,
  preferences: Partial<UserProfile['preferences']>
) {
  if (!isFirebaseConfigured()) return;
  await updateDoc(doc(db, 'users', userId), { preferences });
}

// ── Teams ──────────────────────────────────────────────────────────────────
export async function createTeam(userId: string, team: Omit<Team, 'id' | 'userId' | 'createdAt'>) {
  if (!isFirebaseConfigured()) return 'local_team_' + Date.now();
  const ref = doc(collection(db, 'teams'));
  await setDoc(ref, { ...team, userId, createdAt: serverTimestamp() });
  const userRef = doc(db, 'users', userId);
  const userSnap = await getDoc(userRef);
  const existing = userSnap.data()?.teams ?? [];
  await updateDoc(userRef, { teams: [...existing, ref.id] });
  return ref.id;
}

export async function getTeams(userId: string): Promise<Team[]> {
  if (!isFirebaseConfigured()) return [];
  const q = query(collection(db, 'teams'), where('userId', '==', userId));
  const snap = await getDocs(q);
  return snap.docs.map((d) => ({ id: d.id, ...d.data() } as Team));
}

export async function updateTeam(teamId: string, data: Partial<Team>) {
  if (!isFirebaseConfigured()) return;
  await updateDoc(doc(db, 'teams', teamId), data);
}

export async function deleteTeam(userId: string, teamId: string) {
  if (!isFirebaseConfigured()) return;
  await deleteDoc(doc(db, 'teams', teamId));
  const userRef = doc(db, 'users', userId);
  const userSnap = await getDoc(userRef);
  const existing = userSnap.data()?.teams ?? [];
  await updateDoc(userRef, { teams: existing.filter((id: string) => id !== teamId) });
}

// ── Matches ────────────────────────────────────────────────────────────────
export async function createMatch(matchData: Partial<Match>): Promise<string> {
  if (!isFirebaseConfigured()) {
    const id = 'match_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
    const now = { seconds: Math.floor(Date.now() / 1000), nanoseconds: 0 };
    setLocalMatch(id, {
      ...matchData,
      status: 'processing',
      createdAt: now,
      updatedAt: now,
    } as any);
    return id;
  }
  const ref = doc(collection(db, 'matches'));
  await setDoc(ref, {
    ...matchData,
    status: 'processing',
    createdAt: serverTimestamp(),
    updatedAt: serverTimestamp(),
  });
  return ref.id;
}

export async function getMatch(matchId: string): Promise<Match | null> {
  if (!isFirebaseConfigured()) {
    return getLocalMatch(matchId);
  }
  const snap = await getDoc(doc(db, 'matches', matchId));
  return snap.exists() ? ({ id: snap.id, ...snap.data() } as Match) : null;
}

export async function getUserMatches(userId: string): Promise<Match[]> {
  if (!isFirebaseConfigured()) {
    return getAllLocalMatches(userId);
  }
  const q = query(
    collection(db, 'matches'),
    where('userId', '==', userId),
    orderBy('createdAt', 'desc'),
    limit(20)
  );
  const snap = await getDocs(q);
  return snap.docs.map((d) => ({ id: d.id, ...d.data() } as Match));
}

export function subscribeToMatch(matchId: string, callback: (match: Match) => void) {
  if (!isFirebaseConfigured()) {
    // Poll localStorage for updates
    const check = () => {
      const m = getLocalMatch(matchId);
      if (m) callback(m);
    };
    check();
    const interval = setInterval(check, 500);
    return () => clearInterval(interval);
  }
  return onSnapshot(doc(db, 'matches', matchId), (snap) => {
    if (snap.exists()) callback({ id: snap.id, ...snap.data() } as Match);
  });
}

export function subscribeToUserMatches(userId: string, callback: (matches: Match[]) => void) {
  if (!isFirebaseConfigured()) {
    const check = () => callback(getAllLocalMatches(userId));
    check();
    const interval = setInterval(check, 1000);
    return () => clearInterval(interval);
  }
  const q = query(
    collection(db, 'matches'),
    where('userId', '==', userId),
    orderBy('createdAt', 'desc'),
    limit(20)
  );
  return onSnapshot(q, (snap) => {
    callback(snap.docs.map((d) => ({ id: d.id, ...d.data() } as Match)));
  });
}

export async function reprocessMatch(matchId: string): Promise<void> {
  if (!isFirebaseConfigured()) return;
  const functions = getFunctions();
  const fn = httpsCallable(functions, 'reprocessMatch');
  await fn({ matchId });
}

export async function saveMatchStats(matchId: string, stats: any): Promise<void> {
  // Always save locally first — instant, never blocks the UI
  setLocalMatch(matchId, {
    status: 'completed',
    stats,
    processingProgress: 100,
    updatedAt: { seconds: Math.floor(Date.now() / 1000), nanoseconds: 0 },
  } as any);
  // Then try Firebase in the background — ignore any error
  if (isFirebaseConfigured()) {
    updateDoc(doc(db, 'matches', matchId), {
      status: 'completed',
      stats,
      processingProgress: 100,
      updatedAt: serverTimestamp(),
    }).catch(() => { /* Firebase unavailable — localStorage already has the data */ });
  }
}

export { serverTimestamp, Timestamp };
