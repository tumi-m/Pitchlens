const { onObjectFinalized } = require("firebase-functions/v2/storage");
const { getFirestore } = require("firebase-admin/firestore");
const { initializeApp } = require("firebase-admin/app");

initializeApp();
const db = getFirestore();

/**
 * Triggered when a new video is uploaded to Firebase Storage.
 * It writes a new document to Firestore 'matches' collection and 
 * would normally call the Python FastAPI engine to begin analysis.
 */
exports.onVideoUpload = onObjectFinalized(async (event) => {
  const fileBucket = event.data.bucket;
  const filePath = event.data.name;
  
  // Only process mp4
  if (!filePath.endsWith('.mp4')) return;

  // Extract match ID or user context from path
  const matchId = filePath.split('/').pop().replace('.mp4', '');

  console.log(`Processing uploaded video for matchId: ${matchId}`);

  // Create document marking it as processing
  await db.collection('matches').doc(matchId).set({
    status: 'uploading',
    videoUrl: `gs://${fileBucket}/${filePath}`,
    createdAt: new Date().toISOString()
  });

  // Example of calling Python Backend Endpoint via HTTP:
  /*
  const response = await fetch('https://pitchlens-engine-url.run.app/process-match', {
    method: 'POST',
    body: JSON.stringify({ matchId, videoUrl: `gs://${fileBucket}/${filePath}` }),
    headers: { 'Content-Type': 'application/json' }
  });
  */

  console.log('Successfully kicked off processing...');
});
