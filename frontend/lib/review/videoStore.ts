// Video blobs stay on this device. Transactions must commit before reporting success.
function openStore(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open("pitchlens-videos", 1);
    request.onupgradeneeded = () => request.result.createObjectStore("videos");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () =>
      reject(new Error("Browser video storage is unavailable."));
  });
}
export async function saveVideo(id: string, file: Blob): Promise<void> {
  const db = await openStore();
  try {
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction("videos", "readwrite");
      tx.objectStore("videos").put(file, id);
      tx.oncomplete = () => resolve();
      tx.onabort = tx.onerror = () =>
        reject(
          new Error(
            "Not enough browser storage to save this video. Free some space and try again.",
          ),
        );
    });
  } finally {
    db.close();
  }
}
export async function loadVideo(id: string): Promise<Blob | undefined> {
  const db = await openStore();
  try {
    return await new Promise((resolve, reject) => {
      const request = db.transaction("videos").objectStore("videos").get(id);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(new Error("Unable to read saved video."));
    });
  } finally {
    db.close();
  }
}
export async function deleteVideo(id: string): Promise<void> {
  const db = await openStore();
  try {
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction("videos", "readwrite");
      tx.objectStore("videos").delete(id);
      tx.oncomplete = () => resolve();
      tx.onabort = tx.onerror = () =>
        reject(new Error("Unable to delete video."));
    });
  } finally {
    db.close();
  }
}
