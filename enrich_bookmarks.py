import os
import re
import time
import random
import hashlib
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
        worksheet.append_row(blank_row, value_input_option="USER_ENTERED")
        row_num = max(row_cache.keys(), default=1) + 1
        row_cache[row_num] = blank_row

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
        q = {k: v for k, v in q.items() if not k.lower().startswith("utm_")}
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
    t = text.lower()
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


def build_sheet_review_candidates(row_cache, header_index):
    candidates = []

    def get_value(row, field_name):
        if field_name not in header_index:
            return ""

        idx = header_index[field_name]

        if idx >= len(row):
            return ""

        return (row[idx] or "").strip()

    for sheet_row_num, row in row_cache.items():
        needs_review = get_value(row, "Needs Review").upper()
        place_id = get_value(row, "Google Place ID")
        source = get_value(row, "Source").lower()

        if needs_review != "TRUE":
            continue

        if place_id:
            continue

        if source != "quick_add":
            continue

        name = clean_title(get_value(row, "Name"))
        if not name:
            continue

        arrondissement = get_value(row, "Arrondissement")
        town = get_value(row, "Town")
        city = get_value(row, "City")

        website = canonicalize_url(get_value(row, "Website"))
        instagram = canonicalize_url(get_value(row, "Instagram"))
        facebook = canonicalize_url(get_value(row, "Facebook"))

        if arrondissement:
            folder = f"Paris {arrondissement}"
        elif town:
            folder = f"Suburb {town}"
        elif city:
            folder = city
        else:
            folder = "Unsorted"

        url = website or instagram or facebook

        candidates.append(
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
                "sheet_row_num": sheet_row_num,
                "source_type": "quick_add",
                "dedupe_key": f"sheet_row:{sheet_row_num}",
                "location_hint_missing": not bool(arrondissement or town or city),
            }
        )

    return candidates


def google_find_place(query: str):
    url = "https://maps.googleapis.com/maps/api/place/findplacefromtext/json"
    params = {
        "input": query,
        "inputtype": "textquery",
        "fields": "place_id,name",
        "key": GOOGLE_KEY,
    }

    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()

    data = r.json()
    candidates = data.get("candidates", []) or []

    return candidates[0]["place_id"] if candidates else None


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
            ]
        ),
        "key": GOOGLE_KEY,
    }

    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()

    return r.json().get("result", {}) or {}


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
    worksheet = get_worksheet()
    headers, header_index, row_cache, canonical_key_to_row, place_id_to_row = (
        load_sheet_cache(worksheet)
    )

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
                    )
                    print(f"  -> Updated row {row_num}")
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
            )
            print(f"  -> Wrote row {row_num}")

            time.sleep(1.2)
            continue

        query = f"{clean_name} {city_hint}".strip()

        place_id = None
        if query:
            print(f"  -> Calling Google Places: {query}")
            place_id = google_find_place(query)

        if not place_id:
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
                    "Review Reason": "no_google_candidate",
                    "Match Method": "",
                }
            )

        else:
            details = google_place_details(place_id)

            google_name = details.get("name") or clean_name or title
            google_address = details.get("formatted_address", "")
            google_website = details.get("website", "")

            loc = (details.get("geometry", {}) or {}).get("location", {}) or {}
            lat = loc.get("lat")
            lng = loc.get("lng")

            types = details.get("types", []) or []
            business_status = details.get("business_status", "UNKNOWN")

            exact_ok, reason = is_exact_enough_match(
                bookmark_title=clean_name or title,
                folder=folder,
                original_url=url,
                google_name=google_name,
                google_address=google_address,
                google_website=google_website,
            )

            if not exact_ok:
                print(f"  -> Needs review ({reason})")

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
                        "Review Reason": reason,
                        "Match Method": "",
                    }
                )

            else:
                postal = extract_postal_code(google_address)
                arrondissement = parse_paris_arrondissement(postal)
                google_city_or_town = extract_city_from_address(google_address)

                cuisine, vibe, features = auto_classify(google_name, folder, types)

                final_website = ""
                if links["website"]:
                    final_website = links["website"]
                elif google_website:
                    final_website = google_website

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
        )

        print(f"  -> Wrote row {row_num}")

        time.sleep(1.2)


if __name__ == "__main__":
    main()