"""Path helpers for methodology artifacts."""

from pathlib import Path

from episteme.config import CASES_DIR


def methodology_dir(case: str) -> Path:
    d = CASES_DIR / case / "methodology"
    d.mkdir(parents=True, exist_ok=True)
    return d


def profile_path(case: str) -> Path:
    return methodology_dir(case) / "profile.json"


def audits_dir(case: str) -> Path:
    d = methodology_dir(case) / "audits"
    d.mkdir(parents=True, exist_ok=True)
    return d


def audit_path(case: str, source_id: str) -> Path:
    safe = source_id.replace("/", "_").replace("\\", "_").replace(":", "_")
    return audits_dir(case) / f"{safe}.json"
