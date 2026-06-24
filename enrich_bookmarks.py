import os
import re
import time
import random
import hashlib
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from difflib import SequenceMatcher

import requests
import gspread
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
from gspread.exceptions import APIError
from requests.exceptions import ConnectionError, Timeout

load_dotenv()

GOOGLE_KEY = os.environ["GOOGLE_MAPS_API_KEY"]
HTML_PATH = os.environ.get("BOOKMARKS_HTML", "Restaurants.html")

SHEETS_CREDS_FILE = os.environ["GOOGLE_SHEETS_CREDENTIALS"]
SHEET_ID = os.environ["GOOGLE_SHEET_ID"]
SHEET_TAB = os.environ.get("GOOGLE_SHEET_TAB", "Restaurants")

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

PROTECTED_FIELDS = {"Cuisine", "Vibe", "Features"}

SCRIPT_OWNED_FIELDS = {
    "Name",
    "Google Place ID",
    "Canonical Key",
    "Status",
    "Needs Review",
    "Confidence",
    "Address",
    "City",
    "Postal Code",
    "Arrondissement",
    "Town",
    "Latitude",
    "Longitude",
    "Website",
    "Instagram",
    "Facebook",
    "Review Reason",
    "Match Method",
}

DEFAULT_MAX_PLACE_CANDIDATES = 5
MAX_PLACE_CANDIDATES_CAP = 10
RETRYABLE_REVIEW_REASONS = {"", "manual_review_required"}


@dataclass
class CandidateEvaluation:
    place_id: str
    name: str
    address: str
    details: dict
    score: float
    components: dict
    name_similarity: float
    token_overlap: float
    domain_status: str
    location_status: str
    hint_status: str
    hard_reject_reason: str = ""
    review_reason: str = ""
    accept_reason: str = ""


def env_bool(name: str, default: bool = False) -> bool:
    raw_value = os.environ.get(name)

    if raw_value is None:
        return default

    value = raw_value.strip().lower()

    if value in {"1", "true", "yes", "y", "on"}:
        return True

    if value in {"0", "false", "no", "n", "off"}:
        return False

    return default


def env_int(names, default: int = 0) -> int:
    if isinstance(names, str):
        names = [names]

    for name in names:
        raw_value = os.environ.get(name)

        if raw_value in (None, ""):
            continue

        try:
            return int(raw_value)
        except ValueError:
            print(f"Ignoring invalid integer for {name}: {raw_value!r}")

    return default


def get_worksheet():
    credentials = Credentials.from_service_account_file(
        SHEETS_CREDS_FILE, scopes=SCOPES
    )
    client = gspread.authorize(credentials)
    spreadsheet = client.open_by_key(SHEET_ID)
    return spreadsheet.worksheet(SHEET_TAB)


def load_sheet_cache(worksheet):
    all_values = worksheet.get_all_values()
    if not all_values:
        raise RuntimeError("Google Sheet is empty. Row 1 must contain headers.")

    headers = all_values[0]
    rows = all_values[1:]
    header_index = {h: i for i, h in enumerate(headers)}

    row_cache = {}
    canonical_key_to_row = {}
    place_id_to_row = {}

    for sheet_row_num, row in enumerate(rows, start=2):
        padded = row + [""] * (len(headers) - len(row))
        row_cache[sheet_row_num] = padded

        canonical_key = (
            padded[header_index["Canonical Key"]]
            if "Canonical Key" in header_index
            else ""
        )
        place_id = (
            padded[header_index["Google Place ID"]]
            if "Google Place ID" in header_index
            else ""
        )

        if canonical_key:
            canonical_key_to_row[canonical_key] = sheet_row_num
        if place_id:
            place_id_to_row[place_id] = sheet_row_num

    return headers, header_index, row_cache, canonical_key_to_row, place_id_to_row


def column_number_to_letter(n: int) -> str:
    result = ""
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        result = chr(65 + remainder) + result
    return result


def safe_update_row(worksheet, range_name, row_data, max_retries=8):
    for attempt in range(max_retries):
        try:
            worksheet.update(
                values=[row_data],
                range_name=range_name,
                value_input_option="USER_ENTERED",
            )
            return
        except APIError as e:
            if "429" in str(e):
                wait = min(60, (2**attempt) + random.uniform(0, 1))
                print(f"Rate limited by Sheets. Waiting {wait:.1f}s...")
                time.sleep(wait)
                continue
            raise
        except (ConnectionError, Timeout):
            wait = min(60, (2**attempt) + random.uniform(0, 1))
            print(f"Network error. Waiting {wait:.1f}s...")
            time.sleep(wait)
            continue

    raise RuntimeError("Failed to update row after retries")


def upsert_google_sheet_row(
    worksheet,
    header_index,
    row_cache,
    canonical_key_to_row,
    place_id_to_row,
    fields,
    target_row_num=None,
    dry_run=False,
):
    row_num = target_row_num

    place_id = fields.get("Google Place ID", "")
    canonical_key = fields.get("Canonical Key", "")

    if not row_num:
        if place_id and place_id in place_id_to_row:
            row_num = place_id_to_row[place_id]
        elif canonical_key and canonical_key in canonical_key_to_row:
            row_num = canonical_key_to_row[canonical_key]

    if not row_num:
        blank_row = [""] * len(header_index)
        row_num = max(row_cache.keys(), default=1) + 1
        row_cache[row_num] = blank_row

        if not dry_run:
            worksheet.append_row(blank_row, value_input_option="USER_ENTERED")

    row_data = row_cache.get(row_num, [""] * len(header_index))
    row_data = row_data + [""] * (len(header_index) - len(row_data))

    for field_name, value in fields.items():
        if field_name not in header_index:
            continue

        col_num = header_index[field_name]
        existing_value = row_data[col_num].strip()

        if field_name in PROTECTED_FIELDS and existing_value:
            continue

        if field_name in SCRIPT_OWNED_FIELDS:
            row_data[col_num] = "" if value is None else str(value)
            continue

        if not existing_value:
            row_data[col_num] = "" if value is None else str(value)

    end_col_letter = column_number_to_letter(len(header_index))
    range_name = f"A{row_num}:{end_col_letter}{row_num}"

    if dry_run:
        print(f"  -> DRY RUN: would update row {row_num} ({range_name})")
    else:
        safe_update_row(worksheet, range_name, row_data)

    row_cache[row_num] = row_data

    new_place_id = (
        row_data[header_index["Google Place ID"]]
        if "Google Place ID" in header_index
        else ""
    )
    new_canonical_key = (
        row_data[header_index["Canonical Key"]]
        if "Canonical Key" in header_index
        else ""
    )

    if new_place_id:
        place_id_to_row[new_place_id] = row_num
    if new_canonical_key:
        canonical_key_to_row[new_canonical_key] = row_num

    return row_num


def canonicalize_url(url: str) -> str:
    if not url:
        return ""

    try:
        p = urlparse(url.strip())
        q = parse_qs(p.query)
        q = {
            k: v
            for k, v in q.items()
            if not k.lower().startswith("utm_")
            and k.lower() not in {"tracking", "fbclid", "gclid"}
        }
        query = urlencode(q, doseq=True)
        path = p.path.rstrip("/") if p.path not in ("", "/") else p.path
        return urlunparse((p.scheme, p.netloc, path, p.params, query, p.fragment))
    except Exception:
        return url


def url_type(url: str) -> str:
    host = (urlparse(url or "").netloc or "").lower()
    if "instagram.com" in host:
        return "instagram"
    if "facebook.com" in host or "fb.com" in host:
        return "facebook"
    return "website"


def normalize_instagram_url(url: str) -> str:
    return url


def get_domain(url: str) -> str:
    if not url:
        return ""
    host = (urlparse(url).netloc or "").lower()
    return host.replace("www.", "")


def strip_accents(text: str) -> str:
    if not text:
        return ""

    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def normalize_location_for_match(text: str) -> str:
    if not text:
        return ""

    t = strip_accents(text).lower()
    t = re.sub(r"[’'`]", " ", t)
    t = re.sub(r"[-_/]", " ", t)
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def base_domain(domain: str) -> str:
    domain = (domain or "").lower().strip(".")
    parts = [p for p in domain.split(".") if p]

    if len(parts) <= 2:
        return domain

    if len(parts[-1]) == 2 and parts[-2] in {"co", "com", "net", "org"}:
        return ".".join(parts[-3:])

    return ".".join(parts[-2:])


def domains_match(left_url: str, right_url: str) -> bool:
    left = get_domain(left_url)
    right = get_domain(right_url)

    if not left or not right:
        return False

    if left == right:
        return True

    return base_domain(left) == base_domain(right)


def extract_postal_code(address: str) -> str:
    m = re.search(r"\b(\d{5})\b", address or "")
    return m.group(1) if m else ""


def extract_city_from_address(address: str) -> str:
    if not address:
        return ""

    m = re.search(r"\b\d{5}\s+([^,]+)", address)
    if not m:
        return ""

    return m.group(1).strip()


def parse_paris_arrondissement(postal_code: str):
    if postal_code and re.match(r"^750(0[1-9]|1[0-9]|20)$", postal_code):
        return int(postal_code[-2:])
    return None


def parse_arrondissement_hint(value: str):
    if not value:
        return None

    m = re.search(r"\b(\d{1,2})\b", str(value))
    if not m:
        return None

    arrondissement = int(m.group(1))
    if 1 <= arrondissement <= 20:
        return arrondissement

    return None


def location_terms_match(expected: str, actual: str) -> bool:
    expected_norm = normalize_location_for_match(expected)
    actual_norm = normalize_location_for_match(actual)

    if not expected_norm or not actual_norm:
        return False

    if expected_norm == actual_norm:
        return True

    if expected_norm in actual_norm or actual_norm in expected_norm:
        return True

    return SequenceMatcher(None, expected_norm, actual_norm).ratio() >= 0.88


def build_location_expectation(
    folder: str,
    arrondissement_hint: str = "",
    town_hint: str = "",
    city_hint: str = "",
    postal_hint: str = "",
):
    postal_hint = extract_postal_code(postal_hint or "")
    folder_l = (folder or "").strip().lower()

    if postal_hint and parse_paris_arrondissement(postal_hint):
        arrondissement = parse_paris_arrondissement(postal_hint)
        return {
            "kind": "paris_arrondissement",
            "postal_code": postal_hint,
            "arrondissement": arrondissement,
            "label": f"Paris {arrondissement}",
        }

    arrondissement = parse_arrondissement_hint(arrondissement_hint)

    paris_folder_match = re.match(r"^paris\s+(\d{1,2})$", folder_l)
    if not arrondissement and paris_folder_match:
        arrondissement = parse_arrondissement_hint(paris_folder_match.group(1))

    if arrondissement:
        return {
            "kind": "paris_arrondissement",
            "postal_code": f"750{arrondissement:02d}",
            "arrondissement": arrondissement,
            "label": f"Paris {arrondissement}",
        }

    if folder_l.startswith("paris") or normalize_location_for_match(city_hint) == "paris":
        return {"kind": "paris", "label": "Paris"}

    expected_town = (town_hint or "").strip()

    if not expected_town and folder_l.startswith("suburb "):
        expected_town = folder.replace("Suburb ", "", 1).strip()

    if expected_town:
        return {"kind": "town", "town": expected_town, "label": expected_town}

    if city_hint:
        return {"kind": "city", "city": city_hint.strip(), "label": city_hint.strip()}

    if folder_l == "dublin":
        return {"kind": "city", "city": "Dublin", "label": "Dublin"}

    return {"kind": "", "label": ""}


def make_canonical_key(name_hint: str, url: str, city_hint: str = "") -> str:
    base = (
        name_hint.strip().lower()
        + "|"
        + urlparse(url or "").netloc.lower()
        + "|"
        + city_hint.strip().lower()
    ).encode("utf-8")
    return hashlib.sha1(base).hexdigest()[:16]


def is_probably_not_a_restaurant(url: str) -> bool:
    host = (urlparse(url or "").netloc or "").lower()
    return any(
        x in host
        for x in ["lefigaro.fr", "yonder.fr", "mylittleparis.com", "01net.com"]
    )


def clean_title(text: str) -> str:
    if not text:
        return ""
    t = text.strip()
    t = re.sub(r"\s+\|\s+.*$", "", t)
    t = re.sub(r"^Home\s*-\s*", "", t, flags=re.I)
    t = re.sub(r"\s*-\s*Home$", "", t, flags=re.I)
    return t.strip()


def normalize_name_for_match(text: str) -> str:
    if not text:
        return ""
    t = strip_accents(text).lower()
    t = re.sub(r"[’'`]", "", t)
    t = re.sub(r"&", " and ", t)
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = re.sub(r"\b(restaurant|resto|paris|dublin|france|ireland)\b", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(
        None,
        normalize_name_for_match(a),
        normalize_name_for_match(b),
    ).ratio()


def token_overlap(a: str, b: str) -> float:
    a_tokens = set(normalize_name_for_match(a).split())
    b_tokens = set(normalize_name_for_match(b).split())

    if not a_tokens or not b_tokens:
        return 0.0

    inter = len(a_tokens & b_tokens)
    denom = max(1, len(a_tokens))
    return inter / denom


def address_token_overlap(a: str, b: str) -> float:
    stop_words = {
        "a",
        "au",
        "aux",
        "bis",
        "boulevard",
        "de",
        "des",
        "du",
        "france",
        "la",
        "le",
        "les",
        "paris",
        "place",
        "rue",
        "saint",
        "sainte",
    }
    a_tokens = set(normalize_location_for_match(a).split()) - stop_words
    b_tokens = set(normalize_location_for_match(b).split()) - stop_words

    if not a_tokens or not b_tokens:
        return 0.0

    return len(a_tokens & b_tokens) / max(1, len(a_tokens))


def has_location_expectation(expectation: dict) -> bool:
    return bool((expectation or {}).get("kind"))


def is_exact_location_status(status: str) -> bool:
    return status in {"exact_postal", "exact_town", "exact_city"}


def has_reasonable_name_match(evaluation: CandidateEvaluation) -> bool:
    return evaluation.name_similarity >= 0.72 or evaluation.token_overlap >= 0.67


def has_strong_name_match(evaluation: CandidateEvaluation) -> bool:
    return evaluation.name_similarity >= 0.88 or evaluation.token_overlap >= 0.95


def score_name_component(bookmark_title: str, google_name: str):
    sim = similarity(bookmark_title, google_name)
    overlap = token_overlap(bookmark_title, google_name)
    score = max(sim, overlap) * 45

    if sim >= 0.92 or overlap >= 1.0:
        score += 5

    return min(50.0, round(score, 1)), sim, overlap


def score_domain_component(input_website: str, google_website: str):
    if not input_website:
        return 0.0, "no_input_website", ""

    if not google_website:
        return 0.0, "missing_google_website", ""

    if domains_match(input_website, google_website):
        return 25.0, "match", ""

    return 0.0, "conflict", "domain_mismatch"


def score_location_component(expectation: dict, google_address: str):
    if not has_location_expectation(expectation):
        return 0.0, "no_location_hint", ""

    postal = extract_postal_code(google_address)
    city_or_town = extract_city_from_address(google_address)
    kind = expectation.get("kind")

    if kind == "paris_arrondissement":
        expected_postal = expectation.get("postal_code")

        if postal == expected_postal:
            return 25.0, "exact_postal", ""

        if postal and postal.startswith("75"):
            return 0.0, "wrong_arrondissement", "city_mismatch"

        if postal:
            return 0.0, "wrong_city", "city_mismatch"

        if location_terms_match("Paris", google_address):
            return 8.0, "paris_without_postal", ""

        return 0.0, "missing_postal", ""

    if kind == "paris":
        if postal and postal.startswith("75"):
            return 18.0, "exact_city", ""

        if postal:
            return 0.0, "wrong_city", "city_mismatch"

        if location_terms_match("Paris", google_address):
            return 12.0, "city_match_no_postal", ""

        return 0.0, "missing_city", ""

    if kind in {"town", "city"}:
        expected = expectation.get("town") or expectation.get("city") or ""

        if location_terms_match(expected, city_or_town) or location_terms_match(
            expected, google_address
        ):
            status = "exact_town" if kind == "town" else "exact_city"
            return 25.0, status, ""

        if city_or_town:
            return 0.0, "wrong_city", "city_mismatch"

        return 0.0, "missing_city", ""

    return 0.0, "no_location_hint", ""


def score_hint_component(address_hint: str, postal_hint: str, google_address: str):
    score = 0.0
    statuses = []
    hard_reject_reason = ""
    postal_hint = extract_postal_code(postal_hint or "") or extract_postal_code(
        address_hint or ""
    )
    candidate_postal = extract_postal_code(google_address)

    if postal_hint:
        if candidate_postal == postal_hint:
            score += 8.0
            statuses.append("postal_exact")
        elif candidate_postal:
            statuses.append("postal_conflict")
            hard_reject_reason = "city_mismatch"
        else:
            statuses.append("postal_missing")

    if address_hint:
        overlap = address_token_overlap(address_hint, google_address)

        if overlap >= 0.65:
            score += 7.0
            statuses.append("address_overlap")
        elif overlap >= 0.4:
            score += 3.0
            statuses.append("address_partial")
        else:
            statuses.append("address_weak")

    return round(score, 1), "+".join(statuses) if statuses else "no_address_hint", hard_reject_reason


def candidate_acceptance_reason(evaluation: CandidateEvaluation):
    if evaluation.hard_reject_reason:
        return False, evaluation.hard_reject_reason

    reasonable_name = has_reasonable_name_match(evaluation)
    strong_name = has_strong_name_match(evaluation)
    exact_location = is_exact_location_status(evaluation.location_status)
    exact_hint = "postal_exact" in evaluation.hint_status or "address_overlap" in evaluation.hint_status

    if evaluation.domain_status == "match" and reasonable_name and evaluation.score >= 58:
        return True, "domain_name_match"

    if strong_name and exact_location and evaluation.score >= 70:
        return True, "strong_name_location_match"

    if strong_name and exact_hint and evaluation.score >= 58:
        return True, "strong_name_hint_match"

    if evaluation.score >= 82 and reasonable_name and evaluation.components.get("location", 0) > 0:
        return True, "high_score_match"

    if not reasonable_name:
        return False, "name_mismatch"

    return False, "manual_review_required"


def evaluate_google_candidate(
    bookmark_title: str,
    input_website: str,
    candidate: dict,
    location_expectation: dict,
    address_hint: str = "",
    postal_hint: str = "",
):
    google_name = candidate.get("name") or bookmark_title
    google_address = (
        candidate.get("formatted_address") or candidate.get("vicinity") or ""
    )
    google_website = candidate.get("website", "")

    name_score, sim, overlap = score_name_component(bookmark_title, google_name)
    domain_score, domain_status, domain_reject = score_domain_component(
        input_website, google_website
    )
    location_score, location_status, location_reject = score_location_component(
        location_expectation, google_address
    )
    hint_score, hint_status, hint_reject = score_hint_component(
        address_hint, postal_hint, google_address
    )

    components = {
        "name": name_score,
        "location": location_score,
        "domain": domain_score,
        "hint": hint_score,
    }
    hard_reject_reason = domain_reject or location_reject or hint_reject
    score = round(sum(components.values()), 1)

    evaluation = CandidateEvaluation(
        place_id=candidate.get("place_id", ""),
        name=google_name,
        address=google_address,
        details=candidate,
        score=score,
        components=components,
        name_similarity=sim,
        token_overlap=overlap,
        domain_status=domain_status,
        location_status=location_status,
        hint_status=hint_status,
        hard_reject_reason=hard_reject_reason,
    )

    accepted, reason = candidate_acceptance_reason(evaluation)

    if accepted:
        evaluation.accept_reason = reason
    else:
        evaluation.review_reason = reason

    return evaluation


def is_close_competing_candidate(best: CandidateEvaluation, other: CandidateEvaluation) -> bool:
    if other.hard_reject_reason:
        return False

    if not has_reasonable_name_match(other):
        return False

    if other.score < 58 or other.score < best.score - 8:
        return False

    if best.domain_status == "match" and other.domain_status != "match":
        return False

    if is_exact_location_status(best.location_status) and not is_exact_location_status(
        other.location_status
    ):
        return False

    return True


def select_best_candidate(evaluations):
    if not evaluations:
        return None, "no_google_candidate"

    evaluations.sort(key=lambda e: e.score, reverse=True)
    viable = [e for e in evaluations if not e.hard_reject_reason]

    if not viable:
        return None, evaluations[0].hard_reject_reason or "manual_review_required"

    best = viable[0]
    close_matches = [
        e for e in viable[1:] if is_close_competing_candidate(best, e)
    ]

    if close_matches and (best.accept_reason or close_matches[0].score >= 58):
        return None, "multiple_possible_matches"

    if best.accept_reason:
        return best, ""

    return None, best.review_reason or "manual_review_required"


def component_summary(evaluation: CandidateEvaluation) -> str:
    base = ", ".join(
        f"{name} {score:g}" for name, score in evaluation.components.items()
    )
    return (
        f"{base}; sim {evaluation.name_similarity:.2f}; "
        f"overlap {evaluation.token_overlap:.2f}; "
        f"domain {evaluation.domain_status}; "
        f"location {evaluation.location_status}; hint {evaluation.hint_status}"
    )


def print_candidate_explanations(evaluations, selected: CandidateEvaluation, review_reason: str):
    selected_place_id = selected.place_id if selected else ""

    for evaluation in evaluations:
        decision_reason = (
            evaluation.accept_reason
            if selected_place_id and evaluation.place_id == selected_place_id
            else evaluation.hard_reject_reason
            or review_reason
            or evaluation.review_reason
            or "not_best_candidate"
        )
        decision = (
            "accept"
            if selected_place_id and evaluation.place_id == selected_place_id
            else "reject"
        )
        address = evaluation.address or "no address"
        print(
            "  -> Candidate: "
            f"{evaluation.name} | {address} | score {evaluation.score:g} | "
            f"{component_summary(evaluation)} | {decision} ({decision_reason})"
        )


def city_matches(folder: str, address: str) -> bool:
    folder_l = (folder or "").lower().strip()
    address_l = (address or "").lower().strip()

    paris_arrondissement_match = re.match(r"^paris\s+(\d{1,2})$", folder_l)
    if paris_arrondissement_match:
        arrondissement = int(paris_arrondissement_match.group(1))
        expected_postal_code = f"750{arrondissement:02d}"
        return expected_postal_code in address_l

    if folder_l.startswith("paris"):
        return "paris" in address_l or "750" in address_l

    if folder_l.startswith("suburb "):
        expected_town = folder_l.replace("suburb ", "", 1).strip()
        return expected_town in address_l

    if folder_l == "dublin":
        return "dublin" in address_l

    return True


def is_exact_enough_match(
    bookmark_title: str,
    folder: str,
    original_url: str,
    google_name: str,
    google_address: str,
    google_website: str,
):
    sim = similarity(bookmark_title, google_name)
    overlap = token_overlap(bookmark_title, google_name)
    city_ok = city_matches(folder, google_address)

    original_domain = get_domain(original_url) if url_type(original_url) == "website" else ""
    google_domain = get_domain(google_website)

    if original_domain and google_domain and original_domain != google_domain:
        if sim < 0.92 and overlap < 1.0:
            return False, "domain_mismatch"

    if not city_ok:
        return False, "city_mismatch"

    if sim >= 0.92:
        return True, "high_name_similarity"

    if overlap >= 1.0:
        return True, "full_token_overlap"

    if sim >= 0.84 and overlap >= 0.75:
        return True, "good_match"

    return False, "name_mismatch"


def normalize_status(value: str) -> str:
    v = (value or "").strip().lower()

    if v == "closed":
        return "closed"

    if v == "to_review":
        return "to_review"

    return "active"


def normalize_confidence(value: str) -> str:
    v = (value or "").strip().lower()

    if v in {"low", "medium", "high"}:
        return v

    return "low"


def normalize_needs_review(value: bool) -> str:
    return "TRUE" if value else "FALSE"


def parse_bookmarks_html(path: str):
    with open(path, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "html.parser")

    results = []
    current_folder = "Unsorted"

    for el in soup.find_all(["h3", "a"]):
        if el.name == "h3":
            current_folder = el.get_text(strip=True) or "Unsorted"

        elif el.name == "a":
            href = el.get("href")
            title = el.get_text(strip=True)

            if not href:
                continue

            href = canonicalize_url(href)

            if "instagram.com" in (urlparse(href).netloc or "").lower():
                href = normalize_instagram_url(href)

            results.append(
                {
                    "folder": current_folder,
                    "title": title,
                    "url": href,
                    "source_type": "bookmark",
                }
            )

    return results


def get_sheet_value(row, header_index, field_name):
    if field_name not in header_index:
        return ""

    idx = header_index[field_name]

    if idx >= len(row):
        return ""

    return (row[idx] or "").strip()


def is_clearly_restaurants_row(row, header_index):
    name = clean_title(get_sheet_value(row, header_index, "Name"))

    if not name:
        return False

    identity_fields = [
        "Website",
        "Instagram",
        "Facebook",
        "Address",
        "City",
        "Postal Code",
        "Arrondissement",
        "Town",
        "Source",
        "Status",
        "Review Reason",
    ]

    return any(get_sheet_value(row, header_index, field) for field in identity_fields)


def build_sheet_candidate_from_row(
    row,
    header_index,
    sheet_row_num,
    allow_restaurants_row_source=False,
):
    needs_review = get_sheet_value(row, header_index, "Needs Review").upper()
    place_id = get_sheet_value(row, header_index, "Google Place ID")
    source = get_sheet_value(row, header_index, "Source").lower()
    review_reason = get_sheet_value(row, header_index, "Review Reason").lower()

    if needs_review == "FALSE" and place_id:
        return None, "already validated (Needs Review is FALSE and Google Place ID is present)"

    if needs_review != "TRUE":
        return None, f"Needs Review is {needs_review or 'blank'}, not TRUE"

    if place_id:
        return None, "Google Place ID is already present"

    if review_reason not in RETRYABLE_REVIEW_REASONS:
        return None, f"stable Review Reason is {review_reason}"

    source_is_eligible = source == "quick_add" or review_reason == "manual_review_required"
    clearly_restaurants_row = is_clearly_restaurants_row(row, header_index)

    if not source_is_eligible and not (
        allow_restaurants_row_source and clearly_restaurants_row
    ):
        return None, f"Source is {source or 'blank'}, not quick_add/manual_review_required"

    name = clean_title(get_sheet_value(row, header_index, "Name"))
    if not name:
        return None, "Name is blank"

    arrondissement = get_sheet_value(row, header_index, "Arrondissement")
    town = get_sheet_value(row, header_index, "Town")
    city = get_sheet_value(row, header_index, "City")
    address = get_sheet_value(row, header_index, "Address")
    postal_code = get_sheet_value(row, header_index, "Postal Code")

    website = canonicalize_url(get_sheet_value(row, header_index, "Website"))
    instagram = canonicalize_url(get_sheet_value(row, header_index, "Instagram"))
    facebook = canonicalize_url(get_sheet_value(row, header_index, "Facebook"))

    if arrondissement:
        folder = f"Paris {arrondissement}"
    elif town:
        folder = f"Suburb {town}"
    elif city:
        folder = city
    else:
        folder = "Unsorted"

    url = website or instagram or facebook

    return (
        {
            "folder": folder,
            "title": name,
            "url": url,
            "website": website,
            "instagram": instagram,
            "facebook": facebook,
            "arrondissement": arrondissement,
            "town": town,
            "city": city,
            "address": address,
            "postal_code": postal_code,
            "sheet_row_num": sheet_row_num,
            "source_type": "quick_add",
            "dedupe_key": f"sheet_row:{sheet_row_num}",
            "location_hint_missing": not bool(
                arrondissement or town or city or address or postal_code
            ),
        },
        "",
    )


def build_sheet_review_candidates(row_cache, header_index):
    candidates = []

    for sheet_row_num, row in row_cache.items():
        candidate, _ = build_sheet_candidate_from_row(row, header_index, sheet_row_num)

        if candidate:
            candidates.append(candidate)

    return candidates


def build_target_row_candidate(row_cache, header_index, target_row):
    if target_row == 1:
        return None, "row 1 is the header row"

    row = row_cache.get(target_row)

    if row is None:
        return None, "row is empty or outside the loaded Restaurants sheet"

    return build_sheet_candidate_from_row(
        row,
        header_index,
        target_row,
        allow_restaurants_row_source=True,
    )


def google_find_place_candidates(query: str, max_candidates: int):
    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    params = {"query": query, "key": GOOGLE_KEY}

    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()

    data = r.json()
    results = data.get("results", []) or []

    return results[:max_candidates]


def google_place_details(place_id: str):
    url = "https://maps.googleapis.com/maps/api/place/details/json"
    params = {
        "place_id": place_id,
        "fields": ",".join(
            [
                "name",
                "formatted_address",
                "geometry/location",
                "types",
                "business_status",
                "website",
                "url",
                "place_id",
                "vicinity",
            ]
        ),
        "key": GOOGLE_KEY,
    }

    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()

    return r.json().get("result", {}) or {}


def google_place_candidate_details(query: str, max_candidates: int):
    search_candidates = google_find_place_candidates(query, max_candidates)
    detailed_candidates = []
    seen_place_ids = set()

    for search_candidate in search_candidates:
        place_id = search_candidate.get("place_id")

        if not place_id or place_id in seen_place_ids:
            continue

        seen_place_ids.add(place_id)
        details = google_place_details(place_id)
        merged = dict(search_candidate)
        merged.update({k: v for k, v in details.items() if v not in (None, "", [])})
        merged["place_id"] = place_id
        detailed_candidates.append(merged)

    return detailed_candidates


def auto_classify(name: str, folder: str, types: list):
    allowed_cuisine = {"bakery", "bistro", "cafe", "pizza", "ramen", "sushi", "wine_bar"}
    allowed_vibe = {"casual", "classic"}
    allowed_features = {"takeaway"}

    cuisine = set()
    vibe = set()
    features = set()

    folder_l = (folder or "").strip().lower()
    name_l = (name or "").strip().lower()
    type_set = set(types or [])

    if folder_l == "paris takeaway" or "meal_takeaway" in type_set:
        features.add("takeaway")

    if "cafe" in type_set or "cafe" in name_l or "café" in name_l:
        cuisine.add("cafe")

    if "bakery" in type_set:
        cuisine.add("bakery")

    if "bar" in type_set or "wine bar" in name_l:
        cuisine.add("wine_bar")

    if "ramen" in name_l:
        cuisine.add("ramen")

    if "pizza" in name_l:
        cuisine.add("pizza")

    if "sushi" in name_l:
        cuisine.add("sushi")

    if "bistro" in name_l:
        cuisine.add("bistro")

    if cuisine:
        vibe.add("casual")

    if "bistro" in cuisine:
        vibe.add("classic")

    cuisine = sorted(x for x in cuisine if x in allowed_cuisine)
    vibe = sorted(x for x in vibe if x in allowed_vibe)
    features = sorted(x for x in features if x in allowed_features)

    return cuisine, vibe, features


def main():
    dry_run = env_bool("DRY_RUN", True)
    max_rows = env_int(["MAX_ROWS", "MAX_CANDIDATES"], 0)
    target_row_raw = os.environ.get("TARGET_ROW", "").strip()
    target_row = env_int("TARGET_ROW", 0)
    max_place_candidates = max(
        1,
        min(
            env_int("MAX_PLACE_CANDIDATES", DEFAULT_MAX_PLACE_CANDIDATES),
            MAX_PLACE_CANDIDATES_CAP,
        ),
    )

    if dry_run:
        print("DRY_RUN=true; sheet writes are disabled.")

    print(f"Google Places candidates per search: {max_place_candidates}")

    worksheet = get_worksheet()
    headers, header_index, row_cache, canonical_key_to_row, place_id_to_row = (
        load_sheet_cache(worksheet)
    )

    if target_row_raw:
        if target_row <= 1:
            print(f"TARGET_ROW enabled: processing Restaurants row {target_row_raw}")
            print("  -> Skipping target row: TARGET_ROW must be a sheet data row number greater than 1")
            return

        print(f"TARGET_ROW enabled: processing Restaurants row {target_row}")
        bookmarks = []
        target_candidate, skip_reason = build_target_row_candidate(
            row_cache,
            header_index,
            target_row,
        )

        if not target_candidate:
            print(f"  -> Skipping target row {target_row}: {skip_reason}")
            return

        sheet_candidates = [target_candidate]
    else:
        process_bookmarks = os.environ.get("PROCESS_BOOKMARKS", "false").lower() == "true"
        bookmarks = parse_bookmarks_html(HTML_PATH) if process_bookmarks else []
        sheet_candidates = build_sheet_review_candidates(row_cache, header_index)

    candidates = bookmarks + sheet_candidates

    seen_keys = set()
    cleaned = []

    for bm in candidates:
        dedupe_key = (
            bm.get("dedupe_key")
            or bm.get("url")
            or f'{bm.get("title", "")}|{bm.get("folder", "")}'
        )

        if dedupe_key in seen_keys:
            continue

        seen_keys.add(dedupe_key)
        cleaned.append(bm)

    if max_rows > 0:
        cleaned = cleaned[:max_rows]

    total = len(cleaned)

    for i, bm in enumerate(cleaned, 1):
        folder = bm.get("folder", "")
        title = bm.get("title", "")
        url = bm.get("url", "") or ""
        source_type = bm.get("source_type", "bookmark")
        target_row_num = bm.get("sheet_row_num")

        print(f"[{i}/{total}] Processing: {title}")

        if is_probably_not_a_restaurant(url):
            print("  -> Skipping non-restaurant link")
            continue

        clean_name = clean_title(title)

        arrondissement_hint = str(bm.get("arrondissement", "") or "").strip()
        town_hint = str(bm.get("town", "") or "").strip()
        city_hint_from_row = str(bm.get("city", "") or "").strip()
        address_hint = str(bm.get("address", "") or "").strip()
        postal_code_hint = str(bm.get("postal_code", "") or "").strip()

        city_hint = ""

        paris_folder_match = re.match(r"^Paris\s+(\d{1,2})$", folder or "")
        if paris_folder_match:
            city_hint = f"Paris {int(paris_folder_match.group(1))}"
        elif folder.startswith("Paris"):
            city_hint = "Paris"
        elif folder.startswith("Suburb "):
            city_hint = folder.replace("Suburb ", "", 1).strip()
        elif city_hint_from_row:
            city_hint = city_hint_from_row
        elif folder == "Dublin":
            city_hint = "Dublin"

        location_expectation = build_location_expectation(
            folder,
            arrondissement_hint=arrondissement_hint,
            town_hint=town_hint,
            city_hint=city_hint_from_row or city_hint,
            postal_hint=postal_code_hint,
        )

        canonical_key = make_canonical_key(clean_name or title, url, city_hint)

        existing_row = canonical_key_to_row.get(canonical_key)

        if existing_row and not target_row_num:
            existing_values = row_cache.get(existing_row, [])
            row_dict = {
                h: existing_values[idx]
                for h, idx in header_index.items()
                if idx < len(existing_values)
            }

            needs_review_value = (row_dict.get("Needs Review") or "").strip().upper()
            place_id_value = (row_dict.get("Google Place ID") or "").strip()
            review_reason_value = (row_dict.get("Review Reason") or "").strip().lower()

            if (
                needs_review_value == "TRUE"
                and not place_id_value
                and review_reason_value not in RETRYABLE_REVIEW_REASONS
            ):
                print(f"  -> Existing review row is stable ({review_reason_value}), skipping")
                continue

            if needs_review_value == "FALSE" and place_id_value:
                match_method_value = (row_dict.get("Match Method") or "").strip()
                updates = {"Canonical Key": canonical_key}

                if not match_method_value:
                    updates["Match Method"] = "google_places"

                if not (row_dict.get("Needs Review") or "").strip():
                    updates["Needs Review"] = "FALSE"

                if len(updates) > 1:
                    print("  -> Backfilling validated row")
                    row_num = upsert_google_sheet_row(
                        worksheet,
                        header_index,
                        row_cache,
                        canonical_key_to_row,
                        place_id_to_row,
                        updates,
                        dry_run=dry_run,
                    )
                    action = "Would update" if dry_run else "Updated"
                    print(f"  -> {action} row {row_num}")
                else:
                    print("  -> Already validated, skipping")

                continue

        links = {
            "website": bm.get("website") or None,
            "instagram": bm.get("instagram") or None,
            "facebook": bm.get("facebook") or None,
        }

        if not any(links.values()) and url:
            lt = url_type(url)
            links[lt] = url
        else:
            lt = url_type(url)

        fields = {
            "Canonical Key": canonical_key,
            "Instagram": links["instagram"] or "",
            "Facebook": links["facebook"] or "",
        }

        if source_type == "quick_add" and bm.get("location_hint_missing") and not url:
            print("  -> Needs review (missing_location_hint)")
            cuisine, vibe, features = auto_classify(clean_name or title, folder, [])

            fields.update(
                {
                    "Name": clean_name or title,
                    "Google Place ID": "",
                    "Status": "to_review",
                    "Needs Review": "TRUE",
                    "Confidence": "low",
                    "Address": "",
                    "City": city_hint_from_row,
                    "Postal Code": "",
                    "Arrondissement": arrondissement_hint,
                    "Town": town_hint,
                    "Latitude": "",
                    "Longitude": "",
                    "Website": links["website"] or "",
                    "Cuisine": ", ".join(cuisine),
                    "Vibe": ", ".join(vibe),
                    "Features": ", ".join(features),
                    "Review Reason": "missing_location_hint",
                    "Match Method": "",
                }
            )

            row_num = upsert_google_sheet_row(
                worksheet,
                header_index,
                row_cache,
                canonical_key_to_row,
                place_id_to_row,
                fields,
                target_row_num=target_row_num,
                dry_run=dry_run,
            )
            action = "Would write" if dry_run else "Wrote"
            print(f"  -> {action} row {row_num}")

            time.sleep(1.2)
            continue

        query = f"{clean_name} {city_hint}".strip()

        google_candidates = []
        if query:
            print(f"  -> Calling Google Places: {query}")
            google_candidates = google_place_candidate_details(query, max_place_candidates)

        evaluations = [
            evaluate_google_candidate(
                bookmark_title=clean_name or title,
                input_website=links["website"] or "",
                candidate=candidate,
                location_expectation=location_expectation,
                address_hint=address_hint,
                postal_hint=postal_code_hint,
            )
            for candidate in google_candidates
        ]
        selected_candidate, review_reason = select_best_candidate(evaluations)

        if evaluations:
            print_candidate_explanations(
                evaluations,
                selected_candidate,
                review_reason,
            )
        else:
            print("  -> No Google candidates returned")

        if not selected_candidate:
            print(f"  -> Needs review ({review_reason})")
            cuisine, vibe, features = auto_classify(clean_name or title, folder, [])

            fields.update(
                {
                    "Name": clean_name or title,
                    "Google Place ID": "",
                    "Status": "to_review",
                    "Needs Review": "TRUE",
                    "Confidence": "low",
                    "Address": "",
                    "City": city_hint_from_row,
                    "Postal Code": "",
                    "Arrondissement": arrondissement_hint,
                    "Town": town_hint,
                    "Latitude": "",
                    "Longitude": "",
                    "Website": links["website"] or "",
                    "Cuisine": ", ".join(cuisine),
                    "Vibe": ", ".join(vibe),
                    "Features": ", ".join(features),
                    "Review Reason": review_reason,
                    "Match Method": "",
                }
            )

        else:
            details = selected_candidate.details
            place_id = selected_candidate.place_id
            google_name = selected_candidate.name
            google_address = selected_candidate.address
            google_website = details.get("website", "")

            loc = (details.get("geometry", {}) or {}).get("location", {}) or {}
            lat = loc.get("lat")
            lng = loc.get("lng")

            types = details.get("types", []) or []
            business_status = details.get("business_status", "UNKNOWN")

            postal = extract_postal_code(google_address)
            arrondissement = parse_paris_arrondissement(postal)
            google_city_or_town = extract_city_from_address(google_address)

            cuisine, vibe, features = auto_classify(google_name, folder, types)

            final_website = ""
            if links["website"]:
                final_website = links["website"]
            elif google_website:
                final_website = canonicalize_url(google_website)

            status = (
                "closed"
                if business_status == "CLOSED_PERMANENTLY"
                else "active"
            )
            confidence = "high" if (lat is not None and lng is not None) else "medium"

            if postal.startswith("75"):
                final_city = "Paris"
                final_town = ""
            else:
                final_city = google_city_or_town or town_hint or city_hint_from_row
                final_town = google_city_or_town or town_hint

            fields.update(
                {
                    "Name": google_name,
                    "Google Place ID": place_id,
                    "Status": normalize_status(status),
                    "Needs Review": normalize_needs_review(False),
                    "Confidence": normalize_confidence(confidence),
                    "Address": google_address,
                    "City": final_city,
                    "Postal Code": postal,
                    "Arrondissement": arrondissement if arrondissement is not None else "",
                    "Town": final_town,
                    "Latitude": lat if lat is not None else "",
                    "Longitude": lng if lng is not None else "",
                    "Website": final_website,
                    "Cuisine": ", ".join(cuisine),
                    "Vibe": ", ".join(vibe),
                    "Features": ", ".join(features),
                    "Review Reason": "",
                    "Match Method": "google_places",
                }
            )

        row_num = upsert_google_sheet_row(
            worksheet,
            header_index,
            row_cache,
            canonical_key_to_row,
            place_id_to_row,
            fields,
            target_row_num=target_row_num,
            dry_run=dry_run,
        )

        action = "Would write" if dry_run else "Wrote"
        print(f"  -> {action} row {row_num}")

        time.sleep(1.2)


if __name__ == "__main__":
    main()
