import json
import re
import time
import requests

from config import OMNIROUTE_API_KEY, OMNIROUTE_BASE_URL, TEXT_MODEL

MAX_RETRIES = 3
TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}


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
    model: str | None = None,
    temperature: float = 0.1,
    max_tokens: int | None = None,
    timeout: int = 60,
) -> str:
    """
    Send a text prompt to the OmniRoute /chat/completions endpoint with
    lightweight retries for transient errors (429, 5xx, timeouts, connection errors).
    """
    target_model = model or TEXT_MODEL
    target_base_url = OMNIROUTE_BASE_URL.rstrip("/")
    endpoint = f"{target_base_url}/chat/completions"

    headers = {
        "Content-Type": "application/json",
    }
    if OMNIROUTE_API_KEY:
        headers["Authorization"] = f"Bearer {OMNIROUTE_API_KEY}"

    payload: dict = {
        "model": target_model,
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

    last_error: Exception | None = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            response = requests.post(
                endpoint,
                json=payload,
                headers=headers,
                timeout=timeout,
            )
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                delay = min(1.0 * (2 ** attempt), 10.0)
                time.sleep(delay)
                continue
            if isinstance(exc, requests.exceptions.ConnectionError):
                raise ConnectionError(
                    f"Failed to connect to OmniRoute gateway at '{endpoint}' after {MAX_RETRIES + 1} attempts."
                ) from exc
            else:
                raise TimeoutError(
                    f"Request to OmniRoute gateway at '{endpoint}' timed out after {MAX_RETRIES + 1} attempts."
                ) from exc
        except requests.RequestException as exc:
            raise RuntimeError(
                f"Network error communicating with OmniRoute gateway at '{endpoint}': {exc}"
            ) from exc

        if response.status_code in TRANSIENT_STATUS_CODES:
            last_error = RuntimeError(
                f"OmniRoute gateway returned transient HTTP {response.status_code}: {response.text}"
            )
            if attempt < MAX_RETRIES:
                # Respect Retry-After if provided
                retry_after = response.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    delay = min(float(retry_after), 10.0)
                else:
                    delay = min(1.0 * (2 ** attempt), 10.0)
                time.sleep(delay)
                continue
            raise last_error

        if response.status_code != 200:
            raise RuntimeError(
                f"OmniRoute gateway returned HTTP {response.status_code}: {response.text}"
            )

        resp_text = response.text.strip()
        raw_message = ""

        # Support SSE streaming data chunks (e.g. data: {"choices": [{"delta": {"content": "..."}}]})
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
                            delta = choices[0].get("delta", {})
                            content = delta.get("content", "")
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
                    raise ValueError(f"OmniRoute response contains no 'choices': {data}")
                message = choices[0].get("message", {})
                raw_message = message.get("content", "")
            except Exception as exc:
                raise ValueError(
                    f"OmniRoute gateway response is not valid JSON: {response.text}"
                ) from exc

        if not raw_message:
            raise ValueError(
                f"OmniRoute returned empty message content for model '{target_model}'. "
                f"Full response payload: {response.text[:500]}"
            )

        return raw_message.strip()

    if last_error:
        raise last_error
    raise RuntimeError(f"Failed to get response from OmniRoute gateway at '{endpoint}'")


def generate_json(
    prompt: str,
    model: str | None = None,
    temperature: float = 0.1,
    max_tokens: int | None = None,
    timeout: int = 60,
) -> dict:
    """
    Send a prompt to OmniRoute requesting JSON, safely strip markdown fences,
    apply deterministic JSON recovery, and return the parsed Python dict.
    """
    raw_text = generate_text(
        prompt=prompt,
        model=model,
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
        snippet = raw_text[:300] + ("..." if len(raw_text) > 300 else "")
        raise ValueError(
            f"Model '{model or TEXT_MODEL}' returned malformed JSON.\n"
            f"JSON error: {exc}\n"
            f"Raw output (first 300 chars):\n{snippet}"
        ) from exc

    if not isinstance(parsed, dict):
        raise ValueError(
            f"Expected JSON dictionary from model '{model or TEXT_MODEL}', "
            f"got {type(parsed).__name__}: {parsed}"
        )

    return parsed
