import {
  ref,
  uploadBytesResumable,
  getDownloadURL,
  deleteObject,
  UploadTaskSnapshot,
} from 'firebase/storage';
import { storage } from './config';

export interface UploadProgress {
  bytesTransferred: number;
  totalBytes: number;
  percentage: number;
  state: string;
}

export function uploadVideo(
  userId: string,
  matchId: string,
  file: File,
  onProgress: (progress: UploadProgress) => void,
  onComplete: (downloadUrl: string) => void,
  onError: (error: Error) => void
) {
  const path = `videos/${userId}/${matchId}/${file.name}`;
  const storageRef = ref(storage, path);
  const uploadTask = uploadBytesResumable(storageRef, file, {
    contentType: 'video/mp4',
    customMetadata: { userId, matchId },
  });

  uploadTask.on(
    'state_changed',
    (snapshot: UploadTaskSnapshot) => {
      const percentage = Math.round(
        (snapshot.bytesTransferred / snapshot.totalBytes) * 100
      );
      onProgress({
        bytesTransferred: snapshot.bytesTransferred,
        totalBytes: snapshot.totalBytes,
        percentage,
        state: snapshot.state,
      });
    },
    onError,
    async () => {
      const url = await getDownloadURL(uploadTask.snapshot.ref);
      onComplete(url);
    }
  );

  return uploadTask;
}

export async function deleteVideo(userId: string, matchId: string, filename: string) {
  const path = `videos/${userId}/${matchId}/${filename}`;
  await deleteObject(ref(storage, path));
}
