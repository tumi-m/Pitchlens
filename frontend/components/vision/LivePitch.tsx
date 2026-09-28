"use client";
import { PitchAnimation } from "@/components/vision/AnalysisWait";

/** Landing-page preview of the tracking animation (client-only motion). */
export function LivePitch() {
  return <PitchAnimation progress={100} />;
}
