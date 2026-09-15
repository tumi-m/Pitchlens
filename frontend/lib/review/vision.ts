export type VisionJob = {
  id: string;
  title: string;
  status: string;
  stage: string;
  progress: number;
  createdAt: number;
  processedSeconds?: number;
  etaSeconds?: number;
  video?: { duration: number };
};
export type VisionPlayer = {
  id: number;
  team: number;
  box: number[];
  confidence: number;
};
export type VisionFrame = {
  t: number;
  scene: number;
  players: VisionPlayer[];
  ball: { x: number; y: number; box: number[]; confidence: number } | null;
};
export type VisionResult = {
  schemaVersion: 1;
  source: "computer-vision";
  model: string;
  modelSha256: string;
  ballModel?: string;
  ballModelSha256?: string;
  video: { duration: number; width: number; height: number; fps: number };
  analysedDuration: number;
  sampleFps: number;
  teams: { id: number; label: string; colour: string }[];
  metrics: {
    sampledFrames: number;
    playerFrames: number;
    ballFrames: number;
    teamSeconds: number[];
    unknownSeconds: number;
    possessionShare: (number | null)[];
    possessionCoverage: number;
    trackCount: number;
    events: {
      id: string;
      type: string;
      t: number;
      from: number;
      to: number;
      team: number;
      confidence: number;
      status: string;
    }[];
  };
  frames: VisionFrame[];
  limitations: string[];
};
export const clockTime = (seconds: number) =>
  `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
export async function visionJson<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(`/api/vision/${path}`, init);
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Vision request failed");
  return data;
}
