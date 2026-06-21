"""Detect when an attestation quote contradicts or fails to support its claim.

Stance detection is derived from each case's profile (debate_positions, subfields,
central_questions) — never from domain-specific hardcoded regex.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_STOP_WORDS = frozenset(
    {
        "about", "after", "also", "among", "based", "been", "being", "between",
        "both", "could", "does", "each", "even", "from", "have", "here", "into",
        "likely", "more", "most", "much", "must", "only", "other", "over", "same",
        "some", "such", "than", "that", "their", "them", "then", "there", "these",
        "they", "this", "those", "through", "under", "very", "were", "what", "when",
        "where", "which", "while", "with", "would", "your",
    }
)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:48]


def _tokenize(text: str, *, min_len: int = 3) -> set[str]:
    tokens = re.findall(r"\b[a-z][a-z0-9-]*\b", text.lower())
    return {t for t in tokens if len(t) >= min_len and t not in _STOP_WORDS}


def _keyword_regex(keywords: set[str]) -> re.Pattern | None:
    parts: list[str] = []
    for kw in sorted(keywords, key=len, reverse=True):
        if len(kw) >= 4:
            parts.append(re.escape(kw) + r"\w*")
        else:
            parts.append(re.escape(kw))
    if not parts:
        return None
    return re.compile(r"\b(" + "|".join(parts) + r")\b", re.I)


def _phrase_regex(phrase: str) -> re.Pattern | None:
    words = re.findall(r"[a-zA-Z0-9]+", phrase)
    if len(words) < 2:
        return None
    body = r"[\s-]?".join(re.escape(w) for w in words)
    return re.compile(r"\b" + body + r"\b", re.I)


@dataclass
class DebatePosition:
    id: str
    label: str
    keywords: set[str] = field(default_factory=set)
    phrase_pattern: re.Pattern | None = None
    keyword_pattern: re.Pattern | None = None


_META_POSITION_LABEL = re.compile(
    r"\b(judge|conclusion|analysis|methodology|review|summary|meta)\b",
    re.I,
)


def _is_primary_position(label: str) -> bool:
    head = label.split(":", 1)[0].strip()
    return bool(head) and not _META_POSITION_LABEL.search(head)


def _parse_debate_positions(entries: list) -> list[DebatePosition]:
    positions: list[DebatePosition] = []
    for idx, entry in enumerate(entries):
        if not entry or not isinstance(entry, str):
            continue
        if ":" in entry:
            name, desc = entry.split(":", 1)
        else:
            name, desc = entry, ""
        name = name.strip()
        desc = desc.strip()
        if not name:
            continue

        base_name = re.split(r"\s*\(", name)[0].strip()
        pos_id = _slug(base_name) or f"position_{idx}"
        keywords = set(_tokenize(name))
        keywords.update(_tokenize(desc))

        for alias_group in re.findall(r"\(([^)]+)\)", name):
            for alias in re.split(r"[/,]", alias_group):
                keywords.update(_tokenize(alias.strip()))

        phrase_pattern = _phrase_regex(base_name)
        keyword_pattern = _keyword_regex(keywords)

        positions.append(
            DebatePosition(
                id=pos_id,
                label=name,
                keywords=keywords,
                phrase_pattern=phrase_pattern,
                keyword_pattern=keyword_pattern,
            )
        )
    return positions


class StanceGuard:
    """Case-specific stance detector built from the ingest case profile."""

    def __init__(self, case_profile: dict | None = None):
        profile = case_profile or {}
        self.positions = _parse_debate_positions(profile.get("debate_positions", []))
        self._position_by_id = {p.id: p for p in self.positions}
        primary = [p for p in self.positions if _is_primary_position(p.label)]
        self._stance_positions = primary if len(primary) >= 2 else self.positions

        topic_keywords = set()
        for field_name in ("subfields", "central_questions", "key_entities"):
            for item in profile.get(field_name, []):
                if isinstance(item, str):
                    topic_keywords.update(_tokenize(item))
        for pos in self.positions:
            topic_keywords.update(pos.keywords)
        self._topic_pattern = _keyword_regex(topic_keywords)

        self._than_winners: list[tuple[re.Pattern, str]] = []
        self._against_handlers: list[tuple[re.Pattern, str]] = []
        for pos in self._stance_positions:
            target = pos.phrase_pattern or pos.keyword_pattern
            if not target:
                continue
            target_pat = target.pattern
            self._than_winners.append(
                (
                    re.compile(
                        r"\b(?:more\s+likely|prefer\w*|favor\w*|find\w*|concluded)\b"
                        r"[^.!?]{0,140}(?:"
                        + target_pat
                        + r")[^.!?]{0,80}\bthan\b",
                        re.I,
                    ),
                    pos.id,
                )
            )
            self._than_winners.append(
                (
                    re.compile(
                        r"(?:"
                        + target_pat
                        + r")[^.!?]{0,80}\b(?:is|are|was|were)\s+"
                        r"(?:more\s+likely|prefer\w*|favor\w*)\b"
                        r"[^.!?]{0,80}\bthan\b",
                        re.I,
                    ),
                    pos.id,
                )
            )
            self._against_handlers.append(
                (
                    re.compile(
                        r"\b(?:"
                        r"evidence\s+against|"
                        r"(?:factor(?:s)?\s+)?(?:weighing\s+)?against|"
                        r"weighs?\s+against|"
                        r"undermines?"
                        r")\s+(?:the\s+)?(?:"
                        + target_pat
                        + r")",
                        re.I,
                    ),
                    pos.id,
                )
            )

    @property
    def active(self) -> bool:
        return len(self._stance_positions) >= 2

    def _other_position(self, pos_id: str) -> str | None:
        others = [p.id for p in self._stance_positions if p.id != pos_id]
        if len(others) == 1:
            return others[0]
        return None

    def _score_positions(self, text: str) -> dict[str, int]:
        scores: dict[str, int] = {p.id: 0 for p in self._stance_positions}
        for pos in self._stance_positions:
            if pos.phrase_pattern and pos.phrase_pattern.search(text):
                scores[pos.id] += 3
            if pos.keyword_pattern:
                scores[pos.id] += len(pos.keyword_pattern.findall(text))
        return scores

    def detect_stance(self, text: str) -> str | None:
        if not text or not self.active:
            return None

        for pattern, pos_id in self._than_winners:
            if pattern.search(text):
                return pos_id

        for pattern, opposed_id in self._against_handlers:
            if pattern.search(text):
                scores = self._score_positions(text)
                scores[opposed_id] = 0
                ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
                if ranked and ranked[0][1] > 0:
                    return ranked[0][0]
                other = self._other_position(opposed_id)
                if other:
                    return other

        scores = self._score_positions(text)
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        if len(ranked) < 2:
            return ranked[0][0] if ranked and ranked[0][1] > 0 else None

        best_id, best_score = ranked[0]
        second_score = ranked[1][1]
        if best_score >= 2 and best_score > second_score:
            return best_id

        if best_score > 0 and second_score > 0:
            for pos in self._stance_positions:
                target = pos.phrase_pattern or pos.keyword_pattern
                if not target:
                    continue
                if re.search(
                    rf"\b(?:concluded|conclusion|finding|winner)\b[^.]{{0,80}}{target.pattern}",
                    text,
                    re.I,
                ):
                    return pos.id
        return None

    def attestation_conflicts_claim(self, quote: str, claim_content: str) -> bool:
        """True when quote asserts the opposite debate stance to the claim."""
        claim_stance = self.detect_stance(claim_content)
        quote_stance = self.detect_stance(quote)
        if not claim_stance or not quote_stance:
            return False
        return claim_stance != quote_stance

    def attestation_lacks_debate_support(self, quote: str, claim_content: str) -> bool:
        """
        True when claim engages the case debate but quote never touches it
        (e.g. transcript preamble about 'how we work').
        """
        if not self._topic_pattern or not self._topic_pattern.search(claim_content):
            return False
        if self.attestation_conflicts_claim(quote, claim_content):
            return False
        return not self._topic_pattern.search(quote)

    def pair_merge_stance_conflict(self, a: dict, b: dict) -> bool:
        """True if merging two nodes would mix opposing debate stances."""
        sa = self.detect_stance(a.get("content", ""))
        sb = self.detect_stance(b.get("content", ""))
        if sa and sb and sa != sb:
            return True

        for qa, qb in (
            (a.get("textual_evidence") or a.get("quote_exact") or "", sb),
            (b.get("textual_evidence") or b.get("quote_exact") or "", sa),
        ):
            qst = self.detect_stance(qa)
            if qst and qb and qst != qb:
                return True
        return False


def load_stance_guard(case: str) -> StanceGuard:
    from episteme.profiles.case_profile import load_case_profile

    return StanceGuard(load_case_profile(case))


# Backward-compatible no-op when callers lack a case profile (safe default).
_NO_OP_GUARD = StanceGuard({})


def detect_stance(text: str) -> str | None:
    return _NO_OP_GUARD.detect_stance(text)


def attestation_conflicts_claim(quote: str, claim_content: str) -> bool:
    return _NO_OP_GUARD.attestation_conflicts_claim(quote, claim_content)


def attestation_lacks_origin_support(quote: str, claim_content: str) -> bool:
    return _NO_OP_GUARD.attestation_lacks_debate_support(quote, claim_content)


def pair_merge_stance_conflict(a: dict, b: dict) -> bool:
    return _NO_OP_GUARD.pair_merge_stance_conflict(a, b)
