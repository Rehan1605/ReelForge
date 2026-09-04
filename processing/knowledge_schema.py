"""
processing/knowledge_schema.py
--------------------------------
Centralized schema validation and normalization for all category extractor outputs.
Ensures every Brain Object knowledge block is structurally sound, type-safe,
and adheres to its category-specific schema.
"""

from __future__ import annotations

from typing import Any

# Maps every supported category to its expected schema fields and types.
CATEGORY_SCHEMAS: dict[str, dict[str, type]] = {
    "Programming": {
        "title": str,
        "summary": str,
        "key_takeaways": list,
        "main_topic": str,
        "difficulty": str,
        "key_concepts": list,
        "resources": list,
        "tools": list,
        "code_snippets": list,
        "best_practices": list,
        "mistakes_to_avoid": list,
        "action_items": list,
        "tags": list,
    },
    "AI": {
        "title": str,
        "summary": str,
        "models": list,
        "tools": list,
        "websites": list,
        "prompts": list,
        "concepts": list,
        "use_cases": list,
        "key_points": list,
        "tips": list,
        "tags": list,
    },
    "Food": {
        "title": str,
        "summary": str,
        "dishes": list,
        "ingredients": list,
        "steps": list,
        "cookware": list,
        "cuisine": list,
        "measurements": list,
        "tips": list,
        "websites": list,
        "tags": list,
    },
    "Photography": {
        "title": str,
        "summary": str,
        "gear": list,
        "camera_settings": list,
        "editing_tools": list,
        "techniques": list,
        "locations": list,
        "lighting": list,
        "tips": list,
        "websites": list,
        "tags": list,
    },
    "Gym": {
        "title": str,
        "summary": str,
        "exercises": list,
        "muscles": list,
        "equipment": list,
        "sets_reps": list,
        "form_cues": list,
        "workout_type": list,
        "tips": list,
        "websites": list,
        "tags": list,
    },
    "Movies & Edits": {
        "title": str,
        "summary": str,
        "movies": list,
        "shows": list,
        "songs": list,
        "editing_apps": list,
        "transitions": list,
        "effects": list,
        "steps": list,
        "templates": list,
        "tips": list,
        "websites": list,
        "tags": list,
    },
    "Travel": {
        "title": str,
        "summary": str,
        "destinations": list,
        "hotels": list,
        "attractions": list,
        "restaurants": list,
        "transport": list,
        "best_time": list,
        "budget_tips": list,
        "tips": list,
        "websites": list,
        "tags": list,
    },
    "Finance": {
        "title": str,
        "summary": str,
        "concepts": list,
        "stocks": list,
        "funds": list,
        "apps": list,
        "key_points": list,
        "numbers": list,
        "tips": list,
        "risks": list,
        "websites": list,
        "tags": list,
    },
    "Productivity": {
        "title": str,
        "summary": str,
        "methods": list,
        "apps": list,
        "templates": list,
        "shortcuts": list,
        "workflows": list,
        "habits": list,
        "tips": list,
        "websites": list,
        "tags": list,
    },
    "Other": {
        "title": str,
        "summary": str,
        "key_points": list,
        "steps": list,
        "recommendations": list,
        "websites": list,
        "tags": list,
    },
}


def _normalize_list_item(item: Any) -> Any:
    if isinstance(item, dict):
        return {str(k): str(v).strip() for k, v in item.items() if v is not None}
    return str(item).strip()


def _normalize_list_field(field_name: str, val: Any) -> list:
    if val is None:
        return []

    if isinstance(val, list):
        cleaned = []
        for item in val:
            if item is None:
                continue
            normalized_item = _normalize_list_item(item)
            if normalized_item:
                cleaned.append(normalized_item)
        return cleaned

    if isinstance(val, str):
        val_str = val.strip()
        if not val_str:
            return []
        if "," in val_str:
            return [part.strip() for part in val_str.split(",") if part.strip()]
        return [val_str]

    if isinstance(val, (set, tuple)):
        return [str(item).strip() for item in val if item is not None and str(item).strip()]

    # Single scalar (e.g. number) converted to single-element list
    scalar_str = str(val).strip()
    return [scalar_str] if scalar_str else []


def _normalize_str_field(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, str):
        return val.strip()
    return str(val).strip()


def normalize_knowledge_schema(category: str, raw_data: Any) -> dict:
    """
    Validate and normalize raw extractor output against the schema for `category`.

    Parameters
    ----------
    category : str
        The canonical category name (e.g. 'Programming', 'AI', 'Food', etc.).
    raw_data : Any
        The parsed JSON dictionary returned by the LLM.

    Returns
    -------
    dict
        A fully validated, type-safe knowledge dictionary.

    Raises
    ------
    ValueError
        If raw_data is not a dictionary, category is unknown, or required fields
        are missing or malformed.
    """
    if not isinstance(raw_data, dict):
        raise ValueError(
            f"Knowledge data for '{category}' must be a dictionary, got {type(raw_data).__name__}: {raw_data}"
        )

    schema = CATEGORY_SCHEMAS.get(category)
    if schema is None:
        raise ValueError(
            f"Unknown category for schema validation: '{category}'. "
            f"Valid categories: {list(CATEGORY_SCHEMAS.keys())}"
        )

    # Required field check: 'summary' must be present in raw_data
    if "summary" not in raw_data:
        raise ValueError(
            f"Knowledge data for '{category}' is missing required field 'summary'."
        )

    normalized: dict = {}

    for field_name, expected_type in schema.items():
        val = raw_data.get(field_name)

        if expected_type is list:
            normalized[field_name] = _normalize_list_field(field_name, val)
        elif expected_type is str:
            normalized[field_name] = _normalize_str_field(val)
        else:
            normalized[field_name] = val if val is not None else ""

    # Preserve any extra structured keys the model returned
    for k, v in raw_data.items():
        if k not in normalized:
            normalized[k] = v

    return normalized
