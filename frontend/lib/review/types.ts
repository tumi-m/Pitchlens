export interface Detection {
  x: number;
  y: number;
  width: number;
  height: number;
  confidence: number;
  class: string;
  class_id: number;
}
export interface VideoReview {
  schemaVersion: 1;
  source: "local-video";
  fileName: string;
  fileSize: number;
  duration: number;
  width: number;
  height: number;
  aiStatus: "not-requested" | "completed" | "partial" | "failed";
  aiMessage?: string;
  frames: Array<{
    timestamp: number;
    image: string;
    width: number;
    height: number;
    predictions: Detection[];
  }>;
  events: ReviewEvent[];
  notes: string;
}
export interface ReviewEvent {
  id: string;
  timestamp: number;
  team: "home" | "away";
  type: "goal" | "shot" | "save" | "pass" | "foul" | "corner";
  note: string;
  source: "manual";
}
export function summariseEvents(events: ReviewEvent[]) {
  const count = (team: "home" | "away", type: ReviewEvent["type"]) =>
    events.filter((e) => e.team === team && e.type === type).length;
  return (["home", "away"] as const).map((team) => ({
    team,
    goals: count(team, "goal"),
    shots: count(team, "shot") + count(team, "goal"),
    saves: count(team, "save"),
    passes: count(team, "pass"),
    fouls: count(team, "foul"),
    corners: count(team, "corner"),
  }));
}
