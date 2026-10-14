/**
 * Analysed frames store t = round(frame / fps, 3), up to half a millisecond
 * either side of the frame's real timestamp. Seeking exactly to a t that was
 * rounded down shows the previous video frame, and a presented frame can
 * report a time just below a t that was rounded up. These helpers absorb both.
 */

type Timed = { t: number };

/** Where to seek to show the analysed frame stored at t (1 ms past it). */
export const seekTarget = (t: number) => t + 0.001;

/** Half a source frame: how far a presented frame's time may be from its stored t. */
const halfFrame = (fps: number) => 0.5 / (fps || 30);

/**
 * The analysed frame to draw at `time`: the last one stored at or before it
 * (allowing half a source frame), or -1 when that one is more than 1.5 analysed
 * frames away (a gap in the analysis).
 */
export function frameIndexAt(frames: Timed[], time: number, fps: number, sampleFps: number): number {
  if (!frames.length) return -1;
  const limit = time + halfFrame(fps);
  let l = 0;
  let r = frames.length - 1;
  while (l < r) {
    const m = Math.ceil((l + r) / 2);
    if (frames[m].t <= limit) l = m;
    else r = m - 1;
  }
  return Math.abs(frames[l].t - time) <= (1 / sampleFps) * 1.5 ? l : -1;
}

/** Index of the analysed frame stored closest to t. */
export function nearestFrame(frames: Timed[], t: number): number {
  let l = 0;
  let r = frames.length - 1;
  while (l < r) {
    const m = Math.floor((l + r) / 2);
    if (frames[m].t < t) l = m + 1;
    else r = m;
  }
  if (l > 0 && Math.abs(frames[l - 1].t - t) <= Math.abs(frames[l].t - t)) return l - 1;
  return l;
}

/** The stored t of the analysed frame on screen at `time`, or null when no analysed frame is within half a source frame. */
export function analysedFrameTime(frames: Timed[], time: number, fps: number): number | null {
  if (!frames.length) return null;
  const nearest = frames[nearestFrame(frames, time)].t;
  return Math.abs(nearest - time) <= halfFrame(fps) ? nearest : null;
}
