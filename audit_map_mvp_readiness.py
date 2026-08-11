"""Audit whether the Restaurants sheet can supply the Paris map MVP.

This script is intentionally read-only with respect to Google Sheets. It uses
the Sheets read-only OAuth scope, fetches the configured worksheet once, and
writes audit results only to local CSV files.
"""

from __future__ import annotations

import csv
import math
import os
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

import gspread
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials


SCRIPT_DIR = Path(__file__).resolve().parent
REPORT_DIR = SCRIPT_DIR / "reports" / "map_mvp_readiness"
SHEET_TAB_DEFAULT = "Restaurants"
SHEETS_READONLY_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
TARGET_PER_ARRONDISSEMENT = 15
ARRONDISSEMENTS = range(1, 21)

REQUIRED_COLUMNS = (
    "Name",
    "Status",
    "Needs Review",
    "Google Place ID",
    "Latitude",
    "Longitude",
    "Arrondissement",
    "Favorite",
    "Cuisine",
    "Vibe",
    "Features",
    "Address",
    "City",
    "Postal Code",
    "Website",
)

EXCLUSION_REASONS = (
    "inactive",
    "needs_review",
    "missing_place_id",
    "invalid_coordinates",
    "invalid_or_missing_arrondissement",
    "clearly_outside_paris",
)

CANDIDATE_COLUMNS = (
    "Sheet Row Number",
    "Name",
    "Arrondissement",
    "Favorite",
    "Cuisine",
    "Vibe",
    "Features",
    "Address",
    "Postal Code",
    "Website",
    "Google Place ID",
    "Latitude",
    "Longitude",
)

EXCLUDED_COLUMNS = (
    "Sheet Row Number",
    "Name",
    "Arrondissement",
    "City",
    "Postal Code",
    "Status",
    "Needs Review",
    "Google Place ID",
    "Latitude",
    "Longitude",
    "Exclusion Reasons",
    "Location Concern",
)

SUMMARY_COLUMNS = (
    "Arrondissement",
    "Eligible Rows",
    "Favorite Eligible Rows",
    "Deficit or Surplus",
    "Questionable-Location Rows",
)

ARRONDISSEMENT_RE = re.compile(
    r"^(?:paris\s+)?(\d{1,2})"
    r"(?:\s*(?:er|e|eme|ieme))?"
    r"(?:\s*(?:arr|arrondissement))?\.?$",
    re.IGNORECASE,
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


def cell(row: list[str], column_indexes: dict[str, int], column: str) -> str:
    """Return a stripped cell value without mutating the source row."""
    index = column_indexes[column]
    return row[index].strip() if index < len(row) else ""


def parse_arrondissement(value: str) -> int | None:
    """Resolve common sheet representations to an arrondissement from 1 to 20."""
    text = value.strip()
    if not text:
        return None

    try:
        number = float(text)
    except ValueError:
        normalized = normalize_text(text)
        match = ARRONDISSEMENT_RE.fullmatch(normalized)
        if not match:
            return None
        number = float(match.group(1))

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
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", without_accents.casefold().strip())


def paris_location_concerns(city: str, postal_code: str) -> list[str]:
    """Return explicit City/Postal Code conflicts with Paris, without geocoding."""
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
    # Department 75 is Paris; accepting the full prefix also avoids rejecting
    # legitimate Paris CEDEX values as though they clearly identified a suburb.
    if postal_codes and not any(code.startswith("75") for code in postal_codes):
        concerns.append(f"Postal Code indicates outside Paris: {postal_code.strip()}")

    return concerns


def get_worksheet() -> Any:
    """Authorize with read-only scope and return the configured worksheet."""
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


def evaluate_rows(
    values: list[list[str]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[dict[str, str]], Counter[str]]:
    """Evaluate every data row and build summary, candidate, and exclusion records."""
    if not values:
        raise RuntimeError("The configured worksheet is empty.")

    headers = [header.strip() for header in values[0]]
    duplicate_headers = sorted(
        header for header, count in Counter(headers).items() if header and count > 1
    )
    if duplicate_headers:
        raise RuntimeError(f"Duplicate required sheet headers: {', '.join(duplicate_headers)}")

    column_indexes = {header: index for index, header in enumerate(headers)}
    missing_columns = [column for column in REQUIRED_COLUMNS if column not in column_indexes]
    if missing_columns:
        raise RuntimeError(f"Missing required sheet columns: {', '.join(missing_columns)}")

    eligible_counts = Counter({arrondissement: 0 for arrondissement in ARRONDISSEMENTS})
    favorite_counts = Counter({arrondissement: 0 for arrondissement in ARRONDISSEMENTS})
    questionable_counts = Counter({arrondissement: 0 for arrondissement in ARRONDISSEMENTS})
    exclusion_counts = Counter({reason: 0 for reason in EXCLUSION_REASONS})
    candidates: list[dict[str, str]] = []
    excluded_rows: list[dict[str, str]] = []

    for sheet_row_number, row in enumerate(values[1:], start=2):
        reasons: list[str] = []

        status = cell(row, column_indexes, "Status")
        needs_review = cell(row, column_indexes, "Needs Review")
        place_id = cell(row, column_indexes, "Google Place ID")
        latitude = cell(row, column_indexes, "Latitude")
        longitude = cell(row, column_indexes, "Longitude")
        raw_arrondissement = cell(row, column_indexes, "Arrondissement")
        city = cell(row, column_indexes, "City")
        postal_code = cell(row, column_indexes, "Postal Code")

        if status.casefold() != "active":
            reasons.append("inactive")
        if needs_review.casefold() != "false":
            reasons.append("needs_review")
        if not place_id:
            reasons.append("missing_place_id")
        if (
            parse_coordinate(latitude, -90.0, 90.0) is None
            or parse_coordinate(longitude, -180.0, 180.0) is None
        ):
            reasons.append("invalid_coordinates")

        arrondissement = parse_arrondissement(raw_arrondissement)
        location_concerns: list[str] = []
        if arrondissement is None:
            reasons.append("invalid_or_missing_arrondissement")
        else:
            location_concerns = paris_location_concerns(city, postal_code)
            if location_concerns:
                if not reasons:
                    questionable_counts[arrondissement] += 1
                reasons.append("clearly_outside_paris")

        for reason in reasons:
            exclusion_counts[reason] += 1

        if not reasons:
            assert arrondissement is not None
            favorite = cell(row, column_indexes, "Favorite")
            eligible_counts[arrondissement] += 1
            if favorite.casefold() == "true":
                favorite_counts[arrondissement] += 1

            candidates.append(
                {
                    "Sheet Row Number": str(sheet_row_number),
                    "Name": cell(row, column_indexes, "Name"),
                    "Arrondissement": str(arrondissement),
                    "Favorite": favorite,
                    "Cuisine": cell(row, column_indexes, "Cuisine"),
                    "Vibe": cell(row, column_indexes, "Vibe"),
                    "Features": cell(row, column_indexes, "Features"),
                    "Address": cell(row, column_indexes, "Address"),
                    "Postal Code": postal_code,
                    "Website": cell(row, column_indexes, "Website"),
                    "Google Place ID": place_id,
                    "Latitude": latitude,
                    "Longitude": longitude,
                }
            )
        else:
            excluded_rows.append(
                {
                    "Sheet Row Number": str(sheet_row_number),
                    "Name": cell(row, column_indexes, "Name"),
                    "Arrondissement": raw_arrondissement,
                    "City": city,
                    "Postal Code": postal_code,
                    "Status": status,
                    "Needs Review": needs_review,
                    "Google Place ID": place_id,
                    "Latitude": latitude,
                    "Longitude": longitude,
                    "Exclusion Reasons": ";".join(reasons),
                    "Location Concern": "; ".join(location_concerns),
                }
            )

    candidates.sort(
        key=lambda candidate: (
            int(candidate["Arrondissement"]),
            candidate["Favorite"].casefold() != "true",
            candidate["Name"].casefold(),
            int(candidate["Sheet Row Number"]),
        )
    )
    excluded_rows.sort(key=lambda excluded: int(excluded["Sheet Row Number"]))

    summary = [
        {
            "Arrondissement": arrondissement,
            "Eligible Rows": eligible_counts[arrondissement],
            "Favorite Eligible Rows": favorite_counts[arrondissement],
            "Deficit or Surplus": eligible_counts[arrondissement]
            - TARGET_PER_ARRONDISSEMENT,
            "Questionable-Location Rows": questionable_counts[arrondissement],
        }
        for arrondissement in ARRONDISSEMENTS
    ]
    return summary, candidates, excluded_rows, exclusion_counts


def write_csv(path: Path, fieldnames: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def print_summary(
    summary: list[dict[str, Any]],
    candidates: list[dict[str, str]],
    exclusion_counts: Counter[str],
) -> None:
    table_headers = ("Arr.", "Eligible", "Favorites", "Deficit/Surplus", "Questionable")
    widths = (5, 8, 9, 15, 12)

    def table_row(items: tuple[Any, ...]) -> str:
        return "  ".join(str(item).rjust(width) for item, width in zip(items, widths))

    print(table_row(table_headers))
    print(table_row(tuple("-" * width for width in widths)))
    for record in summary:
        difference = int(record["Deficit or Surplus"])
        print(
            table_row(
                (
                    record["Arrondissement"],
                    record["Eligible Rows"],
                    record["Favorite Eligible Rows"],
                    f"{difference:+d}",
                    record["Questionable-Location Rows"],
                )
            )
        )

    total_eligible = len(candidates)
    total_favorites = sum(
        1 for candidate in candidates if candidate["Favorite"].casefold() == "true"
    )
    below_target = [
        int(record["Arrondissement"])
        for record in summary
        if int(record["Eligible Rows"]) < TARGET_PER_ARRONDISSEMENT
    ]
    feasible = not below_target

    print()
    print(f"Total eligible Paris rows: {total_eligible}")
    print(f"Total eligible favorites: {total_favorites}")
    print(
        "Arrondissements below 15: "
        + (", ".join(str(arrondissement) for arrondissement in below_target) or "none")
    )
    print("Exclusions by reason (a row can have more than one reason):")
    for reason in EXCLUSION_REASONS:
        print(f"  {reason}: {exclusion_counts[reason]}")
    print(f"15 x 20 target currently feasible: {'YES' if feasible else 'NO'}")


def main() -> int:
    try:
        worksheet = get_worksheet()
        values = worksheet.get_all_values()
        summary, candidates, excluded_rows, exclusion_counts = evaluate_rows(values)

        write_csv(REPORT_DIR / "arrondissement_summary.csv", SUMMARY_COLUMNS, summary)
        write_csv(REPORT_DIR / "eligible_candidates.csv", CANDIDATE_COLUMNS, candidates)
        write_csv(REPORT_DIR / "excluded_rows.csv", EXCLUDED_COLUMNS, excluded_rows)
        print_summary(summary, candidates, exclusion_counts)
        print(f"\nLocal reports written to: {REPORT_DIR}")
        return 0
    except KeyError as error:
        print(f"Configuration error: missing environment variable {error.args[0]}", file=sys.stderr)
    except Exception as error:
        print(f"Audit failed: {type(error).__name__}: {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
