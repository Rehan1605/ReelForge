"""
processing/llm_client.py
------------------------
Centralized OpenAI-compatible text generation client routing through OmniRoute.
Provides unified text and structured JSON generation for all ReelForge modules.
"""

from __future__ import annotations

import json
import re
import requests

from config import OMNIROUTE_API_KEY, OMNIROUTE_BASE_URL, TEXT_MODEL


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


def generate_text(
    prompt: str,
    model: str | None = None,
    temperature: float = 0.1,
    max_tokens: int | None = None,
    timeout: int = 60,
) -> str:
    """
    Send a text prompt to the OmniRoute /chat/completions endpoint and return
    the assistant's response string.

    Parameters
    ----------
    prompt : str
        The input prompt to send to the model.
    model : str | None
        Model identifier. If None, defaults to config.TEXT_MODEL.
    temperature : float
        Sampling temperature (default 0.1 for deterministic extraction).
    max_tokens : int | None
        Optional maximum token limit.
    timeout : int
        Request timeout in seconds (default 60).

    Returns
    -------
    str
        The accumulated assistant text response.

    Raises
    ------
    ConnectionError
        If OmniRoute gateway is unreachable.
    TimeoutError
        If the request times out.
    RuntimeError
        If the gateway returns an HTTP error or unexpected status.
    ValueError
        If the response is empty or malformed.
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

    try:
        response = requests.post(
            endpoint,
            json=payload,
            headers=headers,
            timeout=timeout,
        )
    except requests.exceptions.ConnectionError as exc:
        raise ConnectionError(
            f"Failed to connect to OmniRoute gateway at '{endpoint}'. "
            f"Is the local OmniRoute server running? Try: omniroute serve"
        ) from exc
    except requests.exceptions.Timeout as exc:
        raise TimeoutError(
            f"Request to OmniRoute gateway at '{endpoint}' timed out after {timeout} seconds. "
            f"Model: '{target_model}'"
        ) from exc
    except requests.RequestException as exc:
        raise RuntimeError(
            f"Network error communicating with OmniRoute gateway at '{endpoint}': {exc}"
        ) from exc

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


def generate_json(
    prompt: str,
    model: str | None = None,
    temperature: float = 0.1,
    max_tokens: int | None = None,
    timeout: int = 60,
) -> dict:
    """
    Send a prompt to OmniRoute requesting JSON, strip markdown fences, and
    return the parsed Python dict.

    Parameters
    ----------
    prompt : str
        The input prompt to send to the model.
    model : str | None
        Model identifier. If None, defaults to config.TEXT_MODEL.
    temperature : float
        Sampling temperature (default 0.1).
    max_tokens : int | None
        Optional maximum token limit.
    timeout : int
        Request timeout in seconds (default 60).

    Returns
    -------
    dict
        Parsed JSON dictionary.

    Raises
    ------
    ValueError
        If the model output cannot be decoded into a valid JSON dictionary.
    """
    raw_text = generate_text(
        prompt=prompt,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )

    cleaned_text = _strip_fences(raw_text)

    try:
        parsed = json.loads(cleaned_text)
    except json.JSONDecodeError as exc:
        snippet = cleaned_text[:300] + ("..." if len(cleaned_text) > 300 else "")
        raise ValueError(
            f"Model '{model or TEXT_MODEL}' returned malformed JSON.\n"
            f"JSON error: {exc}\n"
            f"Cleaned output (first 300 chars):\n{snippet}"
        ) from exc

    if not isinstance(parsed, dict):
        raise ValueError(
            f"Expected JSON dictionary from model '{model or TEXT_MODEL}', "
            f"got {type(parsed).__name__}: {parsed}"
        )

    return parsed
