import re


def sanitize_page_title(title: str, max_length: int = 100) -> str:
    """
    Sanitize a OneNote page title to prevent Microsoft Graph error 20153
    ("The name value contains invalid characters.") while keeping the title
    readable and informative.

    Transformations:
    1. Replace colons and slashes with ' - '.
    2. Replace ampersands with 'and'.
    3. Remove forbidden characters: ? * < > | # ' " % ~ and control characters.
    4. Collapse multiple spaces and dashes.
    5. Strip leading/trailing whitespace and punctuation.
    6. Truncate to max_length safely at word boundaries.
    """
    if not title:
        return "InstaBrain Reel"

    text = str(title).strip()

    # Replace colons and slashes with a readable separator
    text = re.sub(r"[\:\/\\\|]+", " - ", text)

    # Replace ampersands for clean title display
    text = text.replace("&", "and")

    # Remove characters forbidden by OneNote / Microsoft Graph
    # Forbidden: ? * < > # ' " % ~ ` ^
    text = re.sub(r'[\?\*\<\>\#\'\"\%~`\^]', '', text)

    # Remove non-printable / control characters
    text = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", text)

    # Collapse multiple dashes and spaces
    text = re.sub(r"\s*-\s*-\s*", " - ", text)
    text = re.sub(r"\s+", " ", text).strip()

    # Strip leading/trailing punctuation like dashes or dots
    text = text.strip(" -.,;:")

    if not text:
        return "InstaBrain Reel"

    # Truncate to max_length cleanly at word boundary if possible
    if len(text) > max_length:
        truncated = text[:max_length]
        last_space = truncated.rfind(" ")
        if last_space > max_length // 2:
            text = truncated[:last_space].rstrip(" -.,;:")
        else:
            text = truncated.rstrip(" -.,;:")

    return text or "InstaBrain Reel"
