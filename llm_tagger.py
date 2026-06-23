import os
import re
from typing import Literal

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, field_validator
import unicodedata

load_dotenv()

MODEL = os.getenv("LLM_MODEL", "gpt-5.4-mini")


CuisineTag = Literal[
    "african",
    "bakery",
    "bistro",
    "brasserie",
    "cafe",
    "chinese",
    "cocktail_bar",
    "coffee_shop",
    "fine_dining",
    "french",
    "italian",
    "japanese",
    "korean",
    "lebanese",
    "mediterranean",
    "mexican",
    "north_african",
    "pizza",
    "ramen",
    "seafood",
    "sushi",
    "thai",
    "vegetarian",
    "vietnamese",
    "wine_bar",
]

VibeTag = Literal[
    "casual",
    "classic",
    "cozy",
    "date_night",
    "festive",
    "modern",
    "neighborhood",
    "trendy",
    "upscale",
]

FeatureTag = Literal[
    "bar_seating",
    "counter_seating",
    "terrace",
    "outdoor_seating",
    "rooftop",
    "brunch",
    "breakfast",
    "lunch",
    "late_night",
    "natural_wine",
    "cocktails",
    "good_for_groups",
    "good_for_solo",
    "kid_friendly",
    "dog_friendly",
    "romantic",
    "business_meal",
    "private_room",
    "reservation_recommended",
    "scene",
]

TriState = Literal["TRUE", "FALSE", "UNKNOWN"]
Confidence = Literal["high", "medium", "low"]
BooleanText = Literal["TRUE", "FALSE"]


CUISINE_ORDER = [
    "african",
    "bakery",
    "bistro",
    "brasserie",
    "cafe",
    "chinese",
    "cocktail_bar",
    "coffee_shop",
    "fine_dining",
    "french",
    "italian",
    "japanese",
    "korean",
    "lebanese",
    "mediterranean",
    "mexican",
    "north_african",
    "pizza",
    "ramen",
    "seafood",
    "sushi",
    "thai",
    "vegetarian",
    "vietnamese",
    "wine_bar",
]

VIBE_ORDER = [
    "casual",
    "classic",
    "cozy",
    "date_night",
    "festive",
    "modern",
    "neighborhood",
    "trendy",
    "upscale",
]

FEATURE_ORDER = [
    "bar_seating",
    "counter_seating",
    "terrace",
    "outdoor_seating",
    "rooftop",
    "brunch",
    "breakfast",
    "lunch",
    "late_night",
    "natural_wine",
    "cocktails",
    "good_for_groups",
    "good_for_solo",
    "kid_friendly",
    "dog_friendly",
    "romantic",
    "business_meal",
    "private_room",
    "reservation_recommended",
    "scene",
]


class RestaurantTaggingResult(BaseModel):
    cuisine: list[CuisineTag]
    vibe: list[VibeTag]
    features: list[FeatureTag]
    delivery: TriState
    takeaway: TriState
    llm_confidence: Confidence
    llm_evidence: str
    delivery_takeaway_evidence: str
    llm_review_needed: BooleanText

    @field_validator("cuisine")
    @classmethod
    def max_three_cuisine(cls, value):
        return value[:3]

    @field_validator("vibe")
    @classmethod
    def max_three_vibe(cls, value):
        return value[:3]

    @field_validator("features")
    @classmethod
    def max_five_features(cls, value):
        return value[:5]


def ordered_unique(values: list[str], allowed_order: list[str]) -> list[str]:
    cleaned = []
    for item in values:
        if item not in cleaned:
            cleaned.append(item)

    return [item for item in allowed_order if item in cleaned]


def tags_to_cell(values: list[str], allowed_order: list[str]) -> str:
    return ", ".join(ordered_unique(values, allowed_order))


def normalize_evidence_text(text: str) -> str:
    text = text or ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.lower()
    text = text.replace("’", "'")
    text = re.sub(r"[^a-z0-9']+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def evidence_has_any(text: str, markers: list[str]) -> bool:
    normalized = normalize_evidence_text(text)
    return any(marker in normalized for marker in markers)


def apply_conservative_cuisine_filters(result: RestaurantTaggingResult, row: dict) -> RestaurantTaggingResult:
    """
    Hard safety filters for cuisine tags that the model may infer from location,
    language, or weak influence wording.

    For `french`, require explicit cuisine/menu evidence. Do not allow it just
    because the restaurant is in Paris or the website is written in French.
    """
    evidence_text = " ".join(
        [
            str(row.get("_website_text", "") or ""),
            str(row.get("Notes", "") or ""),
        ]
    )

    removed_cuisines = []

    french_cuisine_markers = [
        "cuisine francaise",
        "cuisine française",
        "french cuisine",
        "produits francais",
        "produits français",
        "specialites francaises",
        "spécialités françaises",
        "classiques francais",
        "classiques français",
        "bistrot francais",
        "bistrot français",
        "bistro francais",
        "bistro français",
        "brasserie francaise",
        "brasserie française",
        "gastronomie francaise",
        "gastronomie française",
        "terroir francais",
        "terroir français",
        "plats francais",
        "plats français",
    ]

    weak_french_markers = [
        "influences francaises",
        "influences françaises",
        "influence francaise",
        "influence française",
    ]

    if "french" in result.cuisine:
        has_strong_french_evidence = evidence_has_any(evidence_text, french_cuisine_markers)
        has_only_weak_french_evidence = evidence_has_any(evidence_text, weak_french_markers)

        if not has_strong_french_evidence or has_only_weak_french_evidence:
            result.cuisine = [tag for tag in result.cuisine if tag != "french"]
            removed_cuisines.append("french")

    if removed_cuisines:
        result.llm_evidence = (
            result.llm_evidence
            + " Conservative filter removed unsupported cuisine tag(s): "
            + ", ".join(removed_cuisines)
            + "."
        )

    return result


def apply_conservative_feature_filters(result: RestaurantTaggingResult, row: dict) -> RestaurantTaggingResult:
    """
    Hard safety filters for features that the model tends to over-infer.

    These prevent:
    - bar_seating from being inferred just because a place says "Bar & Restaurant";
    - reservation_recommended from being inferred from a normal "Réserver" button.
    - business_meal from being inferred from central/refined positioning alone.
    """
    evidence_text = " ".join(
        [
            str(row.get("_website_text", "") or ""),
            str(row.get("Notes", "") or ""),
        ]
    )

    removed_features = []

    bar_seating_markers = [
        "bar seating",
        "counter seating",
        "assis au bar",
        "place au bar",
        "places au bar",
        "manger au bar",
        "au comptoir",
        "comptoir",
    ]

    reservation_recommended_markers = [
        "reservation recommandee",
        "reservation conseillee",
        "reservation obligatoire",
        "uniquement sur reservation",
        "sur reservation uniquement",
        "sur reservation",
        "booking required",
        "reservation required",
        "reservation recommended",
        "booking recommended",
        "by reservation only",
    ]

    business_meal_markers = [
        "business meal",
        "business lunch",
        "business dinner",
        "repas d'affaires",
        "dejeuner d'affaires",
        "diner d'affaires",
        "restaurant d'affaires",
        "repas professionnel",
        "dejeuner professionnel",
        "diner professionnel",
        "seminaire",
        "seminaires",
        "corporate",
        "entreprise",
        "entreprises",
    ]

    if "bar_seating" in result.features and not evidence_has_any(evidence_text, bar_seating_markers):
        result.features = [feature for feature in result.features if feature != "bar_seating"]
        removed_features.append("bar_seating")

    if (
        "reservation_recommended" in result.features
        and not evidence_has_any(evidence_text, reservation_recommended_markers)
    ):
        result.features = [feature for feature in result.features if feature != "reservation_recommended"]
        removed_features.append("reservation_recommended")

    if "business_meal" in result.features and not evidence_has_any(evidence_text, business_meal_markers):
        result.features = [feature for feature in result.features if feature != "business_meal"]
        removed_features.append("business_meal")

    if removed_features:
        result.llm_evidence = (
            result.llm_evidence
            + " Conservative filter removed unsupported feature(s): "
            + ", ".join(removed_features)
            + "."
        )

    return result


def build_restaurant_prompt(row: dict) -> str:
    website_text = row.get("_website_text", "")

    return f"""
Restaurant:
Name: {row.get("Name", "")}
Address: {row.get("Address", "")}
City: {row.get("City", "")}
Postal Code: {row.get("Postal Code", "")}
Arrondissement: {row.get("Arrondissement", "")}
Town: {row.get("Town", "")}
Website: {row.get("Website", "")}
Instagram: {row.get("Instagram", "")}
Facebook: {row.get("Facebook", "")}
Notes: {row.get("Notes", "")}

Official Website Text:
{website_text}

Existing Cuisine: {row.get("Cuisine", "")}
Existing Vibe: {row.get("Vibe", "")}
Existing Features: {row.get("Features", "")}
Existing Delivery: {row.get("Delivery", "")}
Existing Takeaway: {row.get("Takeaway", "")}

Important rules:
- Use only provided information.
- Do not browse.
- Treat Official Website Text as the strongest evidence source when present.
- Be careful when website text mentions sister restaurants or other addresses.
- Prefer tags that match the restaurant name and address being tagged.
- Do not infer delivery or takeaway from cuisine or restaurant type.
- Use Delivery = UNKNOWN and Takeaway = UNKNOWN unless explicit evidence is present in the input.
- Use fewer tags when evidence is weak.
- Leave tag arrays empty if there is not enough evidence.
"""


def tag_restaurant(row: dict) -> RestaurantTaggingResult:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is missing from .env")

    client = OpenAI()

    completion = client.chat.completions.parse(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You tag restaurants for a private Paris restaurant database. "
                    "You must return only structured data matching the schema. "
                    "Use only allowed vocabulary values. "
                    "Never invent new tags. "
                    "Never invent delivery or takeaway evidence. "
                    "Use UNKNOWN unless explicit positive or negative evidence is present. "
                    "Set llm_review_needed to TRUE when evidence is weak, conflicting, or uncertain."
                ),
            },
            {
                "role": "user",
                "content": build_restaurant_prompt(row),
            },
        ],
        response_format=RestaurantTaggingResult,
    )

    result = completion.choices[0].message.parsed

    result.cuisine = ordered_unique(result.cuisine, CUISINE_ORDER)
    result.vibe = ordered_unique(result.vibe, VIBE_ORDER)
    result.features = ordered_unique(result.features, FEATURE_ORDER)

    result = apply_conservative_cuisine_filters(result, row)
    result = apply_conservative_feature_filters(result, row)

    result.cuisine = ordered_unique(result.cuisine, CUISINE_ORDER)
    result.vibe = ordered_unique(result.vibe, VIBE_ORDER)
    result.features = ordered_unique(result.features, FEATURE_ORDER)

    return result


def result_to_sheet_values(result: RestaurantTaggingResult) -> dict:
    return {
        "Cuisine": tags_to_cell(result.cuisine, CUISINE_ORDER),
        "Vibe": tags_to_cell(result.vibe, VIBE_ORDER),
        "Features": tags_to_cell(result.features, FEATURE_ORDER),
        "Delivery": result.delivery,
        "Takeaway": result.takeaway,
        "LLM Confidence": result.llm_confidence,
        "LLM Evidence": result.llm_evidence,
        "LLM Model": MODEL,
        "LLM Review Needed": result.llm_review_needed,
    }
