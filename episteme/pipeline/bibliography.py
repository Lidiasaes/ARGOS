"""Bibliography sidecar files — registered at ingest, not extracted to graph."""

import json
from datetime import datetime, timezone
from pathlib import Path

from episteme.config import BASE_DIR, CASES_DIR
from episteme.pipeline.sources import fetch_local, source_id


def bibliography_path(source: dict) -> Path | None:
    """Resolve optional bibliography file path from source entry."""
    rel = source.get("bibliography")
    if not rel:
        return None
    path = Path(rel)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


def bibliography_dir(case: str) -> Path:
    d = CASES_DIR / case / "bibliography"
    d.mkdir(parents=True, exist_ok=True)
    return d


def manifest_path(case: str) -> Path:
    return bibliography_dir(case) / "manifest.json"


def load_manifest(case: str) -> dict:
    path = manifest_path(case)
    if not path.exists():
        return {"case": case, "sources": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(case: str, manifest: dict) -> None:
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
    manifest_path(case).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def register_bibliography(case: str, source: dict) -> dict | None:
    """
    Validate bibliography sidecar exists and record in manifest.
    Does NOT ingest bibliography into chunks/graph (reserved for research agents).
    """
    bib_path = bibliography_path(source)
    if bib_path is None:
        return None

    sid = source_id(source)
    if not bib_path.exists():
        print(f"    Warning: bibliography file not found: {bib_path}")
        return None

    preview = fetch_local(bib_path)
    if preview.startswith("FETCH_ERROR"):
        print(f"    Warning: could not read bibliography: {preview}")
        return None

    entry = {
        "source_id": sid,
        "bibliography_path": str(source.get("bibliography")),
        "local_path": source.get("local_path"),
        "title": source.get("title", ""),
        "author": source.get("author", ""),
        "date": source.get("date", ""),
        "char_count": len(preview),
        "line_count": len(preview.splitlines()),
        "registered_at": datetime.now(timezone.utc).isoformat(),
    }

    manifest = load_manifest(case)
    sources = [s for s in manifest.get("sources", []) if s.get("source_id") != sid]
    sources.append(entry)
    manifest["case"] = case
    manifest["sources"] = sources
    save_manifest(case, manifest)

    print(
        f"    Bibliography registered ({entry['line_count']} lines) — "
        f"not ingested (future research agents)"
    )
    return entry
