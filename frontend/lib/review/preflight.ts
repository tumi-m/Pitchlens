/** Browser-only inspection: the file is not uploaded or sent to an inference API. */
export type VideoPreflight = {
  width: number;
  height: number;
  duration: number;
  samples: { t: number; image: string }[];
};

export async function inspectVideo(file: File, signal: AbortSignal): Promise<VideoPreflight> {
  const url = URL.createObjectURL(file);
  const video = document.createElement("video");
  video.muted = true;
  video.preload = "auto";
  function waitFor(event: string, action: () => void): Promise<void> {
    return new Promise((resolve, reject) => {
      const cleanup = () => {
        clearTimeout(timer);
        video.removeEventListener(event, done);
        video.removeEventListener("error", failed);
        signal.removeEventListener("abort", aborted);
      };
      const done = () => { cleanup(); resolve(); };
      const failed = () => { cleanup(); reject(new Error("This browser could not decode the video. Export it as H.264 MP4 and try again.")); };
      const aborted = () => { cleanup(); reject(new DOMException("Cancelled", "AbortError")); };
      const timer = setTimeout(failed, 15000);
      video.addEventListener(event, done, { once: true });
      video.addEventListener("error", failed, { once: true });
      signal.addEventListener("abort", aborted, { once: true });
      if (signal.aborted) aborted();
      else action();
    });
  }
  try {
    await waitFor("loadeddata", () => { video.src = url; });
    const { videoWidth: width, videoHeight: height, duration } = video;
    if (!Number.isFinite(duration) || duration <= 0 || !width || !height)
      throw new Error("The video has invalid dimensions or duration.");
    if (duration > 4 * 3600 || width > 4096 || height > 4096)
      throw new Error("Choose a video under four hours and 4096 pixels per side.");
    const canvas = document.createElement("canvas");
    canvas.width = Math.min(480, width);
    canvas.height = Math.max(1, Math.round(height * canvas.width / width));
    const context = canvas.getContext("2d");
    if (!context) throw new Error("Video preview is unavailable in this browser.");
    const samples = [];
    for (const fraction of [.1, .5, .9]) {
      const t = duration * fraction;
      await waitFor("seeked", () => { video.currentTime = t; });
      context.drawImage(video, 0, 0, canvas.width, canvas.height);
      samples.push({ t, image: canvas.toDataURL("image/jpeg", .8) });
    }
    return { width, height, duration, samples };
  } finally {
    video.removeAttribute("src");
    video.load();
    URL.revokeObjectURL(url);
  }
}
