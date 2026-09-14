import { fingerprintVideo } from "@/lib/review/portable";
import { auth } from "@/lib/firebase/config";
import type { VideoReview, Detection } from "@/lib/review/types";

export interface ProcessOptions {
  inference?: boolean;
  signal?: AbortSignal;
  onStage?: (label: string) => void;
  onProgress?: (pct: number) => void;
}
function mediaEvent(
  video: HTMLVideoElement,
  event: string,
  signal?: AbortSignal,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const cleanup = () => {
      clearTimeout(timer);
      video.removeEventListener(event, done);
      video.removeEventListener("error", failed);
      signal?.removeEventListener("abort", aborted);
    };
    const done = () => {
      cleanup();
      resolve();
    };
    const failed = () => {
      cleanup();
      reject(
        new Error(
          "This video cannot be decoded. Try an H.264 MP4 or WebM file.",
        ),
      );
    };
    const aborted = () => {
      cleanup();
      reject(new DOMException("Cancelled", "AbortError"));
    };
    const timer = setTimeout(failed, 15_000);
    video.addEventListener(event, done, { once: true });
    video.addEventListener("error", failed, { once: true });
    signal?.addEventListener("abort", aborted, { once: true });
    if (signal?.aborted) aborted();
  });
}
export async function processVideo(
  file: File,
  options: ProcessOptions = {},
): Promise<VideoReview> {
  if (!file.size || file.size > 500 * 1024 * 1024)
    throw new Error("Select a non-empty video up to 500 MB.");
  const { signal, onStage = () => {}, onProgress = () => {} } = options;
  const video = document.createElement("video");
  video.muted = true;
  video.preload = "auto";
  const url = URL.createObjectURL(file);
  try {
    onStage("Reading video metadata");
    onProgress(5);
    const ready = mediaEvent(video, "loadeddata", signal);
    video.src = url;
    await ready;
    if (
      !Number.isFinite(video.duration) ||
      video.duration <= 0 ||
      video.duration > 86400 ||
      video.videoWidth > 16384 ||
      video.videoHeight > 16384 ||
      !video.videoWidth
    )
      throw new Error("Video duration or dimensions could not be read.");
    const review: VideoReview = {
      schemaVersion: 1,
      source: "local-video",
      fileName: file.name,
      videoFingerprint: await fingerprintVideo(file),
      fileSize: file.size,
      duration: video.duration,
      width: video.videoWidth,
      height: video.videoHeight,
      aiStatus: "not-requested",
      frames: [],
      events: [],
      notes: "",
    };
    onProgress(20);
    if (!options.inference) return review;
    try {
      onStage("Checking player detection availability");
      const configRes = await fetch("/api/infer", { signal });
      if (!configRes.ok || !(await configRes.json()).configured) {
        review.aiStatus = "failed";
        review.aiMessage =
          "AI detection is unavailable. Video review and manual tagging are ready.";
        return review;
      }
      const canvas = document.createElement("canvas");
      canvas.width = Math.min(640, video.videoWidth);
      canvas.height = Math.round(
        (canvas.width * video.videoHeight) / video.videoWidth,
      );
      const ctx = canvas.getContext("2d");
      if (!ctx) throw new Error("This browser cannot extract video frames.");
      let failures = 0;
      for (let i = 0; i < 6; i++) {
        signal?.throwIfAborted();
        onStage(`Inspecting sample frame ${i + 1} of 6`);
        const timestamp = (video.duration * (i + 1)) / 7;
        const seek = mediaEvent(video, "seeked", signal);
        video.currentTime = timestamp;
        await seek;
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
        const image = canvas.toDataURL("image/jpeg", 0.7);
        try {
          const controller = new AbortController();
          const abort = () => controller.abort();
          signal?.addEventListener("abort", abort, { once: true });
          const timeout = setTimeout(abort, 20_000);
          try {
            const res = await fetch("/api/infer", {
              method: "POST",
              headers: {
                "Content-Type": "application/json",
                ...(auth.currentUser
                  ? {
                      Authorization: `Bearer ${await auth.currentUser.getIdToken()}`,
                    }
                  : {}),
              },
              body: JSON.stringify({ frame: image.split(",")[1] }),
              signal: controller.signal,
            });
            if (!res.ok) throw new Error("Frame detection failed");
            const data = await res.json();
            review.frames.push({
              timestamp,
              image,
              width: canvas.width,
              height: canvas.height,
              predictions: data.predictions as Detection[],
            });
          } finally {
            clearTimeout(timeout);
            signal?.removeEventListener("abort", abort);
          }
        } catch {
          signal?.throwIfAborted();
          failures++;
        }
        onProgress(20 + Math.round(((i + 1) / 6) * 70));
      }
      review.aiStatus =
        failures === 6 ? "failed" : failures ? "partial" : "completed";
      review.aiMessage = `${review.frames.length} of 6 sample frames inspected. Detections are image coordinates, not calibrated pitch positions. They do not establish team identity, possession, goals or xG.`;
    } catch {
      signal?.throwIfAborted();
      review.aiStatus = review.frames.length ? "partial" : "failed";
      review.aiMessage =
        "Frame inspection was interrupted. Video review and manual tagging are available.";
    }
    return review;
  } finally {
    video.pause();
    video.removeAttribute("src");
    video.load();
    URL.revokeObjectURL(url);
  }
}
