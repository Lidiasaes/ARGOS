import json
import uuid
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from episteme.config import CASES_DIR
from episteme.core.embeddings import embed, cosine_sim
from episteme.core.node_schema import coerce_score


@dataclass
class EpistemicNode:
    id: str
    type: str
    content: str
    source_url: str
    source_author: str
    source_date: str
    source_type: str
    claim_type: str
    argument_level: str
    abstraction_level: str
    confidence: float
    agent_generated: bool
    human_reviewed: bool
    needs_review: bool
    status: str
    relations: list
    presuppositions: list
    conditions: list
    limitations: list
    genericity_flag: bool
    case: str
    created_at: str
    quote_exact: Optional[str] = None
    evidential_weight: float | None = None
    independence_score: float | None = None
    # v4:  automated specificity (no manual ontology)
    subfield: str = ""
    specificity_score: float = 1.0
    key_question: Optional[str] = None
    textual_evidence: Optional[str] = None
    source_id: str = ""
    source_role: str = ""
    counterargument: Optional[str] = None
    is_rhetorical_move: bool = False
    # Whether textual_evidence/supporting_quote was verified to appear literally
    # in the source chunk at ingest time. False means the claim was kept (not
    # dropped) despite a failed quote-gate check — see episteme/pipeline/ingest.py.
    quote_grounded: bool = True
    # Multi-source grounding — one entry per paper/source that supports this claim
    attestations: list = field(default_factory=list)
    # Reconcile pass — cross-paper convergence metrics
    support_count: int = 0
    contradict_count: int = 0
    epistemic_status: str = ""


def make_attestation(
    source_id: str,
    *,
    author: str = "",
    date: str = "",
    source_url: str = "",
    quote: str | None = None,
    confidence: float = 0.5,
    source_type: str = "unknown",
) -> dict:
    return {
        "source_id": source_id,
        "author": author,
        "date": date,
        "source_url": source_url,
        "quote": quote,
        "confidence": confidence,
        "source_type": source_type,
    }


def ensure_attestations(node: dict) -> list[dict]:
    """Backfill attestations from legacy single-source fields."""
    existing = node.get("attestations")
    if isinstance(existing, list) and existing:
        return existing
    if node.get("source_id") or node.get("textual_evidence") or node.get("quote_exact"):
        return [
            make_attestation(
                node.get("source_id", ""),
                author=node.get("source_author", ""),
                date=node.get("source_date", ""),
                source_url=node.get("source_url", ""),
                quote=node.get("textual_evidence") or node.get("quote_exact"),
                confidence=node.get("confidence", 0.5),
                source_type=node.get("source_type", "unknown"),
            )
        ]
    return []


def _attestation_key(att: dict) -> tuple:
    return (att.get("source_id", ""), (att.get("quote") or "").strip())


def attestation_source_key(att: dict) -> str:
    """Stable key for grouping attestations from the same paper/source."""
    sid = (att.get("source_id") or "").strip()
    if sid:
        return sid
    return (att.get("author") or "").strip()


def unique_attestation_source_count(atts: list[dict]) -> int:
    return len({attestation_source_key(a) for a in atts if attestation_source_key(a)})


def group_attestations_by_source(atts: list[dict]) -> list[dict]:
    """Collapse multiple quotes from the same source into one display group."""
    groups: dict[str, dict] = {}
    order: list[str] = []
    for att in atts:
        key = attestation_source_key(att)
        if not key:
            key = f"__anon_{len(order)}"
        if key not in groups:
            groups[key] = {
                "source_id": att.get("source_id", ""),
                "author": att.get("author") or att.get("source_id", ""),
                "source_url": att.get("source_url", ""),
                "quotes": [],
                "count": 0,
            }
            order.append(key)
        quote = (att.get("quote") or "").strip()
        if quote and quote not in groups[key]["quotes"]:
            groups[key]["quotes"].append(quote)
        groups[key]["count"] += 1
    return [groups[k] for k in order]


class GraphStore:
    def __init__(self, case: str):
        self.case = case
        self.path = CASES_DIR / case / "graph.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._nodes: dict[str, dict] = {}
        self._emb_cache: dict[str, object] = {}
        if self.path.exists():
            self._nodes = json.loads(self.path.read_text(encoding="utf-8"))
            self._normalize_all_attestations()

    def add_node(self, node: EpistemicNode) -> str:
        from dataclasses import asdict
        self._nodes[node.id] = asdict(node)
        self._save()
        return node.id

    def get_node(self, node_id: str) -> dict | None:
        node = self._nodes.get(node_id)
        if node is not None and not node.get("attestations"):
            atts = ensure_attestations(node)
            if atts:
                node["attestations"] = atts
        return node

    def append_attestation(self, node_id: str, attestation: dict) -> bool:
        """
        Add a source-specific quote to an existing node (dedup merge).
        Returns True if a new attestation was added.
        """
        if node_id not in self._nodes:
            return False
        node = self._nodes[node_id]
        if not node.get("attestations"):
            node["attestations"] = ensure_attestations(node)

        key = _attestation_key(attestation)
        for existing in node["attestations"]:
            if _attestation_key(existing) == key:
                return False

        node["attestations"].append(attestation)
        conf = coerce_score(node.get("confidence"), 0.5)
        node["confidence"] = min(conf + 0.1, 0.95)
        if len(node["attestations"]) > 1:
            node["status"] = "corroborated"
        self._save()
        return True

    def get_all_nodes(self, filters: dict = None) -> list[dict]:
        nodes = list(self._nodes.values())
        if not filters:
            return nodes
        for key, value in filters.items():
            nodes = [n for n in nodes if n.get(key) == value]
        return nodes

    def get_nodes_by_type(self, node_type: str) -> list[dict]:
        return self.get_all_nodes({"type": node_type})

    def update_node(self, node_id: str, updates: dict):
        if node_id in self._nodes:
            self._nodes[node_id].update(updates)
            self._save()

    def remove_node(self, node_id: str) -> bool:
        if node_id not in self._nodes:
            return False
        del self._nodes[node_id]
        self._emb_cache.pop(node_id, None)
        self._save()
        return True

    @staticmethod
    def _dedupe_relations(relations: list) -> list:
        seen: set[tuple] = set()
        unique = []
        for rel in relations:
            key = (rel.get("type"), rel.get("target"))
            if key in seen:
                continue
            seen.add(key)
            unique.append(rel)
        return unique

    def merge_nodes(self, canonical_id: str, absorbed_id: str) -> str:
        """
        Merge absorbed node into canonical: union attestations, merge relations,
        redirect inbound edges, delete absorbed.
        """
        if canonical_id not in self._nodes or absorbed_id not in self._nodes:
            return canonical_id
        if canonical_id == absorbed_id:
            return canonical_id

        canon = self._nodes[canonical_id]
        absorbed = self._nodes[absorbed_id]

        if not canon.get("attestations"):
            canon["attestations"] = ensure_attestations(canon)
        for att in ensure_attestations(absorbed):
            key = _attestation_key(att)
            if not any(_attestation_key(e) == key for e in canon["attestations"]):
                canon["attestations"].append(att)

        existing = {(r.get("type"), r.get("target")) for r in canon.get("relations", [])}
        for rel in absorbed.get("relations", []):
            tgt = rel.get("target")
            if tgt in (canonical_id, absorbed_id):
                continue
            key = (rel.get("type"), tgt)
            if key not in existing:
                canon.setdefault("relations", []).append(dict(rel))
                existing.add(key)

        for node in self._nodes.values():
            for rel in node.get("relations", []):
                if rel.get("target") == absorbed_id:
                    rel["target"] = canonical_id
            node["relations"] = self._dedupe_relations(node.get("relations", []))

        canon["confidence"] = min(max(canon.get("confidence", 0.5), absorbed.get("confidence", 0.5)) + 0.05, 0.95)
        if len(canon.get("attestations", [])) > 1:
            canon["status"] = "corroborated"

        self.remove_node(absorbed_id)
        return canonical_id

    def add_relation(
        self,
        source_id: str,
        target_id: str,
        relation_type: str,
        strength: float = 0.5,
        rationale: str = "",
        source: str = "",
    ):
        if source_id in self._nodes:
            rel = {"type": relation_type, "target": target_id, "strength": strength}
            if rationale:
                rel["rationale"] = rationale
            if source:
                rel["source"] = source
            self._nodes[source_id]["relations"].append(rel)
            self._save()

    def get_neighbors(self, node_id: str, relation_type: str = None) -> list[dict]:
        node = self._nodes.get(node_id)
        if not node:
            return []
        relations = node.get("relations", [])
        if relation_type:
            relations = [r for r in relations if r["type"] == relation_type]
        return [self._nodes[r["target"]] for r in relations if r["target"] in self._nodes]

    def node_exists_similar(self, content: str, threshold: float = 0.85) -> str | None:
        if not self._nodes:
            return None
        new_emb = embed(content)
        if new_emb is None:
            for nid, node in self._nodes.items():
                if node["content"].strip() == content.strip():
                    return nid
            return None
        best_sim, best_id = 0.0, None
        for nid, node in self._nodes.items():
            if nid not in self._emb_cache:
                self._emb_cache[nid] = embed(node["content"])
            if self._emb_cache[nid] is None:
                continue
            sim = cosine_sim(new_emb, self._emb_cache[nid])
            if sim > best_sim:
                best_sim, best_id = sim, nid
        return best_id if best_sim >= threshold else None

    def stats(self) -> dict:
        from collections import Counter
        types = Counter(n["type"] for n in self._nodes.values())
        return {"total": len(self._nodes), "by_type": dict(types)}

    def _normalize_all_attestations(self):
        changed = False
        for node in self._nodes.values():
            if not node.get("attestations"):
                atts = ensure_attestations(node)
                if atts:
                    node["attestations"] = atts
                    changed = True
        if changed:
            self._save()

    def _save(self):
        self.path.write_text(
            json.dumps(self._nodes, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def make_node(type: str, content: str, source_url: str = "", **kwargs) -> EpistemicNode:
    from datetime import datetime, timezone
    defaults = {
        "id": str(uuid.uuid4())[:8],
        "source_author": "",
        "source_date": "",
        "source_type": "unknown",
        "claim_type": "unknown",
        "argument_level": "direct",
        "abstraction_level": "empirical",
        "confidence": 0.5,
        "evidential_weight": None,
        "agent_generated": False,
        "human_reviewed": False,
        "needs_review": False,
        "status": "unexamined",
        "relations": [],
        "presuppositions": [],
        "conditions": [],
        "limitations": [],
        "genericity_flag": False,
        "independence_score": None,
        "case": "",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "quote_exact": None,
        "subfield": "",
        "specificity_score": 1.0,
        "key_question": None,
        "textual_evidence": None,
        "source_id": "",
        "source_role": "",
        "counterargument": None,
        "is_rhetorical_move": False,
        "quote_grounded": True,
        "attestations": [],
        "support_count": 0,
        "contradict_count": 0,
        "epistemic_status": "",
    }
    valid_fields = {f.name for f in EpistemicNode.__dataclass_fields__.values()}
    merged = {**defaults, **kwargs}
    merged["confidence"] = coerce_score(merged.get("confidence"), 0.5)
    if merged.get("evidential_weight") is not None:
        merged["evidential_weight"] = coerce_score(merged.get("evidential_weight"), 0.5)
    filtered = {k: v for k, v in merged.items() if k in valid_fields}
    if not filtered.get("attestations"):
        quote = filtered.get("textual_evidence") or filtered.get("quote_exact")
        sid = filtered.get("source_id", "")
        if sid or quote:
            filtered["attestations"] = [
                make_attestation(
                    sid,
                    author=filtered.get("source_author", ""),
                    date=filtered.get("source_date", ""),
                    source_url=source_url,
                    quote=quote,
                    confidence=filtered.get("confidence", 0.5),
                    source_type=filtered.get("source_type", "unknown"),
                )
            ]
    return EpistemicNode(type=type, content=content, source_url=source_url, **filtered)
