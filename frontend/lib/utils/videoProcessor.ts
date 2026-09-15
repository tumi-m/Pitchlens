import { fingerprintVideo } from "@/lib/review/portable";
import { auth } from "@/lib/firebase/config";
import type { VideoReview, Detection } from "@/lib/review/types";
import { buildHostedTracking, sampleTimes } from "@/lib/cv/hostedTrack";
import { candidatesToEvents } from "@/lib/cv/importAnalysis";

export interface ProcessOptions {
  inference?: boolean;
  track?: boolean;
  signal?: AbortSignal;
  onStage?: (label: string) => void;
  onProgress?: (pct: number) => void;
}

function mediaEvent(video: HTMLVideoElement, event: string, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const cleanup = () => {
      clearTimeout(timer);
      video.removeEventListener(event, done);
      video.removeEventListener("error", failed);
      signal?.removeEventListener("abort", aborted);
    };
    const done = () => { cleanup(); resolve(); };
    const failed = () => {
      cleanup();
      reject(new Error("This video cannot be decoded. Try an H.264 MP4 or WebM file."));
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

async function detectFrame(jpegB64: string, signal?: AbortSignal): Promise<Detection[]> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  const timeout = setTimeout(abort, 20_000);
  try {
    const res = await fetch("/api/infer", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(auth.currentUser ? { Authorization: `Bearer ${await auth.currentUser.getIdToken()}` } : {}),
      },
      body: JSON.stringify({ frame: jpegB64 }),
      signal: controller.signal,
    });
    if (!res.ok) throw new Error("Frame detection failed");
    return (await res.json()).predictions as Detection[];
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener("abort", abort);
  }
}

export async function processVideo(file: File, options: ProcessOptions = {}): Promise<VideoReview> {
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
    if (!Number.isFinite(video.duration) || video.duration <= 0 || video.duration > 86400 || video.videoWidth > 16384 || video.videoHeight > 16384 || !video.videoWidth)
      throw new Error("Video duration or dimensions could not be read.");
    const review: VideoReview = {
      schemaVersion: 1, source: "local-video", fileName: file.name,
      videoFingerprint: await fingerprintVideo(file), fileSize: file.size,
      duration: video.duration, width: video.videoWidth, height: video.videoHeight,
      aiStatus: "not-requested", frames: [], events: [], notes: "",
    };
    onProgress(20);
    if (!options.inference && !options.track) return review;
    onStage("Checking hosted detection");
    const configRes = await fetch("/api/infer", { signal });
    if (!configRes.ok || !(await configRes.json()).configured) {
      review.aiStatus = "failed";
      review.aiMessage = "Hosted detection is unavailable (missing ROBOFLOW_API_KEY). Video review and manual tagging are ready.";
      return review;
    }
    const canvas = document.createElement("canvas");
    canvas.width = Math.min(640, video.videoWidth);
    canvas.height = Math.round((canvas.width * video.videoHeight) / video.videoWidth);
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("This browser cannot extract video frames.");
    const times = options.track ? sampleTimes(video.duration, 40) : Array.from({ length: 6 }, (_, i) => (video.duration * (i + 1)) / 7);
    let failures = 0;
    for (let i = 0; i < times.length; i++) {
      signal?.throwIfAborted();
      onStage(options.track ? `Hosted detection ${i + 1} of ${times.length}` : `Inspecting sample frame ${i + 1} of ${times.length}`);
      const timestamp = times[i];
      const seek = mediaEvent(video, "seeked", signal);
      video.currentTime = timestamp;
      await seek;
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      const image = canvas.toDataURL("image/jpeg", 0.7);
      try {
        const predictions = await detectFrame(image.split(",")[1], signal);
        review.frames.push({ timestamp, image, width: canvas.width, height: canvas.height, predictions });
      } catch {
        signal?.throwIfAborted();
        failures++;
      }
      onProgress(20 + Math.round(((i + 1) / times.length) * 70));
    }
    if (options.track && review.frames.length >= 3) {
      const tracking = buildHostedTracking(review.frames, { duration: review.duration, width: review.width, height: review.height });
      review.tracking = tracking;
      review.events = candidatesToEvents(tracking);
      review.aiStatus = failures ? "partial" : "completed";
      review.aiMessage = `Hosted Roboflow tracked ${review.frames.length} frames. ${review.events.length} event candidates need review. Teams are a left/right split, not kit colours.`;
    } else {
      review.aiStatus = failures === times.length ? "failed" : failures ? "partial" : "completed";
      review.aiMessage = `${review.frames.length} of ${times.length} sample frames inspected. Detections are image coordinates, not match statistics.`;
    }
    return review;
  } finally {
    video.pause();
    video.removeAttribute("src");
    video.load();
    URL.revokeObjectURL(url);
  }
}
