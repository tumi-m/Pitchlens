import { NextRequest, NextResponse } from 'next/server';

export const maxDuration = 60;
export const runtime = 'nodejs';

const MAX_BYTES = 500 * 1024 * 1024; // keep in sync with the upload page limit

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

/**
 * For files over ~100MB Google returns an HTML "can't scan for viruses"
 * interstitial instead of the file — even when sharing is public. The real
 * download URL is in a <form action="https://drive.usercontent.google.com/download">
 * with hidden id/export/confirm/uuid inputs. Parse them and build the URL.
 */
function parseScanInterstitial(html: string): string | null {
  const action = html.match(/action="(https:\/\/drive\.usercontent\.google\.com\/download[^"]*)"/)?.[1];
  if (!action) return null;
  const params = new URLSearchParams();
  const inputRe = /<input[^>]+name="([^"]+)"[^>]+value="([^"]*)"/g;
  let m: RegExpExecArray | null;
  while ((m = inputRe.exec(html)) !== null) params.set(m[1], m[2]);
  if (!params.has('id')) return null;
  const sep = action.includes('?') ? '&' : '?';
  return `${action}${sep}${params.toString()}`;
}

function streamResponse(res: Response) {
  return new NextResponse(res.body, {
    headers: {
      'Content-Type': res.headers.get('content-type') || 'video/mp4',
      'Content-Disposition': 'attachment; filename="match.mp4"',
      'Cache-Control': 'no-store',
    },
  });
}

function tooLarge(res: Response): boolean {
  const len = Number(res.headers.get('content-length') || 0);
  return len > MAX_BYTES;
}

export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const url = searchParams.get('url');
  const type = searchParams.get('type'); // 'gdrive'

  if (!url) return NextResponse.json({ error: 'Missing url' }, { status: 400 });

  if (type === 'gdrive') {
    const fileId = extractGDriveId(url);
    if (!fileId) return NextResponse.json({ error: 'Could not extract Google Drive file ID' }, { status: 400 });

    try {
      // First attempt: direct download (works for small/scanned files)
      const res = await fetch(`https://drive.google.com/uc?export=download&id=${fileId}&confirm=t`, {
        redirect: 'follow',
        headers: { 'User-Agent': 'Mozilla/5.0' },
        signal: AbortSignal.timeout(45_000),
      });

      if (!res.ok) {
        return NextResponse.json({ error: `Google Drive returned ${res.status} — check the link and sharing settings` }, { status: 502 });
      }

      const contentType = res.headers.get('content-type') || '';

      if (!contentType.includes('text/html')) {
        if (tooLarge(res)) {
          return NextResponse.json({ error: 'File is over 500 MB — download it from Drive and use the Upload File tab instead.' }, { status: 413 });
        }
        return streamResponse(res);
      }

      // HTML response: either the virus-scan interstitial (large public file)
      // or a sign-in / permission wall.
      const html = await res.text();
      const followUp = parseScanInterstitial(html);
      if (!followUp) {
        return NextResponse.json({
          error: 'File requires Google sign-in or is not publicly shared. In Drive: Share → "Anyone with the link" → copy link.',
        }, { status: 403 });
      }

      const res2 = await fetch(followUp, {
        redirect: 'follow',
        headers: { 'User-Agent': 'Mozilla/5.0' },
        signal: AbortSignal.timeout(45_000),
      });
      if (!res2.ok || (res2.headers.get('content-type') || '').includes('text/html')) {
        return NextResponse.json({
          error: 'Google Drive blocked the automated download for this file. Download it from Drive and use the Upload File tab.',
        }, { status: 502 });
      }
      if (tooLarge(res2)) {
        return NextResponse.json({ error: 'File is over 500 MB — download it from Drive and use the Upload File tab instead.' }, { status: 413 });
      }
      return streamResponse(res2);
    } catch (err: any) {
      const msg = err?.name === 'TimeoutError'
        ? 'Google Drive took too long to respond — try a smaller file or the Upload File tab.'
        : `Fetch failed: ${err.message}`;
      return NextResponse.json({ error: msg }, { status: 502 });
    }
  }

  return NextResponse.json({ error: 'Unsupported type' }, { status: 400 });
}
