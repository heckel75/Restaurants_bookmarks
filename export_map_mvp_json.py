"""Export the validated Paris Map MVP selection to static public JSON.

Google Sheets is accessed once with the read-only OAuth scope. All source,
eligibility, cardinality, contract, and privacy checks complete in memory before
either output file is atomically replaced. This script does not call Google
Places or OpenAI services and does not write to Google Sheets.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import tempfile
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import gspread
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials


SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR / "data" / "map_mvp"
RESTAURANTS_PATH = OUTPUT_DIR / "restaurants.json"
METADATA_PATH = OUTPUT_DIR / "metadata.json"
SHEET_TAB_DEFAULT = "Restaurants"
SHEETS_READONLY_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"

SCHEMA_VERSION = "1.0.0"
TARGET_TOTAL = 300
TARGET_PER_ARRONDISSEMENT = 15
ARRONDISSEMENTS = tuple(range(1, 21))
TAG_FIELDS = ("cuisine", "vibe", "features")
CONTRACT_ENUM_VALUES = {"TRUE", "FALSE", "UNKNOWN"}

# MAP_APP_MVP_SPEC.md is the source for these browser-visible fields. The
# current export requirement deliberately changes arrondissement from the
# spec's string type to an integer and forbids the spec's lastChecked field.
PUBLIC_FIELDS = (
    "id",
    "name",
    "googlePlaceId",
    "address",
    "city",
    "postalCode",
    "arrondissement",
    "town",
    "latitude",
    "longitude",
    "website",
    "instagram",
    "cuisine",
    "vibe",
    "features",
    "delivery",
    "takeaway",
    "favorite",
    "notes",
)

METADATA_FIELDS = (
    "schemaVersion",
    "generatedAt",
    "restaurantCount",
    "arrondissementCounts",
    "withWebsite",
    "withCuisine",
    "withVibe",
    "withFeatures",
)

REQUIRED_SHEET_COLUMNS = (
    "Include in MVP",
    "Name",
    "Google Place ID",
    "Status",
    "Needs Review",
    "Address",
    "City",
    "Postal Code",
    "Arrondissement",
    "Town",
    "Latitude",
    "Longitude",
    "Website",
    "Instagram",
    "Delivery",
    "Takeaway",
    "Cuisine",
    "Vibe",
    "Features",
    "Favorite",
    "Notes",
)

FORBIDDEN_OUTPUT_KEYS = (
    "Canonical Key",
    "Needs Review",
    "Review Reason",
    "Match Method",
    "Confidence",
    "Last Checked",
    "Source",
    "LLM Confidence",
    "LLM Evidence",
    "LLM Tagged at",
    "LLM Model",
    "LLM Review Needed",
    "Map Location",
    "Geocode Cache",
    "Include in MVP",
    "MVP Selection Reason",
    "Sheet Row Number",
)

POSTAL_CODE_RE = re.compile(r"(?<!\d)(\d{5})(?!\d)")
UNKNOWN_CITY_VALUES = {
    "-",
    "?",
    "france",
    "idf",
    "ile de france",
    "ile-de-france",
    "unknown",
    "inconnu",
    "n a",
    "na",
}


class ExportValidationError(RuntimeError):
    """Raised before output when the source cannot produce a valid MVP export."""


def cell(row: list[str], column_indexes: dict[str, int], column: str) -> str:
    """Return a trimmed sheet value without mutating the source snapshot."""
    index = column_indexes[column]
    return str(row[index]).strip() if index < len(row) else ""


def optional_string(value: str) -> str | None:
    """Represent a singular optional contract field as a string or null."""
    trimmed = value.strip()
    return trimmed if trimmed else None


def parse_arrondissement(value: str) -> int | None:
    """Return an integer arrondissement from 1 through 20."""
    text = value.strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if not math.isfinite(number) or not number.is_integer():
        return None
    arrondissement = int(number)
    return arrondissement if arrondissement in ARRONDISSEMENTS else None


def parse_coordinate(value: str, minimum: float, maximum: float) -> float | None:
    """Parse a finite coordinate, accepting a single French decimal comma."""
    text = value.strip()
    if not text:
        return None
    if text.count(",") == 1 and "." not in text:
        text = text.replace(",", ".")
    try:
        number = float(text)
    except ValueError:
        return None
    if not math.isfinite(number) or not minimum <= number <= maximum:
        return None
    return number


def normalize_text(value: str) -> str:
    """Normalize human-entered text for conservative location comparisons."""
    decomposed = unicodedata.normalize("NFKD", value)
    without_accents = "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character)
    )
    return re.sub(r"\s+", " ", without_accents.casefold().strip())


def paris_location_concerns(city: str, postal_code: str) -> list[str]:
    """Return explicit City/Postal Code evidence that a row is outside Paris."""
    concerns: list[str] = []
    normalized_city = normalize_text(city)
    if (
        normalized_city
        and normalized_city not in UNKNOWN_CITY_VALUES
        and not normalized_city.isdigit()
        and not re.search(r"\bparis\b", normalized_city)
    ):
        concerns.append(f"City indicates outside Paris: {city.strip()}")

    postal_codes = POSTAL_CODE_RE.findall(postal_code)
    if postal_codes and not any(code.startswith("75") for code in postal_codes):
        concerns.append(f"Postal Code indicates outside Paris: {postal_code.strip()}")
    return concerns


def parse_tags(value: str) -> list[str]:
    """Split comma-separated tags and preserve the first of each duplicate."""
    tags: list[str] = []
    seen: set[str] = set()
    for raw_tag in value.split(","):
        tag = raw_tag.strip()
        identity = tag.casefold()
        if tag and identity not in seen:
            tags.append(tag)
            seen.add(identity)
    return tags


def normalize_contract_enum(value: str, field: str, sheet_row_number: int) -> str:
    """Normalize Delivery/Takeaway while rejecting unsupported non-empty values."""
    normalized = value.strip().upper()
    if not normalized:
        return "UNKNOWN"
    if normalized not in CONTRACT_ENUM_VALUES:
        raise ExportValidationError(
            f"Sheet row {sheet_row_number} has unsupported {field} value {value!r}."
        )
    return normalized


def normalized_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def get_worksheet() -> Any:
    """Authorize only for Sheets read access and return the configured tab."""
    load_dotenv(SCRIPT_DIR / ".env")
    credentials_file = os.environ["GOOGLE_SHEETS_CREDENTIALS"]
    sheet_id = os.environ["GOOGLE_SHEET_ID"]
    sheet_tab = os.environ.get("GOOGLE_SHEET_TAB", SHEET_TAB_DEFAULT)

    credentials = Credentials.from_service_account_file(
        credentials_file,
        scopes=[SHEETS_READONLY_SCOPE],
    )
    client = gspread.authorize(credentials)
    spreadsheet = client.open_by_key(sheet_id)
    return spreadsheet.worksheet(sheet_tab)


def validate_headers(values: list[list[str]]) -> dict[str, int]:
    if not values:
        raise ExportValidationError("The configured worksheet is empty.")
    headers = [str(header).strip() for header in values[0]]
    duplicate_headers = sorted(
        header
        for header, count in Counter(headers).items()
        if header in REQUIRED_SHEET_COLUMNS and count > 1
    )
    if duplicate_headers:
        raise ExportValidationError(
            "Duplicate required sheet headers: " + ", ".join(duplicate_headers)
        )
    missing_headers = [
        header for header in REQUIRED_SHEET_COLUMNS if header not in headers
    ]
    if missing_headers:
        raise ExportValidationError(
            "Missing required sheet columns: " + ", ".join(missing_headers)
        )
    return {header: index for index, header in enumerate(headers)}


def eligibility_issues(
    row: list[str], column_indexes: dict[str, int]
) -> tuple[list[str], int | None, float | None, float | None]:
    """Evaluate the export eligibility rules for one selected source row."""
    issues: list[str] = []
    status = cell(row, column_indexes, "Status")
    needs_review = cell(row, column_indexes, "Needs Review")
    place_id = cell(row, column_indexes, "Google Place ID")
    raw_arrondissement = cell(row, column_indexes, "Arrondissement")
    latitude_text = cell(row, column_indexes, "Latitude")
    longitude_text = cell(row, column_indexes, "Longitude")

    if status.casefold() != "active":
        issues.append("Status is not active")
    if needs_review.casefold() != "false":
        issues.append("Needs Review is not FALSE")
    if not place_id:
        issues.append("Google Place ID is blank")

    latitude = parse_coordinate(latitude_text, -90.0, 90.0)
    longitude = parse_coordinate(longitude_text, -180.0, 180.0)
    if latitude is None:
        issues.append("Latitude is invalid")
    if longitude is None:
        issues.append("Longitude is invalid")

    arrondissement = parse_arrondissement(raw_arrondissement)
    if arrondissement is None:
        issues.append("Arrondissement is not an integer from 1 through 20")

    issues.extend(
        paris_location_concerns(
            cell(row, column_indexes, "City"),
            cell(row, column_indexes, "Postal Code"),
        )
    )
    return issues, arrondissement, latitude, longitude


def make_public_item(
    row: list[str],
    column_indexes: dict[str, int],
    sheet_row_number: int,
    arrondissement: int,
    latitude: float,
    longitude: float,
) -> dict[str, Any]:
    """Map one eligible sheet row to the approved RestaurantMapItem fields."""
    place_id = cell(row, column_indexes, "Google Place ID")
    name = cell(row, column_indexes, "Name")
    if not name:
        raise ExportValidationError(
            f"Sheet row {sheet_row_number} is eligible but has a blank Name."
        )

    return {
        "id": place_id,
        "name": name,
        "googlePlaceId": place_id,
        "address": optional_string(cell(row, column_indexes, "Address")),
        "city": optional_string(cell(row, column_indexes, "City")),
        "postalCode": optional_string(cell(row, column_indexes, "Postal Code")),
        "arrondissement": arrondissement,
        "town": optional_string(cell(row, column_indexes, "Town")),
        "latitude": latitude,
        "longitude": longitude,
        "website": optional_string(cell(row, column_indexes, "Website")),
        "instagram": optional_string(cell(row, column_indexes, "Instagram")),
        "cuisine": parse_tags(cell(row, column_indexes, "Cuisine")),
        "vibe": parse_tags(cell(row, column_indexes, "Vibe")),
        "features": parse_tags(cell(row, column_indexes, "Features")),
        "delivery": normalize_contract_enum(
            cell(row, column_indexes, "Delivery"),
            "Delivery",
            sheet_row_number,
        ),
        "takeaway": normalize_contract_enum(
            cell(row, column_indexes, "Takeaway"),
            "Takeaway",
            sheet_row_number,
        ),
        "favorite": optional_string(cell(row, column_indexes, "Favorite")),
        "notes": optional_string(cell(row, column_indexes, "Notes")),
    }


def item_sort_key(item: dict[str, Any]) -> tuple[int, str, str]:
    return (
        item["arrondissement"],
        item["name"].casefold(),
        item["googlePlaceId"],
    )


def build_restaurants(values: list[list[str]]) -> list[dict[str, Any]]:
    """Build and fully validate the public restaurant array in memory."""
    column_indexes = validate_headers(values)
    restaurants: list[dict[str, Any]] = []
    selected_ineligible: list[str] = []
    selected_count = 0

    for sheet_row_number, row in enumerate(values[1:], start=2):
        if cell(row, column_indexes, "Include in MVP").casefold() != "true":
            continue
        selected_count += 1
        issues, arrondissement, latitude, longitude = eligibility_issues(
            row, column_indexes
        )
        if issues:
            selected_ineligible.append(
                f"row {sheet_row_number}: " + "; ".join(issues)
            )
            continue

        assert arrondissement is not None
        assert latitude is not None
        assert longitude is not None
        restaurants.append(
            make_public_item(
                row,
                column_indexes,
                sheet_row_number,
                arrondissement,
                latitude,
                longitude,
            )
        )

    problems: list[str] = []
    if selected_ineligible:
        problems.append(
            f"{len(selected_ineligible)} selected row(s) violate export eligibility: "
            + " | ".join(selected_ineligible[:20])
        )
    if selected_count != TARGET_TOTAL:
        problems.append(
            f"expected {TARGET_TOTAL} Include in MVP rows; found {selected_count}"
        )
    if len(restaurants) != TARGET_TOTAL:
        problems.append(
            f"expected {TARGET_TOTAL} qualifying rows; found {len(restaurants)}"
        )

    arrondissement_counts = Counter(
        item["arrondissement"] for item in restaurants
    )
    incorrect_arrondissements = {
        arrondissement: arrondissement_counts[arrondissement]
        for arrondissement in ARRONDISSEMENTS
        if arrondissement_counts[arrondissement] != TARGET_PER_ARRONDISSEMENT
    }
    if incorrect_arrondissements:
        details = ", ".join(
            f"{arrondissement}={count}"
            for arrondissement, count in incorrect_arrondissements.items()
        )
        problems.append(
            f"expected {TARGET_PER_ARRONDISSEMENT} qualifying rows per "
            f"arrondissement; mismatches: {details}"
        )

    id_rows: dict[str, list[int]] = defaultdict(list)
    for item_index, item in enumerate(restaurants, start=1):
        id_rows[item["id"]].append(item_index)
    duplicate_ids = {
        item_id: indexes
        for item_id, indexes in id_rows.items()
        if len(indexes) > 1
    }
    if duplicate_ids:
        problems.append(
            "duplicate Google Place IDs: " + ", ".join(sorted(duplicate_ids))
        )

    if problems:
        raise ExportValidationError("Export validation failed: " + " | ".join(problems))

    restaurants.sort(key=item_sort_key)
    validate_restaurants_payload(restaurants)
    return restaurants


def assert_no_forbidden_keys(payload: Any) -> None:
    """Recursively reject internal/debug key names in any output object."""
    forbidden = {normalized_key(key) for key in FORBIDDEN_OUTPUT_KEYS}

    def walk(value: Any, location: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if normalized_key(str(key)) in forbidden:
                    raise ExportValidationError(
                        f"Forbidden output key {key!r} found at {location}."
                    )
                walk(child, f"{location}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{location}[{index}]")

    walk(payload, "$")


def validate_restaurants_payload(restaurants: Any) -> None:
    """Assert contract, privacy, normalization, totals, and ordering."""
    if not isinstance(restaurants, list):
        raise ExportValidationError("restaurants.json must contain a JSON array.")
    if len(restaurants) != TARGET_TOTAL:
        raise ExportValidationError(
            f"Expected {TARGET_TOTAL} restaurant objects; found {len(restaurants)}."
        )

    expected_fields = set(PUBLIC_FIELDS)
    optional_singular_fields = (
        "address",
        "city",
        "postalCode",
        "town",
        "website",
        "instagram",
        "favorite",
        "notes",
    )
    ids: list[str] = []
    arrondissement_counts: Counter[int] = Counter()

    for index, item in enumerate(restaurants):
        location = f"restaurants[{index}]"
        if not isinstance(item, dict):
            raise ExportValidationError(f"{location} is not an object.")
        if set(item) != expected_fields:
            missing = sorted(expected_fields - set(item))
            extra = sorted(set(item) - expected_fields)
            raise ExportValidationError(
                f"{location} contract mismatch; missing={missing}, extra={extra}."
            )
        if not isinstance(item["id"], str) or not item["id"].strip():
            raise ExportValidationError(f"{location}.id must be a non-empty string.")
        if item["googlePlaceId"] != item["id"]:
            raise ExportValidationError(
                f"{location}.id must equal its Google Place ID."
            )
        if not isinstance(item["name"], str) or not item["name"].strip():
            raise ExportValidationError(f"{location}.name must be a non-empty string.")

        arrondissement = item["arrondissement"]
        if type(arrondissement) is not int or arrondissement not in ARRONDISSEMENTS:
            raise ExportValidationError(
                f"{location}.arrondissement must be an integer from 1 through 20."
            )
        arrondissement_counts[arrondissement] += 1

        for field, minimum, maximum in (
            ("latitude", -90.0, 90.0),
            ("longitude", -180.0, 180.0),
        ):
            coordinate = item[field]
            if (
                isinstance(coordinate, bool)
                or not isinstance(coordinate, (int, float))
                or not math.isfinite(coordinate)
                or not minimum <= coordinate <= maximum
            ):
                raise ExportValidationError(
                    f"{location}.{field} is not a valid numeric coordinate."
                )

        for field in optional_singular_fields:
            value = item[field]
            if value is not None and (
                not isinstance(value, str) or not value or value != value.strip()
            ):
                raise ExportValidationError(
                    f"{location}.{field} must be null or a trimmed non-empty string."
                )

        for field in TAG_FIELDS:
            tags = item[field]
            if not isinstance(tags, list):
                raise ExportValidationError(f"{location}.{field} must be an array.")
            if any(
                not isinstance(tag, str) or not tag or tag != tag.strip()
                for tag in tags
            ):
                raise ExportValidationError(
                    f"{location}.{field} contains an empty or untrimmed tag."
                )
            identities = [tag.casefold() for tag in tags]
            if len(identities) != len(set(identities)):
                raise ExportValidationError(
                    f"{location}.{field} contains duplicate tags."
                )

        for field in ("delivery", "takeaway"):
            if item[field] not in CONTRACT_ENUM_VALUES:
                raise ExportValidationError(
                    f"{location}.{field} is outside the contract enum."
                )
        ids.append(item["id"])

    if len(ids) != len(set(ids)):
        raise ExportValidationError("restaurants.json contains duplicate IDs.")
    for arrondissement in ARRONDISSEMENTS:
        if arrondissement_counts[arrondissement] != TARGET_PER_ARRONDISSEMENT:
            raise ExportValidationError(
                f"Arrondissement {arrondissement} has "
                f"{arrondissement_counts[arrondissement]} items, not "
                f"{TARGET_PER_ARRONDISSEMENT}."
            )
    if restaurants != sorted(restaurants, key=item_sort_key):
        raise ExportValidationError("Restaurant ordering is not deterministic.")
    assert_no_forbidden_keys(restaurants)


def build_metadata(restaurants: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(item["arrondissement"] for item in restaurants)
    metadata = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "restaurantCount": len(restaurants),
        "arrondissementCounts": {
            str(arrondissement): counts[arrondissement]
            for arrondissement in ARRONDISSEMENTS
        },
        "withWebsite": sum(item["website"] is not None for item in restaurants),
        "withCuisine": sum(bool(item["cuisine"]) for item in restaurants),
        "withVibe": sum(bool(item["vibe"]) for item in restaurants),
        "withFeatures": sum(bool(item["features"]) for item in restaurants),
    }
    validate_metadata(metadata, restaurants)
    return metadata


def validate_metadata(
    metadata: Any, restaurants: list[dict[str, Any]]
) -> None:
    if not isinstance(metadata, dict) or tuple(metadata) != METADATA_FIELDS:
        raise ExportValidationError("metadata.json fields do not match its schema.")
    if metadata["schemaVersion"] != SCHEMA_VERSION:
        raise ExportValidationError("metadata schema version is incorrect.")
    if metadata["restaurantCount"] != len(restaurants):
        raise ExportValidationError("metadata restaurant count does not reconcile.")
    expected_counts = Counter(item["arrondissement"] for item in restaurants)
    if metadata["arrondissementCounts"] != {
        str(arrondissement): expected_counts[arrondissement]
        for arrondissement in ARRONDISSEMENTS
    }:
        raise ExportValidationError("metadata arrondissement counts do not reconcile.")
    for field, item_field in (
        ("withWebsite", "website"),
        ("withCuisine", "cuisine"),
        ("withVibe", "vibe"),
        ("withFeatures", "features"),
    ):
        expected = sum(bool(item[item_field]) for item in restaurants)
        if metadata[field] != expected:
            raise ExportValidationError(f"metadata {field} does not reconcile.")
    assert_no_forbidden_keys(metadata)


def write_json_atomic(path: Path, payload: Any) -> None:
    """Write stable UTF-8 JSON through a same-directory temporary file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as output_file:
            temporary_path = Path(output_file.name)
            json.dump(
                payload,
                output_file,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
            output_file.write("\n")
            output_file.flush()
            os.fsync(output_file.fileno())
        os.replace(temporary_path, path)
    except Exception:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
        raise


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as input_file:
        return json.load(input_file)


def assert_written_outputs(
    restaurants: list[dict[str, Any]], metadata: dict[str, Any]
) -> None:
    """Parse written JSON and rerun every payload assertion."""
    written_restaurants = read_json(RESTAURANTS_PATH)
    written_metadata = read_json(METADATA_PATH)
    if written_restaurants != restaurants:
        raise ExportValidationError(
            "Written restaurants.json does not match the validated payload."
        )
    if written_metadata != metadata:
        raise ExportValidationError(
            "Written metadata.json does not match the validated payload."
        )
    validate_restaurants_payload(written_restaurants)
    validate_metadata(written_metadata, written_restaurants)


def print_results(
    restaurants: list[dict[str, Any]], metadata: dict[str, Any]
) -> None:
    print(f"Exported restaurant count: {len(restaurants)}")
    print("Per-arrondissement counts:")
    for arrondissement, count in metadata["arrondissementCounts"].items():
        print(f"  {arrondissement}: {count}")
    print("Browser-visible RestaurantMapItem fields:")
    print("  " + ", ".join(PUBLIC_FIELDS))
    print("Missing optional-field counts:")
    for label, metadata_field in (
        ("Website", "withWebsite"),
        ("Cuisine", "withCuisine"),
        ("Vibe", "withVibe"),
        ("Features", "withFeatures"),
    ):
        print(f"  {label}: {len(restaurants) - metadata[metadata_field]}")
    print("JSON parse and payload assertions: PASS")
    print("Google Sheet access: READ-ONLY")
    print(f"Static JSON written to: {OUTPUT_DIR}")


def main() -> int:
    try:
        worksheet = get_worksheet()
        # Fetch once so both outputs derive from the same immutable source snapshot.
        values = worksheet.get_all_values()
        restaurants = build_restaurants(values)
        metadata = build_metadata(restaurants)

        # All validation above completes before either existing output is replaced.
        write_json_atomic(RESTAURANTS_PATH, restaurants)
        write_json_atomic(METADATA_PATH, metadata)
        assert_written_outputs(restaurants, metadata)
        print_results(restaurants, metadata)
        return 0
    except KeyError as error:
        print(
            f"Configuration error: missing environment variable {error.args[0]}",
            file=sys.stderr,
        )
    except Exception as error:
        print(f"Map MVP export failed: {type(error).__name__}: {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
