"""Path helpers for reasoning artifacts."""

from pathlib import Path

from episteme.config import CASES_DIR


def reasoning_dir(case: str) -> Path:
    d = CASES_DIR / case / "reasoning"
    d.mkdir(parents=True, exist_ok=True)
    return d


def presuppositions_path(case: str) -> Path:
    return reasoning_dir(case) / "presuppositions.json"


def open_questions_path(case: str) -> Path:
    return reasoning_dir(case) / "open_questions.json"


def field_briefing_path(case: str) -> Path:
    return reasoning_dir(case) / "field_briefing.md"


def devils_advocate_dir(case: str) -> Path:
    d = reasoning_dir(case) / "devils_advocate"
    d.mkdir(parents=True, exist_ok=True)
    return d


def devils_advocate_path(case: str, claim_id: str) -> Path:
    return devils_advocate_dir(case) / f"{claim_id}.json"
