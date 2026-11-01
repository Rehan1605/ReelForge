"""Fixed, shared OpenRouter chat-completions transport for text and vision."""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import time

import requests

from config import (
    OPENROUTER_BASE_URL,
    OPENROUTER_MODEL,
    require_openrouter_api_key,
)

CHAT_COMPLETIONS_ENDPOINT = f"{OPENROUTER_BASE_URL}/chat/completions"
MAX_TRANSIENT_RETRIES = 1
MAX_RATE_LIMIT_RETRY_AFTER_SECONDS = 3.0
TRANSIENT_STATUS_CODES = {500, 502, 503, 504}


def validate_assistant_content(
    content,
    finish_reason,
    *,
    modality: str,
) -> str:
    """Validate assistant text without ever consulting reasoning fields."""
    if finish_reason == "length":
        raise ValueError(
            f"OpenRouter returned an incomplete {modality} response (finish_reason=length)"
        )
    if not isinstance(content, str):
        raise ValueError(
            f"OpenRouter returned invalid {modality} message content; expected a non-empty string"
        )
    content = content.strip()
    if not content:
        raise ValueError(
            f"OpenRouter returned empty {modality} message content"
        )
    return content


def extract_assistant_content(data, *, modality: str) -> str:
    """Extract validated assistant content from a chat-completion response."""
    if not isinstance(data, dict):
        raise ValueError(f"OpenRouter {modality} response has an invalid structure")
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ValueError(f"OpenRouter {modality} response has no assistant choice")

    choice = choices[0]
    message = choice.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return validate_assistant_content(
        content,
        choice.get("finish_reason"),
        modality=modality,
    )


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value)
        return seconds if seconds >= 0 else None
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def post_chat_completion(payload: dict, timeout: int):
    """Post only the fixed model to the fixed endpoint with bounded retries.

    A single short retry is allowed for transient transport/5xx failures. A
    429 is retried only once when the server provides a short Retry-After value.
    No response body or credential is included in raised errors.
    """
    if payload.get("model") != OPENROUTER_MODEL:
        raise ValueError("AI requests must use the fixed OpenRouter free model")
    if any(key in payload for key in ("models", "provider", "route")):
        raise ValueError("Alternate model/provider routing is disabled")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {require_openrouter_api_key()}",
    }

    for attempt in range(MAX_TRANSIENT_RETRIES + 1):
        try:
            response = requests.post(
                CHAT_COMPLETIONS_ENDPOINT,
                json=payload,
                headers=headers,
                timeout=timeout,
                allow_redirects=False,
            )
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            if attempt < MAX_TRANSIENT_RETRIES:
                time.sleep(1.0)
                continue
            if isinstance(exc, requests.exceptions.Timeout):
                raise TimeoutError("OpenRouter request timed out after one bounded retry") from None
            raise ConnectionError("Could not connect to the OpenRouter API after one bounded retry") from None
        except requests.RequestException:
            raise RuntimeError("OpenRouter request failed due to a network error") from None

        if response.status_code == 429:
            retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
            if (
                attempt == 0
                and retry_after is not None
                and retry_after <= MAX_RATE_LIMIT_RETRY_AFTER_SECONDS
            ):
                time.sleep(retry_after)
                continue
            raise RuntimeError(
                "OpenRouter rate limit (HTTP 429); request stopped without a model/provider fallback. Retry later."
            )

        if response.status_code in TRANSIENT_STATUS_CODES:
            if attempt < MAX_TRANSIENT_RETRIES:
                time.sleep(1.0)
                continue
            raise RuntimeError(
                f"OpenRouter returned transient HTTP {response.status_code} after one bounded retry"
            )

        if response.status_code != 200:
            raise RuntimeError(
                f"OpenRouter request failed with HTTP {response.status_code}; no model/provider fallback was attempted"
            )

        return response

    raise RuntimeError("OpenRouter request failed after bounded retries")
