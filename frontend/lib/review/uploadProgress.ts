/** Upload progress in words people can act on: sizes as phones show them, a percent
 *  that moves on a 2 GB file, and the time left once the speed is known. */

const KB = 1024;
const MB = 1024 * KB;
const GB = 1024 * MB;

/** 1024-based, as Photos and Files count: 2,000,000,000 bytes is "1.9 GB". */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes >= GB) return `${(bytes / GB).toFixed(1)} GB`;
  if (bytes >= 10 * MB) return `${Math.round(bytes / MB)} MB`;
  if (bytes >= MB) return `${(bytes / MB).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / KB))} KB`;
}

/** Rounded down, so 100% means every byte arrived. Files over 1 GB show tenths: a
 *  whole percent of a 2 GB upload is 20 MB, half a minute on a slow connection. */
export function percentLabel(sent: number, total: number): string {
  if (!(total > 0)) return "0%";
  const share = Math.min(1, Math.max(0, sent / total));
  return total > GB ? `${(Math.floor(share * 1000) / 10).toFixed(1)}%` : `${Math.floor(share * 100)}%`;
}

export type UploadAck = { at: number; sent: number };

/** Seconds of speed history the estimate uses: a phone's speed changes as it moves. */
const WINDOW_MS = 60_000;

/** Seconds left at the speed of the last minute of acknowledged pieces; null until
 *  three acknowledgements (one piece is not a speed) or while nothing moves. */
export function etaSeconds(acks: UploadAck[], total: number): number | null {
  if (acks.length < 3) return null;
  const last = acks[acks.length - 1];
  const recent = acks.filter((a) => last.at - a.at <= WINDOW_MS);
  if (recent.length < 3) return null;
  const first = recent[0];
  const seconds = (last.at - first.at) / 1000;
  const rate = seconds > 0 ? (last.sent - first.sent) / seconds : 0;
  if (!(rate > 0)) return null;
  return Math.max(0, total - last.sent) / rate;
}

/** Minutes a file takes at 10 Mbit/s, the speed the upload advice assumes. */
export const minutesAt10Mbit = (bytes: number) => Math.max(1, Math.ceil((bytes * 8) / 10e6 / 60));
