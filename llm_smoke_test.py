import os
from typing import Literal

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel


load_dotenv()

MODEL = os.getenv("LLM_MODEL", "gpt-5.4-mini")

if not os.getenv("OPENAI_API_KEY"):
    raise RuntimeError("OPENAI_API_KEY is missing from .env")


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


client = OpenAI()

completion = client.chat.completions.parse(
    model=MODEL,
    messages=[
        {
            "role": "system",
            "content": (
                "You tag restaurants for a private Paris restaurant database. "
                "Use only the allowed schema values. "
                "Do not invent delivery or takeaway evidence. "
                "Use UNKNOWN unless the provided input explicitly proves TRUE or FALSE. "
                "Prefer fewer tags when evidence is weak."
            ),
        },
        {
            "role": "user",
            "content": """
Restaurant:
Name: Gloria Osteria Paris
Address: 41 Rue de Lille, 75007 Paris, France
Website: https://gloria-osteria.com/fr/gloria-osteria-paris
Notes: Italian trattoria. Pasta, pizza, lively decor, social dining.
No explicit delivery or takeaway evidence is provided in this test input.

Return structured tags only.
""",
        },
    ],
    response_format=RestaurantTaggingResult,
)

result = completion.choices[0].message.parsed

print("Model:", MODEL)
print(result.model_dump_json(indent=2))