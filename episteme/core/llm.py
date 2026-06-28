import json
import time
import anthropic
from dotenv import load_dotenv
from episteme.config import MODEL_SMART

load_dotenv()

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client

# TODO: v.2.0.0 - Costs are hardcoded for now --> get from API
# TODO: v.2.0.1 - Scalability: get from LITELLM, fastLLM provider for supporting multiple models.

COST = {
    "input": {"haiku": 0.25, "sonnet": 3.0},
    "output": {"haiku": 1.25, "sonnet": 15.0},
}

total_cost_session = 0.0
cost_by_label: dict[str, float] = {}


def _extract_text(response) -> str | None:
    if not response.content:
        return None
    for block in response.content:
        if hasattr(block, "text") and block.text:
            return block.text
    return None


def call_llm(
    prompt: str,
    model: str = MODEL_SMART,
    max_tokens: int = 1000,
    parse_json: bool = False,
    retries: int = 3,
    label: str = "other",
) -> dict | list | str:
    global total_cost_session
    tokens = max_tokens
    last_text = ""

    for attempt in range(retries):
        try:
            response = _get_client().messages.create(
                model=model,
                max_tokens=tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            text = _extract_text(response)

            if text is None:
                reason = response.stop_reason
                if reason == "refusal":
                    print(f"    [SKIP] [{label}] Refused by API - chunk skipped (cached, won't retry)")
                    if parse_json:
                        return {"parse_error": True, "refused": True, "nodes": []}
                    return ""
                print(f"    [WARN] [{label}] Empty response (stop_reason={reason}), retry {attempt + 1}/{retries}...")
                if reason == "max_tokens":
                    tokens = min(tokens * 2, 8000)
                time.sleep(2 ** attempt)
                continue

            _log_cost(model, response.usage, label)
            last_text = text

            if parse_json:
                result = _parse_json(text)
                if (
                    isinstance(result, dict)
                    and result.get("parse_error")
                    and response.stop_reason == "max_tokens"
                ):
                    print(f"    [WARN] [{label}] JSON truncated, retry {attempt + 1}/{retries}...")
                    tokens = min(tokens * 2, 8000)
                    time.sleep(2 ** attempt)
                    continue
                return result

            if response.stop_reason == "max_tokens":
                print(f"    [WARN] [{label}] Text truncated, retry {attempt + 1}/{retries}...")
                tokens = min(tokens * 2, 8000)
                time.sleep(2 ** attempt)
                continue

            return text

        except anthropic.RateLimitError:
            time.sleep(2 ** attempt)
        except Exception as e:
            print(f"    [WARN] [{label}] Error ({type(e).__name__}: {e}), retry {attempt + 1}/{retries}...")
            time.sleep(2 ** attempt)

    print(f"    [FAIL] [{label}] Failed after {retries} retries, skipping.")
    if parse_json:
        return {"raw_response": last_text, "parse_error": True, "nodes": []}
    return last_text


def _parse_json(text: str):
    """Parse JSON; may return dict, list, or error dict."""
    try:
        clean = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(clean)
    except json.JSONDecodeError:
        return {"raw_response": text, "parse_error": True}


def _log_cost(model: str, usage, label: str = "other"):
    global total_cost_session, cost_by_label
    tier = "haiku" if "haiku" in model else "sonnet"
    cost = (usage.input_tokens * COST["input"][tier] + usage.output_tokens * COST["output"][tier]) / 1_000_000
    total_cost_session += cost
    cost_by_label[label] = cost_by_label.get(label, 0.0) + cost
    print(f"    [COST] [{label}] {tier}: {usage.input_tokens}in / {usage.output_tokens}out - ${cost:.4f} (session: ${total_cost_session:.3f})")


def print_budget_report():
    if not cost_by_label:
        return
    print("\n" + "-" * 52)
    print("  SESSION BUDGET REPORT")
    print("-" * 52)
    for label, cost in sorted(cost_by_label.items(), key=lambda x: -x[1]):
        print(f"  {label:<22} ${cost:.4f}")
    print("-" * 52)
    print(f"  {'TOTAL':<22} ${total_cost_session:.4f}")
    print("-" * 52 + "\n")


def reset_budget():
    global total_cost_session, cost_by_label
    total_cost_session = 0.0
    cost_by_label = {}
