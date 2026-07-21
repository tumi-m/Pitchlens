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
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(all));
  } catch {
    // Quota exceeded — evict the oldest half of other matches and retry once
    const others = Object.values(all)
      .filter((m) => m.id !== id)
      .sort((a: any, b: any) => (a.createdAt?.seconds ?? 0) - (b.createdAt?.seconds ?? 0));
    others.slice(0, Math.ceil(others.length / 2)).forEach((m) => delete all[m.id]);
    try { localStorage.setItem(LS_KEY, JSON.stringify(all)); } catch { /* give up silently */ }
  }
}

function getLocalMatch(id: string): Match | null {
  return getLocalMatches()[id] || null;
}

function getAllLocalMatches(userId?: string): Match[] {
  const all = Object.values(getLocalMatches())
    .sort((a: any, b: any) => (b.createdAt?.seconds ?? 0) - (a.createdAt?.seconds ?? 0));
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

export function subscribeToMatch(matchId: string, callback: (match: Match | null) => void) {
  // localStorage is the source of truth for client-side processed matches —
  // always poll it, even when Firebase is configured (processing happens in
  // the browser, so local data lands before/without Firestore).
  // Emits null when the match isn't found, so consumers can stop loading
  // (e.g. a shared link opened in a different browser). Only emits on change
  // to avoid re-rendering the dashboard at 2Hz.
  let lastJson: string | undefined;
  const emitLocal = () => {
    const m = getLocalMatch(matchId);
    const j = m ? JSON.stringify(m) : 'null';
    if (j !== lastJson) { lastJson = j; callback(m); }
  };
  emitLocal();
  const interval = setInterval(emitLocal, 500);

  if (!isFirebaseConfigured()) {
    return () => clearInterval(interval);
  }

  const unsub = onSnapshot(
    doc(db, 'matches', matchId),
    (snap) => {
      if (!snap.exists()) return;
      // Local data (fresher — written by the in-browser pipeline) wins on merge
      const local = getLocalMatch(matchId);
      const merged = { id: snap.id, ...snap.data(), ...(local ?? {}) } as Match;
      const j = JSON.stringify(merged);
      if (j !== lastJson) { lastJson = j; callback(merged); }
    },
    () => { /* permissions/offline — localStorage polling still covers us */ }
  );
  return () => { clearInterval(interval); unsub(); };
}

export function subscribeToUserMatches(userId: string, callback: (matches: Match[]) => void) {
  if (!isFirebaseConfigured()) {
    let lastJson: string | undefined;
    const check = () => {
      const list = getAllLocalMatches(userId);
      const j = JSON.stringify(list);
      if (j !== lastJson) { lastJson = j; callback(list); }
    };
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
  return onSnapshot(
    q,
    (snap) => {
      // Merge remote with locally-processed matches (dedupe by id, local wins)
      const merged = new Map<string, Match>();
      snap.docs.forEach((d) => merged.set(d.id, { id: d.id, ...d.data() } as Match));
      getAllLocalMatches(userId).forEach((m) => merged.set(m.id, m));
      callback(Array.from(merged.values()));
    },
    () => callback(getAllLocalMatches(userId))
  );
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
  // Then try Firebase in the background — ignore any error.
  // setDoc+merge (not updateDoc): the doc may not exist yet, since match
  // skeletons are created in localStorage only.
  if (isFirebaseConfigured()) {
    const local = getLocalMatch(matchId);
    setDoc(doc(db, 'matches', matchId), {
      ...(local ?? {}),
      status: 'completed',
      stats,
      processingProgress: 100,
      updatedAt: serverTimestamp(),
    }, { merge: true }).catch(() => { /* Firebase unavailable — localStorage already has the data */ });
  }
}

export { serverTimestamp, Timestamp };
