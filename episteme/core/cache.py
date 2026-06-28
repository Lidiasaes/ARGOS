import json
import hashlib
from pathlib import Path

from episteme.config import CACHE_DIR, CACHE_VERSION


def content_hash(payload) -> str:
    """Deterministic short hash of arbitrary JSON-serializable content, used
    to make cache keys sensitive to what's actually being sent to the model,
    not just a static label like a case name, batch index, or section number."""
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()[:12]


class Cache:
    LEVELS = ["raw", "chunks", "nodes", "agent", "trust", "doc_summary", "profiles", "polarity"]

    def __init__(self, case: str, reset: bool = False):
        self.case = case
        self.reset = reset
        self.base = CACHE_DIR / case
        for level in self.LEVELS:
            (self.base / level).mkdir(parents=True, exist_ok=True)

    def _path(self, level: str, identifier: str) -> Path:
        h = hashlib.sha256(f"{identifier}::{CACHE_VERSION}".encode()).hexdigest()[:16]
        return self.base / level / f"{h}.json"

    def get_or_run(self, level: str, identifier: str, fn):
        path = self._path(level, identifier)
        if not self.reset and path.exists():
            cached = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(cached, dict) and cached.get("refused"):
                path.unlink()
            elif isinstance(cached, str) and not cached.strip():
                path.unlink()
            else:
                return cached
        result = fn()
        if isinstance(result, dict) and result.get("parse_error") and not result.get("refused"):
            return result
        if isinstance(result, str) and not result.strip():
            return result
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result

    def delete(self, level: str, identifier: str):
        path = self._path(level, identifier)
        if path.exists():
            path.unlink()

    def invalidate(self, level: str):
        for f in (self.base / level).glob("*.json"):
            f.unlink()
        print(f"  Cache '{level}' invalidated for case '{self.case}'")

    def stats(self) -> dict:
        return {level: len(list((self.base / level).glob("*.json"))) for level in self.LEVELS}
