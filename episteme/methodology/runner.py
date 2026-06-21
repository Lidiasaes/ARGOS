"""Methodology pipeline orchestrator."""

from episteme.core.cache import Cache
from episteme.methodology.audit import audit_source
from episteme.methodology.criteria import ensure_methodology_profile
from episteme.pipeline.sources import load_sources, source_id, get_content


def run_methodology(
    case: str,
    cache: Cache,
    source_filter: str | None = None,
    profile_only: bool = False,
    force_profile: bool = False,
):
    sources = load_sources(case)

    raw_by_id: dict[str, str] = {}
    for source in sources:
        sid = source_id(source)
        if sid:
            raw_by_id[sid] = cache.get_or_run("raw", sid, lambda s=source: get_content(s))

    profile = ensure_methodology_profile(
        case, cache, sources, raw_by_id, force=force_profile or cache.reset
    )

    if profile_only:
        print(f"  Profile only — saved to cases/{case}/methodology/profile.json")
        return profile

    audits = []
    for source in sources:
        sid = source_id(source)
        if not sid:
            continue
        if source_filter and sid != source_filter and source_filter not in (source.get("local_path") or ""):
            continue

        label = source.get("local_path") or source.get("url") or sid
        print(f"\n  -> audit {label}")

        raw = raw_by_id.get(sid)
        if raw is None:
            raw = cache.get_or_run("raw", sid, lambda s=source: get_content(s))

        if isinstance(raw, str) and raw.startswith("FETCH_ERROR"):
            print(f"    x {raw}")
            continue

        audit = audit_source(case, cache, source, sid, raw, profile)
        score = audit.get("methodology_score", 0)
        n_eval = len(audit.get("evaluations", []))
        print(f"    score={score:.2f} | {n_eval} evaluations")
        audits.append(audit)

    print(f"\n  Methodology: {len(audits)} source audits for case '{case}'")
    return {"profile": profile, "audits": audits}
