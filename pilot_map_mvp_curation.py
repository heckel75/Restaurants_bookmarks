"""Read-only LLM-assisted curation pilot for one Paris arrondissement.

The script reads Google Sheets through the readiness audit's read-only client,
extracts text only from plausible official homepages with website_text.py,
profiles every eligible candidate with structured LLM output, and writes local
reports. It never updates Google Sheets or calls Google Places/search APIs.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, field_validator

from audit_map_mvp_readiness import cell, evaluate_rows, get_worksheet
from llm_tagger import (
    CUISINE_ORDER,
    FEATURE_ORDER,
    VIBE_ORDER,
    BooleanText,
    Confidence,
    CuisineTag,
    FeatureTag,
    RestaurantTaggingResult,
    VibeTag,
    apply_conservative_cuisine_filters,
    apply_conservative_feature_filters,
    ordered_unique,
)
from website_text import fetch_website_text, is_probably_valid_url


SCRIPT_DIR = Path(__file__).resolve().parent
TARGET_ARRONDISSEMENT = int(os.environ.get("TARGET_ARRONDISSEMENT", "19"))
MVP_TARGET_COUNT = int(os.environ.get("MVP_TARGET_COUNT", "15"))
EXPECTED_ELIGIBLE_COUNT = 23
EXPECTED_ALTERNATE_COUNT = 8
PROMPT_VERSION = "map-mvp-pilot-v1"

REPORT_DIR = (
    SCRIPT_DIR
    / "reports"
    / "map_mvp_curation"
    / f"pilot_arrondissement_{TARGET_ARRONDISSEMENT}"
)
CACHE_PATH = REPORT_DIR / "cache.json"
RUN_SUMMARY_PATH = REPORT_DIR / "run_summary.json"

EVIDENCE_FIELDS = (
    "Name",
    "Address",
    "Postal Code",
    "Website",
    "Instagram",
    "Cuisine",
    "Vibe",
    "Features",
    "Favorite",
    "Notes",
    "Google Place ID",
    "Latitude",
    "Longitude",
)

DISALLOWED_WEBSITE_HOSTS = (
    "deliveroo.",
    "doordash.",
    "facebook.com",
    "google.com",
    "instagram.com",
    "just-eat.",
    "linktr.ee",
    "restaurantguru.",
    "thefork.",
    "tripadvisor.",
    "ubereats.",
    "yelp.",
)

PROFILE_COLUMNS = (
    "Sheet Row Number",
    "Name",
    "Address",
    "Postal Code",
    "Website",
    "Instagram",
    "Existing Cuisine",
    "Existing Vibe",
    "Existing Features",
    "Favorite",
    "Notes",
    "Google Place ID",
    "Latitude",
    "Longitude",
    "Website Extraction Status",
    "Website Text Characters",
    "Cuisine",
    "Vibe",
    "Features",
    "Primary Cuisine",
    "Confidence",
    "Evidence",
    "Curation Note",
    "Review Needed",
    "Selection Group",
    "Rank",
    "Curation Rationale",
)

SHORTLIST_COLUMNS = (
    "Rank",
    "Sheet Row Number",
    "Name",
    "Primary Cuisine",
    "Cuisine",
    "Vibe",
    "Features",
    "Confidence",
    "Evidence",
    "Curation Rationale",
    "Website",
    "Address",
    "Google Place ID",
)


class PilotLLMProfile(BaseModel):
    cuisine: list[CuisineTag]
    vibe: list[VibeTag]
    features: list[FeatureTag]
    primary_cuisine: CuisineTag | None
    confidence: Confidence
    evidence: str
    curation_note: str
    review_needed: BooleanText

    @field_validator("cuisine")
    @classmethod
    def max_three_cuisine(cls, value: list[str]) -> list[str]:
        return value[:3]

    @field_validator("vibe")
    @classmethod
    def max_three_vibe(cls, value: list[str]) -> list[str]:
        return value[:3]

    @field_validator("features")
    @classmethod
    def max_five_features(cls, value: list[str]) -> list[str]:
        return value[:5]

    @field_validator("evidence", "curation_note")
    @classmethod
    def concise_text(cls, value: str) -> str:
        return re.sub(r"\s+", " ", value or "").strip()[:600]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as input_file:
            return json.load(input_file)
    except (json.JSONDecodeError, OSError):
        return default


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    with temporary_path.open("w", encoding="utf-8") as output_file:
        json.dump(value, output_file, ensure_ascii=False, indent=2, sort_keys=True)
        output_file.write("\n")
    temporary_path.replace(path)


def write_csv(path: Path, fieldnames: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(
            {field: row.get(field, "") for field in fieldnames} for row in rows
        )


def stable_hash(value: Any) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def load_model_configuration() -> str:
    load_dotenv(SCRIPT_DIR / ".env")
    model = os.environ.get("LLM_MODEL", "").strip()
    if not model:
        raise RuntimeError("LLM_MODEL is missing from the existing environment configuration.")
    return model


def validate_headers(values: list[list[str]]) -> dict[str, int]:
    if not values:
        raise RuntimeError("The configured Restaurants worksheet is empty.")
    headers = [str(value).strip() for value in values[0]]
    missing = [field for field in EVIDENCE_FIELDS if field not in headers]
    if missing:
        raise RuntimeError("Missing required evidence columns: " + ", ".join(missing))
    duplicates = [field for field in EVIDENCE_FIELDS if headers.count(field) > 1]
    if duplicates:
        raise RuntimeError("Duplicate evidence columns: " + ", ".join(duplicates))
    return {header: index for index, header in enumerate(headers)}


def load_candidates() -> tuple[list[dict[str, str]], int]:
    worksheet = get_worksheet()
    values = worksheet.get_all_values()
    column_indexes = validate_headers(values)
    _, readiness_candidates, _, _ = evaluate_rows(values)
    target_candidates = [
        candidate
        for candidate in readiness_candidates
        if int(candidate["Arrondissement"]) == TARGET_ARRONDISSEMENT
    ]
    if len(target_candidates) != EXPECTED_ELIGIBLE_COUNT:
        raise RuntimeError(
            f"Expected exactly {EXPECTED_ELIGIBLE_COUNT} eligible candidates in "
            f"arrondissement {TARGET_ARRONDISSEMENT}, found {len(target_candidates)}."
        )

    candidates: list[dict[str, str]] = []
    for readiness_candidate in target_candidates:
        row_number = int(readiness_candidate["Sheet Row Number"])
        row = values[row_number - 1]
        record = {
            "Sheet Row Number": str(row_number),
            "Arrondissement": readiness_candidate["Arrondissement"],
        }
        for field in EVIDENCE_FIELDS:
            record[field] = cell(row, column_indexes, field)
        candidates.append(record)

    candidates.sort(
        key=lambda candidate: (
            candidate["Name"].casefold(),
            int(candidate["Sheet Row Number"]),
        )
    )
    if len({candidate["Sheet Row Number"] for candidate in candidates}) != len(candidates):
        raise RuntimeError("Duplicate eligible sheet rows were found before profiling.")
    return candidates, 1


def allowed_existing_tags(value: str, allowed_order: list[str]) -> list[str]:
    raw_tags = [tag.strip() for tag in value.split(",") if tag.strip()]
    return ordered_unique(
        [tag for tag in raw_tags if tag in allowed_order],
        allowed_order,
    )


def website_is_disallowed(url: str) -> bool:
    try:
        hostname = (urlparse(url.strip()).hostname or "").casefold()
    except ValueError:
        return True
    return any(fragment in hostname for fragment in DISALLOWED_WEBSITE_HOSTS)


def website_cache_key(candidate: dict[str, str]) -> str:
    return stable_hash(
        {
            "url": candidate["Website"].strip(),
            "name": candidate["Name"],
            "address": candidate["Address"],
            "extractor": "website_text.fetch_website_text-v1",
        }
    )


def collect_website_evidence(
    candidate: dict[str, str],
    cache: dict[str, Any],
    counters: Counter[str],
) -> dict[str, Any]:
    url = candidate["Website"].strip()
    if not url or not is_probably_valid_url(url):
        return {
            "text": "",
            "status": "missing_or_invalid_url",
            "reason": "No valid official homepage URL is available.",
            "cache_hit": False,
        }
    if website_is_disallowed(url):
        return {
            "text": "",
            "status": "disallowed_or_unofficial_source",
            "reason": "Website field points to a disallowed or unofficial source.",
            "cache_hit": False,
        }

    key = website_cache_key(candidate)
    cached = cache["website_successes"].get(key)
    if cached and cached.get("text"):
        counters["website_cache_hits"] += 1
        return {
            "text": str(cached["text"]),
            "status": "cached_success",
            "reason": "",
            "cache_hit": True,
        }

    counters["website_http_calls"] += 1
    text = fetch_website_text(
        url,
        target_name=candidate["Name"],
        target_address=candidate["Address"],
    )
    if not text:
        counters["website_http_failures"] += 1
        return {
            "text": "",
            "status": "extraction_failed",
            "reason": "Homepage returned no usable HTML text or the request failed.",
            "cache_hit": False,
        }

    counters["website_http_successes"] += 1
    cache["website_successes"][key] = {
        "url": url,
        "name": candidate["Name"],
        "address": candidate["Address"],
        "text": text,
        "characters": len(text),
        "cached_at": now_iso(),
    }
    write_json(CACHE_PATH, cache)
    return {
        "text": text,
        "status": "success",
        "reason": "",
        "cache_hit": False,
    }


def build_profile_prompt(
    candidate: dict[str, str],
    website_evidence: dict[str, Any],
) -> str:
    existing_cuisine = allowed_existing_tags(candidate["Cuisine"], CUISINE_ORDER)
    existing_vibe = allowed_existing_tags(candidate["Vibe"], VIBE_ORDER)
    existing_features = allowed_existing_tags(candidate["Features"], FEATURE_ORDER)
    normalized_website_status = (
        "success" if website_evidence["text"] else website_evidence["status"]
    )
    return f"""
Profile this restaurant for a private Map MVP curation pilot using only the
provided sheet fields and official-homepage extraction. Do not browse.

Restaurant evidence:
- Name: {candidate['Name']}
- Address: {candidate['Address']}
- Postal Code: {candidate['Postal Code']}
- Website: {candidate['Website']}
- Instagram URL (reference only; do not scrape): {candidate['Instagram']}
- Favorite: {candidate['Favorite']}
- Notes: {candidate['Notes']}
- Existing valid Cuisine tags: {existing_cuisine}
- Existing valid Vibe tags: {existing_vibe}
- Existing valid Features: {existing_features}
- Website extraction status: {normalized_website_status}

Official homepage text:
{website_evidence['text']}

Approved Cuisine vocabulary:
{CUISINE_ORDER}

Approved Vibe vocabulary:
{VIBE_ORDER}

Approved Features vocabulary:
{FEATURE_ORDER}

Rules:
- Return only values from the approved vocabularies.
- Use at most 3 cuisine tags, 3 vibe tags, and 5 features.
- Treat valid existing tags as evidence, while resolving conflicts conservatively.
- Do not infer French cuisine from Paris, French-language text, or a French name.
- Do not infer a feature merely because a normal booking/contact link exists.
- Prefer explicit restaurant/menu descriptions from the official homepage.
- If evidence is weak or absent, return fewer tags or blank arrays, low confidence,
  and review_needed TRUE.
- primary_cuisine must be one returned cuisine tag, or null when cuisine is blank.
- Evidence must be concise and identify the actual supplied source.
- The curation note should explain usefulness/limitations for shortlist decisions,
  not claim facts unsupported by the supplied evidence.
""".strip()


def normalize_profile(
    result: PilotLLMProfile,
    candidate: dict[str, str],
    website_evidence: dict[str, Any],
) -> PilotLLMProfile:
    result.cuisine = ordered_unique(result.cuisine, CUISINE_ORDER)[:3]
    result.vibe = ordered_unique(result.vibe, VIBE_ORDER)[:3]
    result.features = ordered_unique(result.features, FEATURE_ORDER)[:5]

    filter_row = {
        "_website_text": website_evidence["text"],
        "Notes": candidate["Notes"],
    }
    tagging_result = RestaurantTaggingResult(
        cuisine=result.cuisine,
        vibe=result.vibe,
        features=result.features,
        delivery="UNKNOWN",
        takeaway="UNKNOWN",
        llm_confidence=result.confidence,
        llm_evidence=result.evidence,
        delivery_takeaway_evidence="Not evaluated in this pilot.",
        llm_review_needed=result.review_needed,
    )
    tagging_result = apply_conservative_cuisine_filters(tagging_result, filter_row)
    tagging_result = apply_conservative_feature_filters(tagging_result, filter_row)
    result.cuisine = ordered_unique(tagging_result.cuisine, CUISINE_ORDER)[:3]
    result.vibe = ordered_unique(tagging_result.vibe, VIBE_ORDER)[:3]
    result.features = ordered_unique(tagging_result.features, FEATURE_ORDER)[:5]
    result.evidence = tagging_result.llm_evidence[:600]

    existing_evidence = any(
        (
            allowed_existing_tags(candidate["Cuisine"], CUISINE_ORDER),
            allowed_existing_tags(candidate["Vibe"], VIBE_ORDER),
            allowed_existing_tags(candidate["Features"], FEATURE_ORDER),
        )
    ) or bool(candidate["Notes"].strip())
    homepage_evidence = bool(website_evidence["text"].strip())
    if not homepage_evidence and not existing_evidence:
        result.cuisine = []
        result.vibe = []
        result.features = []
        result.primary_cuisine = None
        result.confidence = "low"
        result.review_needed = "TRUE"
        result.evidence = "No usable official homepage text or existing structured evidence."
        result.curation_note = "Insufficient evidence for automated diversity curation."
    elif result.primary_cuisine not in result.cuisine:
        result.primary_cuisine = result.cuisine[0] if result.cuisine else None

    if not result.cuisine:
        result.primary_cuisine = None
    return result


def llm_cache_key(
    model: str,
    candidate: dict[str, str],
    website_evidence: dict[str, Any],
) -> str:
    return stable_hash(
        {
            "prompt_version": PROMPT_VERSION,
            "model": model,
            "candidate": {field: candidate[field] for field in EVIDENCE_FIELDS},
            "official_homepage_text": website_evidence["text"],
            "website_status": (
                "success" if website_evidence["text"] else website_evidence["status"]
            ),
        }
    )


def profile_candidate(
    client: OpenAI | None,
    model: str,
    candidate: dict[str, str],
    website_evidence: dict[str, Any],
    cache: dict[str, Any],
    counters: Counter[str],
) -> tuple[PilotLLMProfile, OpenAI | None]:
    key = llm_cache_key(model, candidate, website_evidence)
    cached = cache["llm_results"].get(key)
    if cached and cached.get("result"):
        try:
            result = PilotLLMProfile.model_validate(cached["result"])
            counters["llm_cache_hits"] += 1
            return result, client
        except Exception:
            pass

    if not os.environ.get("OPENAI_API_KEY", "").strip():
        raise RuntimeError("OPENAI_API_KEY is missing from the environment.")
    if client is None:
        client = OpenAI()

    counters["llm_api_calls"] += 1
    completion = client.chat.completions.parse(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You conservatively profile Paris restaurants for a private map. "
                    "Return only structured data matching the schema and controlled "
                    "vocabularies. Use only supplied evidence. Never browse, invent tags, "
                    "or infer French cuisine from location or language."
                ),
            },
            {"role": "user", "content": build_profile_prompt(candidate, website_evidence)},
        ],
        response_format=PilotLLMProfile,
    )
    parsed = completion.choices[0].message.parsed
    if parsed is None:
        refusal = completion.choices[0].message.refusal or "no parsed result"
        raise RuntimeError(f"LLM did not return a profile for {candidate['Name']}: {refusal}")

    result = normalize_profile(parsed, candidate, website_evidence)
    cache["llm_results"][key] = {
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "sheet_row_number": candidate["Sheet Row Number"],
        "name": candidate["Name"],
        "result": result.model_dump(mode="json"),
        "cached_at": now_iso(),
    }
    write_json(CACHE_PATH, cache)
    return result, client


def profile_record(
    candidate: dict[str, str],
    website_evidence: dict[str, Any],
    result: PilotLLMProfile,
) -> dict[str, Any]:
    return {
        **candidate,
        "Existing Cuisine": candidate["Cuisine"],
        "Existing Vibe": candidate["Vibe"],
        "Existing Features": candidate["Features"],
        "Website Extraction Status": website_evidence["status"],
        "Website Extraction Reason": website_evidence["reason"],
        "Website Text Characters": len(website_evidence["text"]),
        "Cuisine Tags": list(result.cuisine),
        "Vibe Tags": list(result.vibe),
        "Feature Tags": list(result.features),
        "Primary Cuisine": result.primary_cuisine or "",
        "Confidence": result.confidence,
        "Evidence": result.evidence,
        "Curation Note": result.curation_note,
        "Review Needed": result.review_needed,
    }


def favorite_true(profile: dict[str, Any]) -> bool:
    return str(profile["Favorite"]).strip().casefold() == "true"


def review_needed(profile: dict[str, Any]) -> bool:
    return str(profile["Review Needed"]).strip().upper() == "TRUE"


def quality_bucket(profile: dict[str, Any]) -> int:
    if favorite_true(profile):
        return 0
    confidence = profile["Confidence"]
    needs_review = review_needed(profile)
    if confidence == "high" and not needs_review:
        return 1
    if confidence == "medium" and not needs_review:
        return 2
    if confidence == "high":
        return 3
    if confidence == "medium":
        return 4
    if not needs_review:
        return 5
    return 6


def normalized_concept_name(name: str) -> str:
    text = unicodedata.normalize("NFKD", name)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.casefold().split(" - ", 1)[0]
    text = re.sub(r"\b(?:restaurant|paris|le|la|les)\b", " ", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def near_duplicate(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_name = normalized_concept_name(left["Name"])
    right_name = normalized_concept_name(right["Name"])
    if not left_name or not right_name:
        return False
    if left_name == right_name:
        return True
    return SequenceMatcher(None, left_name, right_name).ratio() >= 0.9


def diversity_key(
    candidate: dict[str, Any],
    selected: list[dict[str, Any]],
) -> tuple[Any, ...]:
    primary_counts = Counter(
        profile["Primary Cuisine"] for profile in selected if profile["Primary Cuisine"]
    )
    used_cuisines = {
        tag for profile in selected for tag in profile["Cuisine Tags"]
    }
    used_vibes = {tag for profile in selected for tag in profile["Vibe Tags"]}
    used_features = {
        tag for profile in selected for tag in profile["Feature Tags"]
    }
    primary = candidate["Primary Cuisine"]
    primary_count = primary_counts[primary] if primary else 0
    duplicate_penalty = any(near_duplicate(candidate, profile) for profile in selected)
    cap_penalty = bool(primary and primary_count >= 3)
    new_cuisines = len(set(candidate["Cuisine Tags"]) - used_cuisines)
    new_vibes = len(set(candidate["Vibe Tags"]) - used_vibes)
    new_features = len(set(candidate["Feature Tags"]) - used_features)
    return (
        duplicate_penalty,
        cap_penalty,
        not bool(primary),
        primary_count,
        -new_cuisines,
        -new_vibes,
        -new_features,
        candidate["Name"].casefold(),
        int(candidate["Sheet Row Number"]),
    )


def rank_profiles(
    profiles: list[dict[str, Any]],
    count: int,
    already_selected: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    selected = list(already_selected or [])
    ranked: list[dict[str, Any]] = []
    remaining = list(profiles)
    while remaining and len(ranked) < count:
        best_quality = min(quality_bucket(profile) for profile in remaining)
        quality_pool = [
            profile for profile in remaining if quality_bucket(profile) == best_quality
        ]
        chosen = min(quality_pool, key=lambda profile: diversity_key(profile, selected))
        remaining.remove(chosen)
        selected.append(chosen)
        ranked.append(chosen)
    return ranked


def selection_rationale(
    profile: dict[str, Any],
    group: str,
    recommended_primary_counts: Counter[str],
) -> str:
    source = (
        "official-homepage evidence"
        if profile["Website Text Characters"]
        else "existing sheet evidence only"
    )
    primary = profile["Primary Cuisine"]
    pieces = [f"{profile['Confidence'].capitalize()} confidence from {source}"]
    if favorite_true(profile):
        pieces.append("Favorite priority")
    if primary:
        pieces.append(f"primary cuisine: {primary}")
        if group == "recommended" and recommended_primary_counts[primary] <= 3:
            pieces.append("within the soft primary-cuisine cap")
    else:
        pieces.append("no defensible primary cuisine")
    if review_needed(profile):
        pieces.append("manual review needed")
    if group == "alternate":
        pieces.append("ranked behind stronger or more complementary profiles")
    if profile["Curation Note"]:
        pieces.append(profile["Curation Note"])
    return "; ".join(pieces)[:900]


def finalize_selection(
    profiles: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if MVP_TARGET_COUNT != 15:
        raise RuntimeError(
            "This pilot must produce exactly 15 recommendations; "
            f"MVP_TARGET_COUNT is {MVP_TARGET_COUNT}."
        )
    recommended = rank_profiles(profiles, MVP_TARGET_COUNT)
    recommended_ids = {profile["Sheet Row Number"] for profile in recommended}
    remaining = [
        profile for profile in profiles if profile["Sheet Row Number"] not in recommended_ids
    ]
    alternates = rank_profiles(
        remaining,
        EXPECTED_ALTERNATE_COUNT,
        already_selected=recommended,
    )

    primary_counts = Counter(
        profile["Primary Cuisine"]
        for profile in recommended
        if profile["Primary Cuisine"]
    )
    for rank, profile in enumerate(recommended, start=1):
        profile["Selection Group"] = "recommended"
        profile["Rank"] = rank
        profile["Curation Rationale"] = selection_rationale(
            profile,
            "recommended",
            primary_counts,
        )
    for rank, profile in enumerate(alternates, start=1):
        profile["Selection Group"] = "alternate"
        profile["Rank"] = rank
        profile["Curation Rationale"] = selection_rationale(
            profile,
            "alternate",
            primary_counts,
        )
    return recommended, alternates


def validate_outputs(
    input_profiles: list[dict[str, Any]],
    recommended: list[dict[str, Any]],
    alternates: list[dict[str, Any]],
) -> None:
    if len(input_profiles) != EXPECTED_ELIGIBLE_COUNT:
        raise RuntimeError("Profile count changed after the eligibility check.")
    if len(recommended) != 15 or len(alternates) != EXPECTED_ALTERNATE_COUNT:
        raise RuntimeError(
            f"Expected 15 recommendations and 8 alternates; got "
            f"{len(recommended)} and {len(alternates)}."
        )

    input_ids = {profile["Sheet Row Number"] for profile in input_profiles}
    output_profiles = recommended + alternates
    output_ids = [profile["Sheet Row Number"] for profile in output_profiles]
    if len(output_ids) != len(set(output_ids)):
        raise RuntimeError("A candidate appears more than once in the outputs.")
    if set(output_ids) != input_ids:
        raise RuntimeError("The 15+8 outputs do not partition all 23 input candidates.")

    for profile in input_profiles:
        if not set(profile["Cuisine Tags"]).issubset(CUISINE_ORDER):
            raise RuntimeError(f"Invalid Cuisine tag returned for {profile['Name']}.")
        if not set(profile["Vibe Tags"]).issubset(VIBE_ORDER):
            raise RuntimeError(f"Invalid Vibe tag returned for {profile['Name']}.")
        if not set(profile["Feature Tags"]).issubset(FEATURE_ORDER):
            raise RuntimeError(f"Invalid Feature tag returned for {profile['Name']}.")
        if len(profile["Cuisine Tags"]) > 3:
            raise RuntimeError(f"Too many Cuisine tags for {profile['Name']}.")
        if len(profile["Vibe Tags"]) > 3:
            raise RuntimeError(f"Too many Vibe tags for {profile['Name']}.")
        if len(profile["Feature Tags"]) > 5:
            raise RuntimeError(f"Too many Features for {profile['Name']}.")
        if profile["Primary Cuisine"] and profile["Primary Cuisine"] not in profile["Cuisine Tags"]:
            raise RuntimeError(f"Primary cuisine mismatch for {profile['Name']}.")


def csv_profile_row(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        **profile,
        "Cuisine": ", ".join(profile["Cuisine Tags"]),
        "Vibe": ", ".join(profile["Vibe Tags"]),
        "Features": ", ".join(profile["Feature Tags"]),
    }


def shortlist_row(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "Rank": profile["Rank"],
        "Sheet Row Number": profile["Sheet Row Number"],
        "Name": profile["Name"],
        "Primary Cuisine": profile["Primary Cuisine"],
        "Cuisine": ", ".join(profile["Cuisine Tags"]),
        "Vibe": ", ".join(profile["Vibe Tags"]),
        "Features": ", ".join(profile["Feature Tags"]),
        "Confidence": profile["Confidence"],
        "Evidence": profile["Evidence"],
        "Curation Rationale": profile["Curation Rationale"],
        "Website": profile["Website"],
        "Address": profile["Address"],
        "Google Place ID": profile["Google Place ID"],
    }


def duplicate_warnings(profiles: list[dict[str, Any]]) -> list[list[str]]:
    groups: list[list[str]] = []
    used: set[str] = set()
    for index, profile in enumerate(profiles):
        if profile["Sheet Row Number"] in used:
            continue
        matches = [profile["Name"]]
        for other in profiles[index + 1 :]:
            if near_duplicate(profile, other):
                matches.append(other["Name"])
                used.add(other["Sheet Row Number"])
        if len(matches) > 1:
            groups.append(matches)
    return groups


def diversity_summary(recommended: list[dict[str, Any]]) -> dict[str, Any]:
    primary_counts = Counter(
        profile["Primary Cuisine"] or "(blank)" for profile in recommended
    )
    cap_exceptions = {
        cuisine: count
        for cuisine, count in primary_counts.items()
        if cuisine != "(blank)" and count > 3
    }
    limitations: list[str] = []
    if primary_counts["(blank)"]:
        limitations.append(
            f"{primary_counts['(blank)']} recommendation(s) lack a defensible primary cuisine."
        )
    if cap_exceptions:
        limitations.append(
            "Soft maximum exceeded for: "
            + ", ".join(f"{tag} ({count})" for tag, count in cap_exceptions.items())
            + "."
        )
    low_count = sum(profile["Confidence"] == "low" for profile in recommended)
    review_count = sum(review_needed(profile) for profile in recommended)
    if low_count:
        limitations.append(f"{low_count} recommendation(s) have low confidence.")
    if review_count:
        limitations.append(f"{review_count} recommendation(s) need manual review.")
    if not limitations:
        limitations.append("No material diversity limitation was detected.")
    return {
        "primary_cuisine_distribution": dict(
            sorted(primary_counts.items(), key=lambda item: (-item[1], item[0]))
        ),
        "distinct_nonblank_primary_cuisines": len(
            [tag for tag in primary_counts if tag != "(blank)"]
        ),
        "soft_cap_exceptions": cap_exceptions,
        "limitations": limitations,
    }


def render_profile_table(profiles: list[dict[str, Any]]) -> str:
    rows = []
    for profile in profiles:
        rows.append(
            "<tr>"
            f"<td>{escape(str(profile.get('Rank', '')))}</td>"
            f"<td>{escape(profile['Name'])}</td>"
            f"<td>{escape(profile['Primary Cuisine'] or '—')}</td>"
            f"<td>{escape(', '.join(profile['Cuisine Tags']) or '—')}</td>"
            f"<td>{escape(', '.join(profile['Vibe Tags']) or '—')}</td>"
            f"<td>{escape(', '.join(profile['Feature Tags']) or '—')}</td>"
            f"<td>{escape(profile['Confidence'])}</td>"
            f"<td>{escape(profile['Review Needed'])}</td>"
            f"<td>{escape(profile['Evidence'])}</td>"
            f"<td>{escape(profile.get('Curation Rationale', ''))}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>Rank</th><th>Name</th><th>Primary cuisine</th>"
        "<th>Cuisine</th><th>Vibe</th><th>Features</th><th>Confidence</th>"
        "<th>Review</th><th>Evidence</th><th>Curation rationale</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def render_html(
    recommended: list[dict[str, Any]],
    alternates: list[dict[str, Any]],
    profiles: list[dict[str, Any]],
    website_failures: list[dict[str, Any]],
    diversity: dict[str, Any],
    model: str,
) -> str:
    low_or_review = [
        profile
        for profile in profiles
        if profile["Confidence"] == "low" or review_needed(profile)
    ]
    failure_items = "".join(
        f"<li><strong>{escape(item['Name'])}</strong>: "
        f"{escape(item['Website Extraction Status'])} — "
        f"{escape(item['Website Extraction Reason'])}</li>"
        for item in website_failures
    ) or "<li>None</li>"
    distribution_items = "".join(
        f"<li>{escape(cuisine)}: {count}</li>"
        for cuisine, count in diversity["primary_cuisine_distribution"].items()
    )
    limitation_items = "".join(
        f"<li>{escape(limitation)}</li>" for limitation in diversity["limitations"]
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Map MVP curation pilot — arrondissement {TARGET_ARRONDISSEMENT}</title>
<style>
body{{font-family:Arial,sans-serif;margin:2rem;color:#1d2433;background:#f7f7f5}}
h1,h2{{color:#182233}} .meta{{color:#56606f}} section{{margin:2rem 0}}
table{{width:100%;border-collapse:collapse;background:white;font-size:.88rem}}
th,td{{padding:.55rem;border:1px solid #d9dde3;vertical-align:top;text-align:left}}
th{{background:#e9edf3;position:sticky;top:0}} ul{{line-height:1.5}}
.card{{background:white;padding:1rem 1.25rem;border:1px solid #d9dde3;border-radius:8px}}
</style>
</head>
<body>
<h1>Map MVP curation pilot — arrondissement {TARGET_ARRONDISSEMENT}</h1>
<p class="meta">Read-only pilot · model {escape(model)} · generated {escape(now_iso())}</p>
<section><h2>Recommended 15</h2>{render_profile_table(recommended)}</section>
<section><h2>Eight alternatives</h2>{render_profile_table(alternates)}</section>
<section><h2>Low-confidence or review-needed candidates</h2>
{render_profile_table(low_or_review) if low_or_review else '<p>None</p>'}</section>
<section class="card"><h2>Website extraction failures</h2><ul>{failure_items}</ul></section>
<section class="card"><h2>Diversity summary</h2>
<h3>Recommended primary cuisines</h3><ul>{distribution_items}</ul>
<h3>Limitations</h3><ul>{limitation_items}</ul></section>
</body></html>
"""


def build_run_summary(
    model: str,
    counters: Counter[str],
    profiles: list[dict[str, Any]],
    recommended: list[dict[str, Any]],
    alternates: list[dict[str, Any]],
    website_failures: list[dict[str, Any]],
    diversity: dict[str, Any],
    duplicate_groups: list[list[str]],
    google_sheet_read_calls: int,
) -> dict[str, Any]:
    current_run = {
        "completed_at": now_iso(),
        "target_arrondissement": TARGET_ARRONDISSEMENT,
        "eligible_input_count": len(profiles),
        "recommendation_count": len(recommended),
        "alternate_count": len(alternates),
        "model": model,
        "api_calls": {
            "google_sheets_read_calls": google_sheet_read_calls,
            "official_website_http_calls": counters["website_http_calls"],
            "openai_llm_calls": counters["llm_api_calls"],
        },
        "cache": {
            "website_success_hits": counters["website_cache_hits"],
            "llm_result_hits": counters["llm_cache_hits"],
        },
        "website_extraction": {
            "usable_successes": sum(
                profile["Website Text Characters"] > 0 for profile in profiles
            ),
            "failures_or_skips": len(website_failures),
            "fresh_http_successes": counters["website_http_successes"],
            "fresh_http_failures": counters["website_http_failures"],
            "failure_details": [
                {
                    "sheet_row_number": profile["Sheet Row Number"],
                    "name": profile["Name"],
                    "website": profile["Website"],
                    "status": profile["Website Extraction Status"],
                    "reason": profile["Website Extraction Reason"],
                }
                for profile in website_failures
            ],
        },
        "confidence_distribution_all": dict(
            sorted(Counter(profile["Confidence"] for profile in profiles).items())
        ),
        "confidence_distribution_recommended": dict(
            sorted(Counter(profile["Confidence"] for profile in recommended).items())
        ),
        "review_needed_count_all": sum(review_needed(profile) for profile in profiles),
        "review_needed_count_recommended": sum(
            review_needed(profile) for profile in recommended
        ),
        "recommended_names": [profile["Name"] for profile in recommended],
        "alternate_names": [profile["Name"] for profile in alternates],
        "diversity": diversity,
        "near_duplicate_groups": duplicate_groups,
        "validations": {
            "eligible_input_exactly_23": len(profiles) == 23,
            "recommendations_exactly_15": len(recommended) == 15,
            "alternates_exactly_8": len(alternates) == 8,
            "all_candidates_partitioned_once": len(
                {
                    profile["Sheet Row Number"]
                    for profile in recommended + alternates
                }
            )
            == 23,
            "controlled_vocabularies_only": True,
            "google_sheets_writes": 0,
        },
    }
    previous = load_json(RUN_SUMMARY_PATH, {})
    history = list(previous.get("run_history", []))
    history.append(current_run)
    return {
        "latest_run": current_run,
        "run_history": history[-20:],
    }


def main() -> int:
    try:
        model = load_model_configuration()
        candidates, sheet_read_calls = load_candidates()
        cache = load_json(
            CACHE_PATH,
            {"version": 1, "website_successes": {}, "llm_results": {}},
        )
        if cache.get("version") != 1:
            cache = {"version": 1, "website_successes": {}, "llm_results": {}}
        cache.setdefault("website_successes", {})
        cache.setdefault("llm_results", {})
        counters: Counter[str] = Counter()

        print(
            f"Eligible arrondissement {TARGET_ARRONDISSEMENT} candidates: "
            f"{len(candidates)}",
            flush=True,
        )
        website_evidence_by_row: dict[str, dict[str, Any]] = {}
        for index, candidate in enumerate(candidates, start=1):
            evidence = collect_website_evidence(candidate, cache, counters)
            website_evidence_by_row[candidate["Sheet Row Number"]] = evidence
            print(
                f"Website {index:02d}/{len(candidates)}: {candidate['Name']} — "
                f"{evidence['status']}",
                flush=True,
            )

        profiles: list[dict[str, Any]] = []
        client: OpenAI | None = None
        for index, candidate in enumerate(candidates, start=1):
            evidence = website_evidence_by_row[candidate["Sheet Row Number"]]
            calls_before = counters["llm_api_calls"]
            result, client = profile_candidate(
                client,
                model,
                candidate,
                evidence,
                cache,
                counters,
            )
            source = "cache" if counters["llm_api_calls"] == calls_before else "API"
            profiles.append(profile_record(candidate, evidence, result))
            print(
                f"LLM {index:02d}/{len(candidates)}: {candidate['Name']} — {source}",
                flush=True,
            )

        recommended, alternates = finalize_selection(profiles)
        validate_outputs(profiles, recommended, alternates)
        diversity = diversity_summary(recommended)
        duplicate_groups = duplicate_warnings(profiles)
        website_failures = [
            profile for profile in profiles if not profile["Website Text Characters"]
        ]

        ordered_profiles = sorted(
            profiles,
            key=lambda profile: (
                0 if profile["Selection Group"] == "recommended" else 1,
                int(profile["Rank"]),
            ),
        )
        write_csv(
            REPORT_DIR / "candidate_profiles.csv",
            PROFILE_COLUMNS,
            [csv_profile_row(profile) for profile in ordered_profiles],
        )
        write_csv(
            REPORT_DIR / "recommended_15.csv",
            SHORTLIST_COLUMNS,
            [shortlist_row(profile) for profile in recommended],
        )
        write_csv(
            REPORT_DIR / "alternates.csv",
            SHORTLIST_COLUMNS,
            [shortlist_row(profile) for profile in alternates],
        )
        (REPORT_DIR / "review_report.html").write_text(
            render_html(
                recommended,
                alternates,
                profiles,
                website_failures,
                diversity,
                model,
            ),
            encoding="utf-8",
        )
        run_summary = build_run_summary(
            model,
            counters,
            profiles,
            recommended,
            alternates,
            website_failures,
            diversity,
            duplicate_groups,
            sheet_read_calls,
        )
        write_json(RUN_SUMMARY_PATH, run_summary)
        write_json(CACHE_PATH, cache)

        print("\nPilot complete", flush=True)
        print(
            f"API calls: Sheets reads={sheet_read_calls}, "
            f"website HTTP={counters['website_http_calls']}, "
            f"OpenAI={counters['llm_api_calls']}",
            flush=True,
        )
        print(
            f"Cache hits: websites={counters['website_cache_hits']}, "
            f"LLM={counters['llm_cache_hits']}",
            flush=True,
        )
        print(
            f"Website evidence: successes={len(profiles) - len(website_failures)}, "
            f"failures/skips={len(website_failures)}",
            flush=True,
        )
        print(f"Reports written to: {REPORT_DIR}", flush=True)
        return 0
    except Exception as error:
        print(f"Pilot failed: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
