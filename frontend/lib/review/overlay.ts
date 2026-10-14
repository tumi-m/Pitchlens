import type { VisionFrame, VisionPlayer } from "./vision";

/** One id -> player map per analysed frame, built the first time it is asked for. */
export function playerMaps(frames: VisionFrame[]) {
  const cache = new Map<number, Map<number, VisionPlayer>>();
  return (index: number) => {
    let map = cache.get(index);
    if (!map) {
      map = new Map((frames[index]?.players ?? []).map((p) => [p.id, p]));
      cache.set(index, map);
    }
    return map;
  };
}

/**
 * What the detection overlay draws at `time`, given the held frame `index` (the
 * last analysed frame at or before it). Between two analysed frames of the same
 * scene that are at most 1.5 sample intervals apart, a track present in both is
 * moved linearly from one box to the next: at 3 fps a held box is drawn up to
 * 333 ms late (on real footage the mean error fell from 10.5 to 3.4 px). Tracks
 * seen in only one of the two frames stay where they were. The ball is always
 * held: its flight is not linear between samples (interpolating it was worse).
 */
export function overlayAt(
  frames: VisionFrame[],
  index: number,
  time: number,
  sampleFps: number,
  maps: (index: number) => Map<number, VisionPlayer>,
): { players: VisionPlayer[]; ball: VisionFrame["ball"] } {
  const held = frames[index];
  if (!held) return { players: [], ball: null };
  const next = frames[index + 1];
  const gap = next ? next.t - held.t : 0;
  if (!next || next.scene !== held.scene || !(gap > 0) || gap > 1.5 / sampleFps)
    return { players: held.players, ball: held.ball };
  const k = Math.min(1, Math.max(0, (time - held.t) / gap));
  if (k === 0) return { players: held.players, ball: held.ball };
  const later = maps(index + 1);
  const players = held.players.map((p) => {
    const q = later.get(p.id);
    if (!q || q.box.length !== p.box.length) return p;
    return { ...p, box: p.box.map((v, i) => v + (q.box[i] - v) * k) };
  });
  return { players, ball: held.ball };
}

/** A magnified crop of the frame, in source pixels. */
export type ZoomCrop = { x0: number; y0: number; w: number; h: number };

/**
 * The crop a first tap opens for precise placement: 1/zoom of the frame in each
 * direction, centred on the tap and moved back inside the frame at its edges.
 */
export function zoomCrop(x: number, y: number, width: number, height: number, zoom: number): ZoomCrop {
  const w = width / zoom;
  const h = height / zoom;
  const x0 = Math.min(Math.max(0, x - w / 2), width - w);
  const y0 = Math.min(Math.max(0, y - h / 2), height - h);
  return { x0, y0, w, h };
}

/** A tap inside the zoomed view, as fractions (0-1) of its box, back to source pixels. */
export function zoomToSource(crop: ZoomCrop, fx: number, fy: number): { x: number; y: number } {
  const clamp = (v: number) => Math.min(1, Math.max(0, v));
  return { x: crop.x0 + clamp(fx) * crop.w, y: crop.y0 + clamp(fy) * crop.h };
}
