from pathlib import Path

from processing.llm_client import generate_json
from processing.vision_analyzer import format_vision_analysis


def extract_other_knowledge(caption, transcript, vision_analysis=None):
    prompt_path = Path("prompts") / "other_extractor.txt"

    prompt = prompt_path.read_text(encoding="utf-8")

    formatted_vision = format_vision_analysis(vision_analysis)

    prompt = (
        prompt.replace("{{", "{")
        .replace("}}", "}")
        .replace("{caption}", caption or "")
        .replace("{transcript}", transcript or "")
        .replace("{vision_analysis}", formatted_vision)
    )

    try:
        return generate_json(prompt)
    except ValueError as e:
        raise ValueError("Other extractor returned invalid JSON.") from e
