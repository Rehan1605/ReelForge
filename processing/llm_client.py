import json
import re
from config import OPENROUTER_MODEL
from processing.openrouter_gateway import post_chat_completion


def _strip_fences(text: str) -> str:
    """
    Remove markdown code fences if the model wrapped the JSON output.
    Handles ```json ... ```, ``` ... ```, and surrounding whitespace.
    """
    text = text.strip()
    match = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    # Also check if a code block is embedded within surrounding text
    match_inner = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if match_inner:
        return match_inner.group(1).strip()
    return text


def _extract_top_level_json(text: str) -> str:
    """
    Extract a clearly identifiable top-level JSON object {...} or array [...]
    if the model surrounded it with conversational text or commentary.
    """
    text = text.strip()
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    first_bracket = text.find("[")
    last_bracket = text.rfind("]")

    # If both exist, choose the outermost or first starting valid construct
    has_obj = first_brace != -1 and last_brace != -1 and last_brace > first_brace
    has_arr = first_bracket != -1 and last_bracket != -1 and last_bracket > first_bracket

    if has_obj and has_arr:
        if first_brace < first_bracket and last_brace > last_bracket:
            return text[first_brace:last_brace + 1]
        elif first_bracket < first_brace and last_bracket > last_brace:
            return text[first_bracket:last_bracket + 1]
        elif first_brace < first_bracket:
            return text[first_brace:last_brace + 1]
        else:
            return text[first_bracket:last_bracket + 1]
    elif has_obj:
        return text[first_brace:last_brace + 1]
    elif has_arr:
        return text[first_bracket:last_bracket + 1]

    return text


def _safe_recover_json_text(text: str) -> str:
    """
    Perform safe, deterministic JSON recovery:
    1. Strip outer code fences.
    2. Extract top-level JSON object/array if surrounded by prose.
    3. Remove trailing commas before closing } or ].
    """
    cleaned = _strip_fences(text)
    extracted = _extract_top_level_json(cleaned)
    # Remove trailing commas before } or ]
    no_trailing_commas = re.sub(r",\s*([\]}])", r"\1", extracted)
    return no_trailing_commas.strip()


def generate_text(
    prompt: str,
    temperature: float = 0.1,
    max_tokens: int | None = None,
    timeout: int = 60,
) -> str:
    """
    Send a text prompt to the fixed OpenRouter free-model route.
    """
    payload: dict = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
        "temperature": temperature,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    response = post_chat_completion(payload, timeout)
    resp_text = response.text.strip()
    raw_message = ""

    # Support SSE streaming data chunks returned by compatible gateways.
    if resp_text.startswith("data:"):
        delta_contents = []
        for line in resp_text.splitlines():
            line_str = line.strip()
            if line_str.startswith("data:"):
                chunk_payload = line_str[5:].strip()
                if chunk_payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(chunk_payload)
                    choices = chunk.get("choices", [])
                    if choices:
                        content = choices[0].get("delta", {}).get("content", "")
                        if content:
                            delta_contents.append(content)
                except json.JSONDecodeError:
                    pass
        raw_message = "".join(delta_contents)
    else:
        try:
            data = response.json()
            choices = data.get("choices", [])
            if not choices:
                raise ValueError("OpenRouter response contains no choices")
            raw_message = choices[0].get("message", {}).get("content", "")
        except Exception as exc:
            raise ValueError("OpenRouter response is not valid JSON") from exc

    if not raw_message:
        raise ValueError(
            f"OpenRouter returned empty message content for the fixed model '{OPENROUTER_MODEL}'"
        )

    return raw_message.strip()


def generate_json(
    prompt: str,
    temperature: float = 0.1,
    max_tokens: int | None = None,
    timeout: int = 60,
) -> dict:
    """
    Send a prompt to OpenRouter requesting JSON, safely strip markdown fences,
    apply deterministic JSON recovery, and return the parsed Python dict.
    """
    raw_text = generate_text(
        prompt=prompt,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )

    # First attempt direct parse after fence stripping
    direct_cleaned = _strip_fences(raw_text)
    try:
        parsed = json.loads(direct_cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # Apply safe deterministic recovery (top-level extraction + trailing comma removal)
    recovered_text = _safe_recover_json_text(raw_text)
    try:
        parsed = json.loads(recovered_text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"OpenRouter returned malformed JSON. JSON error: {exc}"
        ) from exc

    if not isinstance(parsed, dict):
        raise ValueError(
            f"Expected JSON dictionary from OpenRouter, got {type(parsed).__name__}"
        )

    return parsed
