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

// ── Users ──────────────────────────────────────────────────────────────────
export async function getUserProfile(userId: string): Promise<UserProfile | null> {
  const snap = await getDoc(doc(db, 'users', userId));
  return snap.exists() ? ({ id: snap.id, ...snap.data() } as UserProfile) : null;
}

export async function updateUserPreferences(
  userId: string,
  preferences: Partial<UserProfile['preferences']>
) {
  await updateDoc(doc(db, 'users', userId), { preferences });
}

// ── Teams ──────────────────────────────────────────────────────────────────
export async function createTeam(userId: string, team: Omit<Team, 'id' | 'userId' | 'createdAt'>) {
  const ref = doc(collection(db, 'teams'));
  await setDoc(ref, { ...team, userId, createdAt: serverTimestamp() });
  // add teamId to user doc
  const userRef = doc(db, 'users', userId);
  const userSnap = await getDoc(userRef);
  const existing = userSnap.data()?.teams ?? [];
  await updateDoc(userRef, { teams: [...existing, ref.id] });
  return ref.id;
}

export async function getTeams(userId: string): Promise<Team[]> {
  const q = query(collection(db, 'teams'), where('userId', '==', userId));
  const snap = await getDocs(q);
  return snap.docs.map((d) => ({ id: d.id, ...d.data() } as Team));
}

export async function updateTeam(teamId: string, data: Partial<Team>) {
  await updateDoc(doc(db, 'teams', teamId), data);
}

export async function deleteTeam(userId: string, teamId: string) {
  await deleteDoc(doc(db, 'teams', teamId));
  const userRef = doc(db, 'users', userId);
  const userSnap = await getDoc(userRef);
  const existing = userSnap.data()?.teams ?? [];
  await updateDoc(userRef, { teams: existing.filter((id: string) => id !== teamId) });
}

// ── Matches ────────────────────────────────────────────────────────────────
export async function createMatch(matchData: Partial<Match>): Promise<string> {
  const ref = doc(collection(db, 'matches'));
  await setDoc(ref, {
    ...matchData,
    status: 'uploading',
    createdAt: serverTimestamp(),
    updatedAt: serverTimestamp(),
  });
  return ref.id;
}

export async function getMatch(matchId: string): Promise<Match | null> {
  const snap = await getDoc(doc(db, 'matches', matchId));
  return snap.exists() ? ({ id: snap.id, ...snap.data() } as Match) : null;
}

export async function getUserMatches(userId: string): Promise<Match[]> {
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
  return onSnapshot(doc(db, 'matches', matchId), (snap) => {
    if (snap.exists()) callback({ id: snap.id, ...snap.data() } as Match);
  });
}

export function subscribeToUserMatches(userId: string, callback: (matches: Match[]) => void) {
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
  const functions = getFunctions();
  const fn = httpsCallable(functions, 'reprocessMatch');
  await fn({ matchId });
}

export async function saveMatchStats(matchId: string, stats: any): Promise<void> {
  await updateDoc(doc(db, 'matches', matchId), {
    status: 'completed',
    stats,
    processingProgress: 100,
    updatedAt: serverTimestamp(),
  });
}

export { serverTimestamp, Timestamp };
