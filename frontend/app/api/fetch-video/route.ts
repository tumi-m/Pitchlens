import { NextResponse } from 'next/server';
// Large remote video proxying is unsuitable for the deployment request limits.
export async function GET() {
  return NextResponse.json({ error: 'Download the video from Drive, then select the file in the review room.' }, { status: 410 });
}
