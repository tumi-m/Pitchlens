import { NextRequest, NextResponse } from 'next/server';

export const maxDuration = 30;
export const runtime = 'nodejs';

const ROBOFLOW_PROJECT = process.env.ROBOFLOW_PROJECT ?? 'football-players-detection-3zvbc';
const ROBOFLOW_VERSION = process.env.ROBOFLOW_VERSION ?? '9';

/** Preflight: lets the client know whether real inference is available,
 *  so it can decide how long to wait for YOLOv8 vs falling back to demo stats. */
export async function GET() {
  return NextResponse.json({ configured: !!process.env.ROBOFLOW_API_KEY });
}

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
      `https://detect.roboflow.com/${ROBOFLOW_PROJECT}/${ROBOFLOW_VERSION}` +
      `?api_key=${apiKey}&confidence=35&overlap=30&format=json`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: frame,
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
