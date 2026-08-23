from processing.ai_extractor import extract_ai_knowledge
from processing.finance_extractor import extract_finance_knowledge
from processing.food_extractor import extract_food_knowledge
from processing.gym_extractor import extract_gym_knowledge
from processing.movies_edits_extractor import extract_movies_edits_knowledge
from processing.other_extractor import extract_other_knowledge
from processing.photography_extractor import extract_photography_knowledge
from processing.programming_extractor import extract_programming_knowledge
from processing.productivity_extractor import extract_productivity_knowledge
from processing.travel_extractor import extract_travel_knowledge

# Maps every valid category name to its extractor function.
# Category strings must match config.CATEGORIES exactly (Title Case).
_EXTRACTORS = {
    "Programming": extract_programming_knowledge,
    "AI": extract_ai_knowledge,
    "Food": extract_food_knowledge,
    "Photography": extract_photography_knowledge,
    "Gym": extract_gym_knowledge,
    "Movies & Edits": extract_movies_edits_knowledge,
    "Travel": extract_travel_knowledge,
    "Finance": extract_finance_knowledge,
    "Productivity": extract_productivity_knowledge,
    "Other": extract_other_knowledge,
}


def dispatch(category, caption, transcript):
    normalized_category = category.strip()

    extractor = _EXTRACTORS.get(normalized_category)

    if extractor is None:
        raise ValueError(
            f"Unknown category: {repr(category)}. "
            f"Valid categories: {list(_EXTRACTORS.keys())}"
        )

    print(f"Using extractor: {normalized_category}")
    return extractor(caption, transcript)
