import type { VideoReview } from '@/lib/review/types';
import { Timestamp } from 'firebase/firestore';

// ── Auth / User ────────────────────────────────────────────────────────────
export interface UserProfile {
  id: string;
  email: string;
  displayName: string;
  photoURL: string;
  teams: string[];
  preferences: { theme: 'dark' | 'light' };
  role: 'player' | 'coach' | 'club-owner';
  createdAt: Timestamp;
}

// ── Team ───────────────────────────────────────────────────────────────────
export interface Player {
  id: string;
  name: string;
  jerseyColor: string;
  jerseyNumber?: number;
  position?: string;
}

export interface Team {
  id: string;
  userId: string;
  name: string;
  players: Player[];
  createdAt: Timestamp;
}

// ── Match Status ───────────────────────────────────────────────────────────
export type MatchStatus = 'uploading' | 'processing' | 'completed' | 'error';

// ── Match Document ─────────────────────────────────────────────────────────
export interface Match {
  id: string;
  userId: string;
  title: string;
  homeTeamId?: string;
  awayTeamId?: string;
  homeTeamName: string;
  awayTeamName: string;
  homeTeamColor?: string;
  awayTeamColor?: string;
  videoUrls: string[];
  status: MatchStatus;
  errorMessage?: string;
  processingProgress?: number;
  review?: VideoReview;
  duration?: number; // seconds
  createdAt: Timestamp;
  updatedAt: Timestamp;
}
