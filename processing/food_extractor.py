from pathlib import Path

from processing.knowledge_schema import normalize_knowledge_schema
from processing.llm_client import generate_json
from processing.vision_analyzer import format_vision_analysis


def extract_food_knowledge(caption, transcript, vision_analysis=None):
    prompt_path = Path("prompts") / "food_extractor.txt"

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
        raw_json = generate_json(prompt)
        return normalize_knowledge_schema("Food", raw_json)
    except ValueError as e:
        raise ValueError(f"Food extractor returned invalid output: {e}") from e
