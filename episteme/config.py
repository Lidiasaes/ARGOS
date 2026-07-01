from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CASES_DIR = BASE_DIR / "cases"
CACHE_DIR = BASE_DIR / "cache"
REPORTS_DIR = BASE_DIR / "reports"

VALID_CASES = ["covid", "covid_small", "covid_demo", "lhc", "eggs", "fertility"]

# Models
MODEL_FAST = "claude-haiku-4-5"
MODEL_SMART = "claude-sonnet-4-5"
MAX_TOKENS_FAST = 1000
MAX_TOKENS_SMART = 2000

# Chunking tokens — separate from MAX_TOKENS_FAST because chunker
# output is always a short JSON array (max 8 offsets ≈ 30 tokens).
# 100 gives headroom without wasting budget.
CHUNK_MAX_OUTPUT_TOKENS = 100

# Chunking
CHUNK_MAX_CHARS = 4000

# Quote repair — max excerpt length when extending to sentence boundaries.
# Was 600 (hard-coded); caused mid-word clips on restored quotes (see docs/quote_length_limits.md).
QUOTE_REPAIR_MAX_CHARS = 4000

# Dedup / clustering
DEDUP_SIMILARITY_THRESHOLD = 0.88
CLUSTER_THRESHOLD = 0.75
CLAIM_CLUSTER_THRESHOLD = 0.75
MAX_THEMES = 40
MAX_RANKED_CLAIMS = 20

# Cross-paper reconcile (between ingest and crystallize)
RECONCILE_MIN_SIM = 0.65
RECONCILE_MAX_SIM = 0.84
RECONCILE_HAIKU_CEILING = 0.75  # pairs below this need Haiku verdict
RECONCILE_NODE_TYPES = ("claim", "evidence")
RECONCILE_CONFLICT_TYPES = frozenset({"contradicts", "undermines"})

# Floor below which a `contradicts` edge is treated as weak; to be calibrated
# from the observed strength distribution of contradicts edges.
CONTESTED_MIN_CONTRADICTION_STRENGTH: float = 0.55

# Contradiction detection — wider similarity window than merging.
# Opposing claims on the same question often embed at lower similarity
# than paraphrases (because polarity flips word distributions), so the
# contradiction sweep needs a lower floor than merge candidates.
RECONCILE_CONTRADICTION_MIN_SIM = 0.45
RECONCILE_CONTRADICTION_MAX_SIM = 0.84
RECONCILE_DETECT_CONTRADICTIONS = True

# Shared-question validation (anti coherence-hallucination guard).
# When Haiku returns "contradicts", it also invents a shared_question that
# supposedly connects both nodes. If that question is too abstract to
# overlap with either node's actual content, the contradiction is likely a
# fabricated connection — we downgrade it to "weak_contradicts" so it stays
# out of the high-confidence crux signal. Threshold = min cosine similarity
# between the shared_question and each node's content embedding.
RECONCILE_SHARED_QUESTION_MIN_OVERLAP = 0.5

# Proposition collapse (deterministic post-reconcile compile step)
# Cosine threshold for grouping shared_question strings into a single
# proposition. Short strings cluster tight; calibrate from the collapse
# ratio report.
PROPOSITION_CLUSTER_THRESHOLD: float = 0.78

# Crux alignment — strict dispute-mass coverage.
# The lax coverage metric counts a contested proposition as covered if it is any
# crux's best-match, even at Jaccard ~0.01 (a single shared claim). That can
# nominally "cover" the dominant dispute with a spurious match. The strict
# variant only counts a proposition as covered when its best-matching crux has
# Jaccard >= this floor, i.e. a meaningful claim-set overlap rather than an
# accidental one-claim brush.
MIN_MEANINGFUL_JACCARD: float = 0.1

# Structure
PHILOSOPHER_BATCH_SIZE = 6
MAX_PRESUPPOSITIONS_PER_BATCH = 3

# Extraction v4 — anti-generic gates
EXTRACTOR_VERSION = "v4.2"  # bump: attributed_to + section metadata
CHUNK_VERSION = "v2"        # bump: section-aware overlap chunking
POLARITY_CACHE_VERSION = "v1"
MIN_QUOTE_CHARS = 20
MIN_GROUNDING_RATIO = 0.45
MIN_ENTITY_SPECIFICITY = 0.15  # for direct / empirical claims
GENERICITY_EMBED_THRESHOLD = 0.90
REQUIRE_QUOTE_FOR = {"claim", "evidence"}

# Cache
CACHE_VERSION = "v2"
CONFIDENCE_AGENT_GENERATED = 0.3
CONFIDENCE_SOURCE = 0.7
CONFIDENCE_CORROBORATED = 0.9
SPECIFICITY_MISMATCH_THRESHOLD = 0.4

# Report
REPORT_SECTION_MAX_TOKENS = 4000
REPORT_SECTION_CONTINUATION_TOKENS = 2000
REPORT_SECTION_MAX_CONTINUATIONS = 2
REPORT_CACHE_VERSION = "v2"

# Legacy agent limits
MAX_AGENT_ITERATIONS = 5
MIN_NEW_NODES_TO_CONTINUE = 2
