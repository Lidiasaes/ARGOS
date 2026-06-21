"""YouTube transcript download for video sources in sources.json."""

from __future__ import annotations

import json
import re
from pathlib import Path

from episteme.config import BASE_DIR, CASES_DIR

_YOUTUBE_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?v=|embed/|shorts/)|youtu\.be/)([A-Za-z0-9_-]{11})"
)


def is_youtube_url(url: str) -> bool:
    return bool(url and _YOUTUBE_RE.search(url))


def extract_video_id(url: str) -> str | None:
    if not url:
        return None
    match = _YOUTUBE_RE.search(url)
    return match.group(1) if match else None


def is_video_source(source: dict) -> bool:
    return (source.get("content_type") or "").lower() in ("video", "audio")


def _local_path_exists(local_path: str | None) -> bool:
    if not local_path:
        return False
    path = Path(local_path)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path.exists() and path.stat().st_size > 0


def needs_transcript_download(source: dict) -> bool:
    """True when source is a YouTube video without a usable local transcript file."""
    if not is_video_source(source):
        return False
    url = source.get("url") or ""
    if not is_youtube_url(url):
        return False
    return not _local_path_exists(source.get("local_path"))


def transcript_local_path(case: str, video_id: str) -> str:
    return f"cases/{case}/files/youtube_{video_id}.txt"


def fetch_transcript_text(video_id: str, languages: list[str] | None = None) -> str:
    """Download transcript from YouTube. Raises on failure."""
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api._errors import (
        NoTranscriptFound,
        TranscriptsDisabled,
        VideoUnavailable,
    )

    langs = languages or ["en", "en-US", "en-GB"]
    api = YouTubeTranscriptApi()
    try:
        transcript = api.fetch(video_id, languages=langs)
    except NoTranscriptFound:
        available = api.list(video_id)
        transcript = available.find_generated_transcript(["en"]).fetch()
    except (TranscriptsDisabled, VideoUnavailable) as exc:
        raise RuntimeError(str(exc)) from exc

    lines = [snippet.text.strip() for snippet in transcript.snippets if snippet.text.strip()]
    body = "\n".join(lines).strip()
    if not body:
        raise RuntimeError(f"Empty transcript for video {video_id}")
    return body


def _write_transcript_file(
    case: str,
    video_id: str,
    source: dict,
    body: str,
) -> Path:
    rel = transcript_local_path(case, video_id)
    path = BASE_DIR / rel
    path.parent.mkdir(parents=True, exist_ok=True)

    title = (source.get("title") or "").strip()
    url = source.get("url") or ""
    author = source.get("author") or ""
    date = source.get("date") or ""
    header = [
        f"# YouTube transcript — {title or video_id}",
        f"# URL: {url}",
    ]
    if author:
        header.append(f"# Author: {author}")
    if date:
        header.append(f"# Date: {date}")
    header.append("")

    path.write_text("\n".join(header) + body, encoding="utf-8")
    return path


def download_transcript_for_source(case: str, source: dict) -> str:
    """
    Download transcript for one source entry.
    Returns relative local_path on success, or FETCH_ERROR message.
    """
    url = source.get("url") or ""
    video_id = extract_video_id(url)
    if not video_id:
        return "FETCH_ERROR: Could not parse YouTube video id from URL"

    try:
        body = fetch_transcript_text(video_id)
        _write_transcript_file(case, video_id, source, body)
        return transcript_local_path(case, video_id)
    except Exception as exc:
        return f"FETCH_ERROR: YouTube transcript — {exc}"


def _save_sources(case: str, sources: list) -> None:
    path = CASES_DIR / case / "sources.json"
    path.write_text(json.dumps(sources, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def prepare_youtube_transcripts(case: str, sources: list | None = None) -> list:
    """
    For each YouTube video source missing a local file:
    download transcript, save under cases/{case}/files/, set local_path.

    Persists sources.json when any transcript is newly downloaded.
    Returns the (possibly updated) sources list.
    """
    sources = list(sources if sources is not None else _load_sources(case))
    changed = False
    downloaded = 0
    skipped = 0
    failed = 0

    for source in sources:
        if not needs_transcript_download(source):
            if is_video_source(source) and is_youtube_url(source.get("url") or ""):
                skipped += 1
            continue

        label = source.get("title") or source.get("url") or "video"
        print(f"  [youtube] downloading transcript: {label}")

        result = download_transcript_for_source(case, source)
        if result.startswith("FETCH_ERROR"):
            failed += 1
            print(f"    x {result}")
            continue

        source["local_path"] = result
        changed = True
        downloaded += 1
        print(f"    + saved {result}")

    if changed:
        _save_sources(case, sources)

    if downloaded or failed:
        print(f"  YouTube transcripts: {downloaded} downloaded, {skipped} cached, {failed} failed")
    elif skipped:
        print(f"  YouTube transcripts: {skipped} already on disk")

    return sources


def _load_sources(case: str) -> list:
    path = CASES_DIR / case / "sources.json"
    return json.loads(path.read_text(encoding="utf-8"))
