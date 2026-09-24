"""Fetch a YouTube match video onto the worker so it can be analysed.

Only single YouTube videos are accepted: the URL is reduced to its video id and
rebuilt, so a link can never point the worker at another host or a playlist.
Users must own the video or have permission to analyse it (asked in the UI).
"""

import os
import re
from pathlib import Path

VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
PATTERNS = [
    re.compile(r"^https?://(?:www\.|m\.|music\.)?youtube\.com/watch\?(?:.*&)?v=([A-Za-z0-9_-]{11})"),
    re.compile(r"^https?://(?:www\.|m\.)?youtube\.com/(?:shorts|live|embed)/([A-Za-z0-9_-]{11})"),
    re.compile(r"^https?://youtu\.be/([A-Za-z0-9_-]{11})"),
]
MAX_SECONDS = 3 * 3600


def video_id(url):
    url = (url or "").strip()
    for pattern in PATTERNS:
        match = pattern.match(url)
        if match:
            return match.group(1)
    raise ValueError("Paste a link to a single YouTube video (youtube.com/watch?v=… or youtu.be/…).")


def friendly(message):
    text = str(message)
    lowered = text.lower()
    if "not a bot" in lowered or "sign in to confirm" in lowered:
        return (
            "YouTube blocked the analysis server from downloading this video. "
            "Download it yourself and use Upload a file instead."
        )
    if "private video" in lowered:
        return "This YouTube video is private. Make it public or unlisted, or upload the file."
    if "age" in lowered and "restrict" in lowered or "confirm your age" in lowered:
        return "This YouTube video is age-restricted and cannot be fetched. Upload the file instead."
    if "unavailable" in lowered or "removed" in lowered:
        return "This YouTube video is unavailable."
    if "live" in lowered and ("not" in lowered or "upcoming" in lowered):
        return "Live streams can be analysed once they have finished and been processed by YouTube."
    return "Could not fetch this YouTube video. Download it and use Upload a file instead."


def download(url, directory, max_bytes, progress=lambda **kw: None, cancelled=lambda: False):
    """Download the best MP4 video stream up to 1080p. Returns (path, info)."""
    import yt_dlp

    vid = video_id(url)
    directory = Path(directory)

    def hook(state):
        if cancelled():
            raise InterruptedError("Download cancelled")
        if state.get("status") == "downloading":
            total = state.get("total_bytes") or state.get("total_bytes_estimate") or 0
            done = state.get("downloaded_bytes") or 0
            progress(
                stage="Downloading from YouTube",
                progress=round(done / total * 100, 1) if total else 0,
                receivedBytes=done,
            )

    def check(info, *, incomplete):
        duration = info.get("duration") or 0
        if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
            return "live"
        if duration > MAX_SECONDS:
            return "too long"
        return None

    options = {
        # Video only: no audio track means no ffmpeg merge step on the worker.
        # H.264 MP4 first because every browser can play it back in the report.
        "format": (
            "bv*[height<=1080][ext=mp4][vcodec^=avc1]/bv*[height<=1080][ext=mp4]"
            "/b[height<=1080][ext=mp4]/bv*[height<=1080]/b[height<=1080]"
        ),
        "outtmpl": str(directory / "download.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "max_filesize": max_bytes,
        "match_filter": check,
        "progress_hooks": [hook],
        "retries": 3,
        "fragment_retries": 3,
        "cachedir": False,
        "restrictfilenames": True,
    }
    if os.getenv("VISION_YOUTUBE_PROXY"):
        options["proxy"] = os.environ["VISION_YOUTUBE_PROXY"]
    if os.getenv("VISION_YOUTUBE_COOKIES"):
        options["cookiefile"] = os.environ["VISION_YOUTUBE_COOKIES"]

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={vid}", download=True)
    except InterruptedError:
        raise
    except yt_dlp.utils.DownloadError as exc:
        if isinstance(getattr(exc, "exc_info", [None, None])[1], InterruptedError):
            raise InterruptedError("Download cancelled") from exc
        raise ValueError(friendly(exc)) from exc
    if not info:
        raise ValueError("YouTube returned no video for this link.")
    if (info.get("duration") or 0) > MAX_SECONDS:
        raise ValueError("Videos longer than three hours are not supported.")
    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
        raise ValueError(friendly("live not finished"))
    files = [p for p in directory.glob("download.*") if not p.name.endswith(".part")]
    if not files:
        raise ValueError(
            "This video is larger than 500 MB at 1080p. Trim it or upload a shorter clip."
        )
    return files[0], {
        "title": info.get("title"),
        "duration": info.get("duration"),
        "height": info.get("height"),
        "url": f"https://www.youtube.com/watch?v={vid}",
    }
