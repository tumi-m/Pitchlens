import { VisionReport } from "@/components/vision/VisionReport";
export default async function Page({
  params,
}: {
  params: Promise<{ jobId: string }>;
}) {
  const { jobId } = await params;
  return <VisionReport jobId={jobId} />;
}
