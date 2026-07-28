from processing.ai_extractor import extract_ai_knowledge
from processing.finance_extractor import extract_finance_knowledge
from processing.food_extractor import extract_food_knowledge
from processing.gym_extractor import extract_gym_knowledge
from processing.movies_edits_extractor import extract_movies_edits_knowledge
from processing.photography_extractor import extract_photography_knowledge
from processing.programming_extractor import extract_programming_knowledge
from processing.productivity_extractor import extract_productivity_knowledge
from processing.travel_extractor import extract_travel_knowledge


def dispatch(category, caption, transcript):
    normalized_category = category.strip()

    if normalized_category == "Programming":
        print("Using extractor: Programming")
        return extract_programming_knowledge(caption, transcript)

    if normalized_category == "AI":
        print("Using extractor: AI")
        return extract_ai_knowledge(caption, transcript)

    if normalized_category == "Food":
        print("Using extractor: Food")
        return extract_food_knowledge(caption, transcript)

    if normalized_category == "Photography":
        print("Using extractor: Photography")
        return extract_photography_knowledge(caption, transcript)

    if normalized_category == "Gym":
        print("Using extractor: Gym")
        return extract_gym_knowledge(caption, transcript)

    if normalized_category == "Movies & Edits":
        print("Using extractor: Movies & Edits")
        return extract_movies_edits_knowledge(caption, transcript)

    if normalized_category == "Travel":
        print("Using extractor: Travel")
        return extract_travel_knowledge(caption, transcript)

    if normalized_category == "Finance":
        print("Using extractor: Finance")
        return extract_finance_knowledge(caption, transcript)

    if normalized_category == "Productivity":
        print("Using extractor: Productivity")
        return extract_productivity_knowledge(caption, transcript)

    if normalized_category == "Other":
        print("Using extractor: None")
        return {}

    raise ValueError(f"Unknown category: {category}")
