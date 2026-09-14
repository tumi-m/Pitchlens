import { initializeApp } from 'firebase-admin/app';
import { getFirestore, FieldValue } from 'firebase-admin/firestore';
import { getStorage } from 'firebase-admin/storage';
import { onObjectFinalized } from 'firebase-functions/v2/storage';
import { onCall, HttpsError } from 'firebase-functions/v2/https';
import { logger } from 'firebase-functions/v2';
import { defineSecret } from 'firebase-functions/params';
import axios from 'axios';

initializeApp();
const db = getFirestore();
const storage = getStorage();
const apiSecret = defineSecret('API_SECRET_KEY');
const pythonUrl = defineSecret('PYTHON_API_URL');
const validId = (id: unknown): id is string => typeof id === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(id);

// Research path only: there is one engine, and failures remain failures.
export const onVideoUpload = onObjectFinalized(
  { region: 'us-central1', timeoutSeconds: 540, memory: '512MiB', secrets: [apiSecret, pythonUrl] },
  async event => {
    const { name, contentType, size, bucket } = event.data;
    const parts = name?.split('/');
    if (!parts || parts.length !== 4 || parts[0] !== 'videos' || contentType !== 'video/mp4') return;
    const [, userId, matchId] = parts;
    if (!validId(matchId)) return;
    const ref = db.doc(`matches/${matchId}`);
    // Ownership is checked before every mutation, including error updates.
    const claimed = await db.runTransaction(async tx => {
      const snap = await tx.get(ref);
      const data = snap.data();
      if (!data || data.userId !== userId || data.status !== 'uploading') return false;
      if (Number(size) > 500 * 1024 * 1024) {
        tx.update(ref, { status: 'error', errorMessage: 'Video exceeds 500 MB.' }); return false;
      }
      tx.update(ref, { status: 'processing', processingProgress: 0, storagePath: name, updatedAt: FieldValue.serverTimestamp() });
      return true;
    });
    if (!claimed) return;
    try {
      if (!pythonUrl.value() || !apiSecret.value()) throw new Error('Engine configuration is missing.');
      const [signedUrl] = await storage.bucket(bucket).file(name!).getSignedUrl({ action: 'read', expires: Date.now() + 60 * 60 * 1000 });
      const match = (await ref.get()).data()!;
      // Keep the request alive until work completes; no fire-and-forget Cloud Run jobs.
      await axios.post(`${pythonUrl.value().replace(/\/$/, '')}/api/v1/process-match`, {
        matchId, userId, videoUrl: signedUrl,
        teamColors: { home: match.homeTeamColor ?? '#ef4444', away: match.awayTeamColor ?? '#3b82f6' },
      }, { headers: { Authorization: `Bearer ${apiSecret.value()}` }, timeout: 480_000 });
    } catch {
      logger.error('Match engine failed', { matchId });
      await ref.update({ status: 'error', errorMessage: 'The experimental engine could not complete this video. Use the local review room or check engine configuration.', updatedAt: FieldValue.serverTimestamp() });
    }
  }
);

export const deleteMatch = onCall({ region: 'us-central1' }, async request => {
  if (!request.auth) throw new HttpsError('unauthenticated', 'Sign in first.');
  const matchId = request.data?.matchId;
  if (!validId(matchId)) throw new HttpsError('invalid-argument', 'Invalid match ID.');
  const ref = db.doc(`matches/${matchId}`);
  const snap = await ref.get();
  if (!snap.exists) throw new HttpsError('not-found', 'Match not found.');
  if (snap.data()!.userId !== request.auth.uid) throw new HttpsError('permission-denied', 'You do not own this match.');
  const [files] = await storage.bucket().getFiles({ prefix: `videos/${request.auth.uid}/${matchId}/` });
  await Promise.all(files.map(file => file.delete()));
  await ref.delete();
  return { success: true };
});

export const reprocessMatch = onCall({ region: 'us-central1' }, async () => {
  throw new HttpsError('failed-precondition', 'Upload the video again. Expired signed URLs cannot be used to retry.');
});
