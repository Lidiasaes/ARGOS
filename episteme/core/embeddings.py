"""Embedding utilities — CPU, $0, shared across dedup/clustering/filters."""

_st_model = None


def _get_model():
    global _st_model
    if _st_model is None:
        try:
            from sentence_transformers import SentenceTransformer
            print("  [LOAD] Loading embedding model (first run - cached after)...")
            _st_model = SentenceTransformer("all-MiniLM-L6-v2")
        except ImportError:
            _st_model = "unavailable"
    return None if _st_model == "unavailable" else _st_model


def embed(text: str):
    model = _get_model()
    if model is None:
        return None
    return model.encode(text, normalize_embeddings=True)


def embed_many(texts, batch_size: int = 64):
    """Batch-encode a list of texts to normalized vectors. Returns None if the model
    is unavailable. Shape: (len(texts), dim)."""
    model = _get_model()
    if model is None:
        return None
    return model.encode(list(texts), batch_size=batch_size, normalize_embeddings=True)


def cosine_sim(a, b) -> float:
    return float(__import__("numpy").dot(a, b))
