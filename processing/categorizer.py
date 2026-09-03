from pathlib import Path

from config import CATEGORIES
from processing.llm_client import generate_text

# Build a lookup map for fast case-insensitive normalization.
# Keys are lowercase versions of each valid category name.
# Values are the canonical Title-Case strings that the dispatcher expects.
_CATEGORY_LOOKUP = {c.lower(): c for c in CATEGORIES}


def _normalize_category(raw: str) -> str:
    """
    Given the raw string returned by the model, return the canonical
    category name that matches config.CATEGORIES exactly.

    Strategy:
    1. Strip whitespace and trailing punctuation.
    2. Try an exact match first (model returned the correct string).
    3. Try a case-insensitive match (model returned wrong case).
    4. Try stripping a leading "Answer:" prefix the model occasionally adds.
    5. If nothing matches, return "Other" as a safe fallback rather than
       crashing the pipeline with a ValueError.
    """
    candidate = raw.strip().rstrip(".,;:")

    # Exact match
    if candidate in CATEGORIES:
        return candidate

    # Case-insensitive match
    lower = candidate.lower()
    if lower in _CATEGORY_LOOKUP:
        return _CATEGORY_LOOKUP[lower]

    # Model may have returned "Answer: Food" or "Category: Food"
    for prefix in ("answer:", "category:", "answer :", "category :"):
        if lower.startswith(prefix):
            inner = candidate[len(prefix):].strip().rstrip(".,;:")
            if inner in CATEGORIES:
                return inner
            inner_lower = inner.lower()
            if inner_lower in _CATEGORY_LOOKUP:
                return _CATEGORY_LOOKUP[inner_lower]

    # Model returned something unrecognised — fall back to Other
    print(f"Warning: categorizer returned unrecognised value {repr(raw)!s}. Defaulting to 'Other'.")
    return "Other"


def categorize(caption, transcript):
    prompt_path = Path("prompts") / "categorizer.txt"

    prompt = prompt_path.read_text(encoding="utf-8")

    categories = "\n".join(f"- {category}" for category in CATEGORIES)

    prompt = prompt.replace("{caption}", caption or "").replace("{transcript}", transcript or "")
    if "{categories}" in prompt:
        prompt = prompt.replace("{categories}", categories)

    raw_category = generate_text(prompt)
    category = _normalize_category(raw_category)

    return category
