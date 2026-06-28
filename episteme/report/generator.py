import json
import re

from episteme.config import (
    REPORTS_DIR,
    MODEL_SMART,
    REPORT_SECTION_MAX_TOKENS,
    REPORT_SECTION_CONTINUATION_TOKENS,
    REPORT_SECTION_MAX_CONTINUATIONS,
    REPORT_CACHE_VERSION,
)
from episteme.compile.crystallize import load_compiled, compiled_summary_for_llm, run_crystallize
from episteme.core.cache import Cache, content_hash
from episteme.core.llm import call_llm
from episteme.prompts import HEALTH_REPORT_SECTION, REPORT_SECTIONS

_CONTINUATION_PROMPT = """
Continue writing section {section_num} ({section_title}) of the Epistemic Health Report for case {case}.

Rules:
- Do NOT repeat the section heading.
- Continue exactly where the previous text left off.
- Complete any unfinished sentence first, then continue.
- Match the same analytical tone and markdown style.
- Stop when the section is fully complete.

COMPILED INDEX (reference):
{compiled_index}

PREVIOUS TEXT (ends here — continue after this):
{previous_tail}
"""


def generate_report(case: str, store, cache=None) -> None:
    """Generate Epistemic Health Report section-by-section from compiled index."""
    index = load_compiled(case)
    if index is None:
        if cache is None:
            cache = Cache(case=case, reset=False)
        print("  No compiled index found — running crystallize first...")
        index = run_crystallize(case, cache, store)

    compiled_json = compiled_summary_for_llm(index)
    if cache is None:
        cache = Cache(case=case, reset=False)

    parts = []
    for i, section_title in enumerate(REPORT_SECTIONS, start=1):
        section_id = f"report_section::{REPORT_CACHE_VERSION}::{case}::{i}::{content_hash(compiled_json)}"
        section_text = cache.get_or_run(
            "agent",
            section_id,
            lambda t=section_title, n=i: _generate_section(case, n, t, compiled_json),
        )
        if isinstance(section_text, dict):
            section_text = section_text.get("text", section_text.get("raw_response", ""))
        if not (section_text and section_text.strip()):
            section_text = _load_fallback_section(cache, case, i) or section_text
        parts.append(section_text.strip())

    if not any(p for p in parts):
        path = REPORTS_DIR / f"{case}_report.md"
        if path.exists():
            print("  Report generation failed — keeping existing report at", path)
        else:
            print("  Report generation failed — no section content produced.")
        return

    report = f"# Epistemic Health Report: {case}\n\n" + "\n\n".join(parts)
    report = _append_groundedness_note(report, store, index)
    REPORTS_DIR.mkdir(exist_ok=True)
    path = REPORTS_DIR / f"{case}_report.md"
    path.write_text(report, encoding="utf-8")
    incomplete = _count_incomplete_sections(parts)
    status = f"{len(REPORT_SECTIONS)} sections"
    if incomplete:
        status += f" ({incomplete} may still be incomplete — re-run with --reset-cache)"
    print(f"\n  Report saved: {path} ({len(report):,} chars, {status})")


def _append_groundedness_note(report: str, store, index: dict) -> str:
    """Audit the assembled report against the source graph and append a transparency
    note. Never blocks report generation — degrades silently if embeddings are
    unavailable or anything goes wrong."""
    try:
        from episteme.report.groundedness import audit_groundedness, build_transparency_note

        graph = {n["id"]: n for n in store.get_all_nodes()}
        audit = audit_groundedness(report, graph, index)
        if not audit:
            return report
        print(
            f"  Groundedness: {audit['explicit_pct']:.0f}% explicit-ID, "
            f"{audit['ungrounded_pct']:.1f}% ungrounded, "
            f"{audit['id_validity_pct']:.0f}% ID validity"
        )
        return report.rstrip() + "\n\n---\n\n" + build_transparency_note(audit) + "\n"
    except Exception as e:  # pragma: no cover - defensive
        print(f"  [warn] groundedness note skipped: {e}")
        return report


def _generate_section(case: str, section_num: int, section_title: str, compiled_json: str) -> str:
    text = call_llm(
        HEALTH_REPORT_SECTION.format(
            case=case,
            section_num=section_num,
            section_title=section_title,
            compiled_index=compiled_json,
        ),
        model=MODEL_SMART,
        max_tokens=REPORT_SECTION_MAX_TOKENS,
        label=f"report_s{section_num}",
    )
    text = text if isinstance(text, str) else str(text)

    for cont in range(REPORT_SECTION_MAX_CONTINUATIONS):
        if _section_looks_complete(text):
            break
        print(f"    section {section_num} looks incomplete — continuation {cont + 1}")
        tail = text[-1200:] if len(text) > 1200 else text
        more = call_llm(
            _CONTINUATION_PROMPT.format(
                case=case,
                section_num=section_num,
                section_title=section_title,
                compiled_index=compiled_json,
                previous_tail=tail,
            ),
            model=MODEL_SMART,
            max_tokens=REPORT_SECTION_CONTINUATION_TOKENS,
            label=f"report_s{section_num}_cont{cont + 1}",
        )
        more = more if isinstance(more, str) else str(more)
        if not more.strip():
            break
        text = text.rstrip() + "\n\n" + more.strip()

    return text


def _section_looks_complete(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 100:
        return True
    last = stripped[-1]
    if last in ".!?`:)]}\"'":
        return True
    if last.isalnum() or last in ",-;":
        return False
    lines = [ln.strip() for ln in stripped.splitlines() if ln.strip()]
    if lines and re.match(r"^#{1,4}\s", lines[-1]):
        return False
    return True


def _count_incomplete_sections(parts: list[str]) -> int:
    return sum(1 for p in parts if not _section_looks_complete(p))


def _load_fallback_section(cache, case: str, section_num: int) -> str:
    for version in ("", REPORT_CACHE_VERSION):
        key = (
            f"report_section::{version}::{case}::{section_num}"
            if version
            else f"report_section::{case}::{section_num}"
        )
        path = cache._path("agent", key)
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        text = data if isinstance(data, str) else data.get("text", "")
        if text and text.strip():
            print(f"    section {section_num}: using cached fallback ({path.name})")
            return text
    return ""
