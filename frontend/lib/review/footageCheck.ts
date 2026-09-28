/**
 * Pre-upload footage check: what the analysis can expect from this file,
 * measured in the browser before a single byte is uploaded.
 */
export type FootageCheck = {
  width: number;
  height: number;
  duration: number;
  /** Expected ball diameter in pixels for a full-pitch view (~1/70 of frame height). */
  ballPixels: number;
  /** 0–100 */
  score: number;
  grade: "great" | "good" | "limited" | "poor";
  notes: string[];
  /** Crude motion estimate from sampled frames: 0 static … 1 constant cutting. */
  motion?: number;
};

export function gradeFootage(width: number, height: number, duration: number, motion?: number): FootageCheck {
  const notes: string[] = [];
  let score = 100;
  const ballPixels = Math.round((height / 70) * 10) / 10;
  if (height >= 1080) notes.push("1080p or better: the ball is large enough to track well.");
  else if (height >= 720) {
    score -= 15;
    notes.push("720p: workable. 1080p would make the ball twice as easy to follow.");
  } else if (height >= 480) {
    score -= 35;
    notes.push("Below 720p the ball is only a few pixels wide; expect gaps in ball tracking.");
  } else {
    score -= 55;
    notes.push(
      `At ${height}p the ball is about ${ballPixels} pixels across. Player stats will work; ball stats will be partial.`,
    );
  }
  if (duration < 60) {
    score -= 10;
    notes.push("Very short clip: fine for a highlight, too short for match-level stats.");
  } else if (duration > 3 * 3600) {
    score -= 20;
    notes.push("Over three hours: trim to the match itself.");
  }
  if (motion !== undefined) {
    if (motion > 0.5) {
      score -= 30;
      notes.push("Looks edited (frequent cuts or replays). Tracking resets at every cut; use the raw recording if you have it.");
    } else if (motion > 0.25) {
      score -= 10;
      notes.push("Some camera cuts or fast pans detected. A fixed camera gives steadier stats.");
    }
  }
  if (width / Math.max(1, height) < 1.3) {
    score -= 20;
    notes.push("Portrait or square video: film landscape so the whole pitch fits.");
  }
  score = Math.max(0, Math.min(100, score));
  const grade = score >= 85 ? "great" : score >= 65 ? "good" : score >= 40 ? "limited" : "poor";
  return { width, height, duration, ballPixels, score, grade, notes, motion };
}

function frameSignature(video: HTMLVideoElement, canvas: HTMLCanvasElement): number[] | null {
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  if (!ctx) return null;
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
  const { data } = ctx.getImageData(0, 0, canvas.width, canvas.height);
  // Coarse 4x4x4 colour histogram; robust to pans, sensitive to cuts.
  const hist = new Array(64).fill(0);
  for (let i = 0; i < data.length; i += 4) {
    const r = data[i] >> 6;
    const g = data[i + 1] >> 6;
    const b = data[i + 2] >> 6;
    hist[(r << 4) | (g << 2) | b]++;
  }
  const total = data.length / 4;
  return hist.map((v) => v / total);
}

function bhattacharyya(a: number[], b: number[]) {
  let s = 0;
  for (let i = 0; i < a.length; i++) s += Math.sqrt(a[i] * b[i]);
  return Math.sqrt(Math.max(0, 1 - s));
}

const seek = (video: HTMLVideoElement, t: number) =>
  new Promise<boolean>((resolve) => {
    const done = () => {
      cleanup();
      resolve(true);
    };
    const fail = () => {
      cleanup();
      resolve(false);
    };
    const timer = setTimeout(fail, 4000);
    const cleanup = () => {
      clearTimeout(timer);
      video.removeEventListener("seeked", done);
      video.removeEventListener("error", fail);
    };
    video.addEventListener("seeked", done, { once: true });
    video.addEventListener("error", fail, { once: true });
    video.currentTime = t;
  });

/**
 * Reads metadata and, when the browser can decode the file, samples pairs of
 * frames 0.5 s apart at several points to estimate how often the picture
 * changes abruptly (cuts/replays). Never throws: returns null if unreadable.
 */
export async function checkFootage(file: File, signal?: AbortSignal): Promise<FootageCheck | null> {
  const url = URL.createObjectURL(file);
  const video = document.createElement("video");
  video.muted = true;
  video.preload = "metadata";
  video.src = url;
  try {
    const ready = await new Promise<boolean>((resolve) => {
      const timer = setTimeout(() => resolve(false), 8000);
      video.onloadedmetadata = () => {
        clearTimeout(timer);
        resolve(true);
      };
      video.onerror = () => {
        clearTimeout(timer);
        resolve(false);
      };
    });
    if (!ready || !video.videoWidth) return null;
    const { videoWidth: width, videoHeight: height, duration } = video;
    let motion: number | undefined;
    if (isFinite(duration) && duration > 10) {
      const canvas = document.createElement("canvas");
      canvas.width = 64;
      canvas.height = 36;
      let pairs = 0;
      let cuts = 0;
      for (const fraction of [0.15, 0.3, 0.45, 0.6, 0.75, 0.9]) {
        if (signal?.aborted) break;
        const t = duration * fraction;
        if (!(await seek(video, t))) break;
        const a = frameSignature(video, canvas);
        if (!(await seek(video, Math.min(duration - 0.1, t + 0.5)))) break;
        const b = frameSignature(video, canvas);
        if (!a || !b) break;
        pairs++;
        if (bhattacharyya(a, b) > 0.35) cuts++;
      }
      if (pairs >= 3) motion = cuts / pairs;
    }
    return gradeFootage(width, height, isFinite(duration) ? duration : 0, motion);
  } catch {
    return null;
  } finally {
    video.removeAttribute("src");
    video.load();
    URL.revokeObjectURL(url);
  }
}
