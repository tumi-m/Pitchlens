import { NextRequest, NextResponse } from 'next/server';

export const maxDuration = 60;
export const runtime = 'nodejs';

function extractGDriveId(url: string): string | null {
  const patterns = [
    /\/file\/d\/([a-zA-Z0-9_-]+)/,
    /id=([a-zA-Z0-9_-]+)/,
    /\/d\/([a-zA-Z0-9_-]+)/,
  ];
  for (const pattern of patterns) {
    const match = url.match(pattern);
    if (match) return match[1];
  }
  return null;
}

export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const url = searchParams.get('url');
  const type = searchParams.get('type'); // 'gdrive'

  if (!url) return NextResponse.json({ error: 'Missing url' }, { status: 400 });

  if (type === 'gdrive') {
    const fileId = extractGDriveId(url);
    if (!fileId) return NextResponse.json({ error: 'Could not extract Google Drive file ID' }, { status: 400 });

    // First attempt: direct download
    const downloadUrl = `https://drive.google.com/uc?export=download&id=${fileId}&confirm=t`;

    try {
      const res = await fetch(downloadUrl, {
        redirect: 'follow',
        headers: { 'User-Agent': 'Mozilla/5.0' },
        signal: AbortSignal.timeout(45_000),
      });

      if (!res.ok) {
        return NextResponse.json({ error: `Google Drive returned ${res.status}` }, { status: 502 });
      }

      const contentType = res.headers.get('content-type') || 'video/mp4';

      // Only proxy if it looks like a video (not an HTML error page)
      if (contentType.includes('text/html')) {
        return NextResponse.json({
          error: 'File requires Google sign-in or is not publicly shared. Make sure sharing is set to "Anyone with the link".',
        }, { status: 403 });
      }

      return new NextResponse(res.body, {
        headers: {
          'Content-Type': contentType,
          'Content-Disposition': 'attachment; filename="match.mp4"',
          'Cache-Control': 'no-store',
        },
      });
    } catch (err: any) {
      return NextResponse.json({ error: `Fetch failed: ${err.message}` }, { status: 502 });
    }
  }

  return NextResponse.json({ error: 'Unsupported type' }, { status: 400 });
}
