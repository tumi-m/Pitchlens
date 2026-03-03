import * as admin from 'firebase-admin';
import { onObjectFinalized } from 'firebase-functions/v2/storage';
import { onDocumentUpdated } from 'firebase-functions/v2/firestore';
import { onCall, HttpsError } from 'firebase-functions/v2/https';
import { logger } from 'firebase-functions/v2';
import axios from 'axios';
import { v4 as uuidv4 } from 'uuid';

admin.initializeApp();
const db = admin.firestore();
const storage = admin.storage();

// ── Constants ─────────────────────────────────────────────────────────────
const PYTHON_API_URL = process.env.PYTHON_API_URL ?? '';
const API_SECRET_KEY = process.env.API_SECRET_KEY ?? '';
const MAX_RETRIES = 3;

// ── Exponential backoff ───────────────────────────────────────────────────
async function withRetry<T>(fn: () => Promise<T>, retries = MAX_RETRIES, delayMs = 2000): Promise<T> {
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      return await fn();
    } catch (err) {
      if (attempt === retries) throw err;
      const wait = delayMs * Math.pow(2, attempt);
      logger.warn(`Retry ${attempt + 1}/${retries} after ${wait}ms`, err);
      await new Promise((r) => setTimeout(r, wait));
    }
  }
  throw new Error('Max retries exceeded');
}

// ── onVideoUpload: Storage trigger ────────────────────────────────────────
export const onVideoUpload = onObjectFinalized(
  { region: 'us-central1', timeoutSeconds: 120 },
  async (event) => {
    const { name: filePath, contentType, size } = event.data;

    // Only process MP4s in the videos/ bucket path
    if (!filePath?.startsWith('videos/') || contentType !== 'video/mp4') {
      logger.info('Skipping non-video upload', { filePath, contentType });
      return;
    }

    // Parse path: videos/{userId}/{matchId}/{filename}
    const parts = filePath.split('/');
    if (parts.length < 4) { logger.error('Unexpected path structure', { filePath }); return; }
    const [, userId, matchId] = parts;

    // Validate size (500MB)
    if (size && size > 500 * 1024 * 1024) {
      logger.error('Video exceeds size limit', { filePath, size });
      await db.doc(`matches/${matchId}`).update({
        status: 'error',
        errorMessage: 'Video file exceeds the 500MB limit.',
        updatedAt: admin.firestore.FieldValue.serverTimestamp(),
      });
      return;
    }

    // Idempotency: check if already queued
    const matchDoc = await db.doc(`matches/${matchId}`).get();
    if (!matchDoc.exists) {
      logger.error('Match document not found', { matchId });
      return;
    }
    const matchData = matchDoc.data()!;
    if (matchData.status !== 'uploading') {
      logger.info('Match already processing/processed, skipping', { matchId, status: matchData.status });
      return;
    }

    // Generate signed read URL (valid 1 hour)
    const file = storage.bucket().file(filePath);
    const [signedUrl] = await file.getSignedUrl({
      action: 'read',
      expires: Date.now() + 60 * 60 * 1000,
    });

    // Update Firestore to 'processing'
    await db.doc(`matches/${matchId}`).update({
      status: 'processing',
      videoUrls: admin.firestore.FieldValue.arrayUnion(signedUrl),
      updatedAt: admin.firestore.FieldValue.serverTimestamp(),
    });

    // Invoke Python AI engine
    await withRetry(async () => {
      const response = await axios.post(
        `${PYTHON_API_URL}/process-match`,
        {
          matchId,
          videoUrl: signedUrl,
          userId,
          teamColors: matchData.teamColors ?? null,
        },
        {
          headers: {
            Authorization: `Bearer ${API_SECRET_KEY}`,
            'Content-Type': 'application/json',
          },
          timeout: 30000,
        }
      );
      logger.info('Python API queued match', { matchId, status: response.data.status });
    });

    // Audit log
    await db.collection('audit').add({
      type: 'match_queued',
      matchId,
      userId,
      timestamp: admin.firestore.FieldValue.serverTimestamp(),
    });

    logger.info('Match processing initiated', { matchId, userId });
  }
);

// ── onMatchComplete: Firestore trigger ────────────────────────────────────
export const onMatchComplete = onDocumentUpdated(
  { document: 'matches/{matchId}', region: 'us-central1' },
  async (event) => {
    const before = event.data?.before.data();
    const after = event.data?.after.data();

    if (!before || !after) return;
    if (before.status === after.status || after.status !== 'completed') return;

    const matchId = event.params.matchId;
    const userId = after.userId;

    logger.info('Match completed, sending notification', { matchId, userId });

    // FCM push notification (if user has FCM token)
    const userDoc = await db.doc(`users/${userId}`).get();
    const userData = userDoc.data();
    const fcmToken = userData?.fcmToken;

    if (fcmToken) {
      try {
        await admin.messaging().send({
          token: fcmToken,
          notification: {
            title: '⚽ Match Analysis Ready!',
            body: `Your match "${after.title}" has been fully analysed. Tap to view.`,
          },
          data: {
            matchId,
            type: 'match_completed',
            click_action: `${process.env.APP_URL}/dashboard/${matchId}`,
          },
          webpush: {
            notification: {
              icon: '/icon-192.png',
              badge: '/badge.png',
            },
          },
        });
        logger.info('FCM notification sent', { userId, matchId });
      } catch (err) {
        logger.warn('FCM notification failed (non-fatal)', err);
      }
    }

    // Audit log
    await db.collection('audit').add({
      type: 'match_completed',
      matchId,
      userId,
      score: after.stats?.score,
      timestamp: admin.firestore.FieldValue.serverTimestamp(),
    });
  }
);

// ── deleteMatch: Callable function ───────────────────────────────────────
export const deleteMatch = onCall({ region: 'us-central1' }, async (request) => {
  if (!request.auth) throw new HttpsError('unauthenticated', 'Must be signed in.');

  const { matchId } = request.data;
  if (!matchId) throw new HttpsError('invalid-argument', 'matchId is required.');

  const matchDoc = await db.doc(`matches/${matchId}`).get();
  if (!matchDoc.exists) throw new HttpsError('not-found', 'Match not found.');
  if (matchDoc.data()!.userId !== request.auth.uid) {
    throw new HttpsError('permission-denied', 'You do not own this match.');
  }

  // Delete storage files
  const [files] = await storage.bucket().getFiles({
    prefix: `videos/${request.auth.uid}/${matchId}/`,
  });
  await Promise.all(files.map((f) => f.delete().catch(() => {})));

  // Delete Firestore doc
  await db.doc(`matches/${matchId}`).delete();

  return { success: true };
});
