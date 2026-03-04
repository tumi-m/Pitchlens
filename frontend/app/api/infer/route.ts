import { NextRequest, NextResponse } from 'next/server';

export const maxDuration = 30;
export const runtime = 'nodejs';

const ROBOFLOW_PROJECT = process.env.ROBOFLOW_PROJECT ?? 'football-players-detection-3zvbc';
const ROBOFLOW_VERSION = process.env.ROBOFLOW_VERSION ?? '9';

export async function POST(request: NextRequest) {
  try {
    const { frame } = await request.json();
    if (!frame) {
      return NextResponse.json({ error: 'Missing frame data' }, { status: 400 });
    }

    const apiKey = process.env.ROBOFLOW_API_KEY;
    if (!apiKey) {
      // No API key configured — signal client to use mock stats
      return NextResponse.json({ predictions: [], mock: true });
    }

    const response = await fetch(
      `https://detect.roboflow.com/${ROBOFLOW_PROJECT}/${ROBOFLOW_VERSION}?api_key=${apiKey}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: frame, // raw base64 JPEG (no data URI prefix)
      }
    );

    if (!response.ok) {
      const errText = await response.text();
      console.error('[infer] Roboflow error:', errText);
      return NextResponse.json({ predictions: [] });
    }

    const data = await response.json();
    return NextResponse.json({ predictions: data.predictions ?? [] });
  } catch (err: any) {
    console.error('[infer] exception:', err.message);
    return NextResponse.json({ predictions: [] });
  }
}
