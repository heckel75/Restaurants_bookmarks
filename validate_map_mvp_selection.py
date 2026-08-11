"""Validate the completed Paris Map MVP selection without modifying Google Sheets.

The script authenticates with the Google Sheets read-only OAuth scope, reads the
configured worksheet once, and writes only the requested local CSV reports.
It never calls Google Places or OpenAI services.
"""

from __future__ import annotations

import csv
import math
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import gspread
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials


SCRIPT_DIR = Path(__file__).resolve().parent
REPORT_DIR = SCRIPT_DIR / "reports" / "map_mvp_selection"
SHEET_TAB_DEFAULT = "Restaurants"
SHEETS_READONLY_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"

TARGET_TOTAL = 300
TARGET_PER_ARRONDISSEMENT = 15
ARRONDISSEMENTS = tuple(range(1, 21))
ALLOWED_SELECTION_REASONS = (
    "favorite",
    "manual_essential",
    "curated_fill",
)
INFORMATIONAL_FIELDS = ("Website", "Cuisine", "Vibe", "Features")

REQUIRED_COLUMNS = (
    "Include in MVP",
    "MVP Selection Reason",
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
    "Instagram",
)

SELECTED_COLUMNS = (
    "Sheet Row Number",
    "Name",
    "Arrondissement",
    "MVP Selection Reason",
    "Favorite",
    "Cuisine",
    "Vibe",
    "Features",
    "Address",
    "Postal Code",
    "Website",
    "Instagram",
    "Google Place ID",
    "Latitude",
    "Longitude",
)

ISSUE_COLUMNS = (
    "Severity",
    "Issue Type",
    "Sheet Row Number",
    "Name",
    "Arrondissement",
    "Google Place ID",
    "Field",
    "Value",
    "Affected Sheet Rows",
    "Details",
)

SUMMARY_COLUMNS = (
    "Scope",
    "Arrondissement",
    "Selected Count",
    "Target Count",
    "favorite",
    "manual_essential",
    "curated_fill",
    "Blank Selection Reason",
    "Invalid Selection Reason",
    "Eligibility-Violating Selected Rows",
    "Selected Rows With Duplicate Place ID",
    "Duplicate Sheet Rows",
    "FALSE Rows With Selection Reason",
    "Missing Website",
    "Missing Cuisine",
    "Missing Vibe",
    "Missing Features",
    "Validation Result",
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


@dataclass
class Validation:
    """All derived records and counters from one immutable sheet read."""

    selected: list[dict[str, str]]
    issues: list[dict[str, str]]
    summary: list[dict[str, Any]]
    selected_counts: Counter[int]
    reason_counts: Counter[str]
    reason_counts_by_arrondissement: dict[int, Counter[str]]
    eligibility_rows: set[int]
    duplicate_place_ids: dict[str, list[int]]
    duplicate_sheet_rows: dict[int, int]
    false_reason_rows: list[int]
    missing_counts: Counter[str]
    passed: bool


def cell(row: list[str], column_indexes: dict[str, int], column: str) -> str:
    """Return a stripped cell value without changing the source row."""
    index = column_indexes[column]
    return str(row[index]).strip() if index < len(row) else ""


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
    """Parse a finite coordinate, accepting a single decimal comma."""
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


def get_worksheet() -> Any:
    """Authorize only for read access and return the configured worksheet."""
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
        raise RuntimeError("The configured worksheet is empty.")
    headers = [str(header).strip() for header in values[0]]
    duplicates = sorted(
        header
        for header, count in Counter(headers).items()
        if header in REQUIRED_COLUMNS and count > 1
    )
    if duplicates:
        raise RuntimeError("Duplicate required sheet headers: " + ", ".join(duplicates))
    missing = [header for header in REQUIRED_COLUMNS if header not in headers]
    if missing:
        raise RuntimeError("Missing required sheet columns: " + ", ".join(missing))
    return {header: index for index, header in enumerate(headers)}


def issue_record(
    severity: str,
    issue_type: str,
    *,
    sheet_row_number: int | str = "",
    name: str = "",
    arrondissement: int | str = "",
    place_id: str = "",
    field: str = "",
    value: str = "",
    affected_rows: Iterable[int] = (),
    details: str,
) -> dict[str, str]:
    return {
        "Severity": severity,
        "Issue Type": issue_type,
        "Sheet Row Number": str(sheet_row_number),
        "Name": name,
        "Arrondissement": str(arrondissement),
        "Google Place ID": place_id,
        "Field": field,
        "Value": value,
        "Affected Sheet Rows": ";".join(str(row) for row in affected_rows),
        "Details": details,
    }


def selected_record(
    row: list[str],
    column_indexes: dict[str, int],
    sheet_row_number: int,
    arrondissement: int | None,
) -> dict[str, str]:
    return {
        "Sheet Row Number": str(sheet_row_number),
        "Name": cell(row, column_indexes, "Name"),
        "Arrondissement": (
            str(arrondissement)
            if arrondissement is not None
            else cell(row, column_indexes, "Arrondissement")
        ),
        "MVP Selection Reason": cell(row, column_indexes, "MVP Selection Reason"),
        "Favorite": cell(row, column_indexes, "Favorite"),
        "Cuisine": cell(row, column_indexes, "Cuisine"),
        "Vibe": cell(row, column_indexes, "Vibe"),
        "Features": cell(row, column_indexes, "Features"),
        "Address": cell(row, column_indexes, "Address"),
        "Postal Code": cell(row, column_indexes, "Postal Code"),
        "Website": cell(row, column_indexes, "Website"),
        "Instagram": cell(row, column_indexes, "Instagram"),
        "Google Place ID": cell(row, column_indexes, "Google Place ID"),
        "Latitude": cell(row, column_indexes, "Latitude"),
        "Longitude": cell(row, column_indexes, "Longitude"),
    }


def selected_sort_key(record: dict[str, str]) -> tuple[Any, ...]:
    arrondissement = parse_arrondissement(record["Arrondissement"])
    return (
        arrondissement if arrondissement is not None else 999,
        record["Name"].casefold(),
        int(record["Sheet Row Number"]),
    )


def add_selected_error(
    issues: list[dict[str, str]],
    eligibility_rows: set[int],
    record: dict[str, str],
    issue_type: str,
    field: str,
    value: str,
    details: str,
) -> None:
    row_number = int(record["Sheet Row Number"])
    eligibility_rows.add(row_number)
    issues.append(
        issue_record(
            "ERROR",
            issue_type,
            sheet_row_number=row_number,
            name=record["Name"],
            arrondissement=record["Arrondissement"],
            place_id=record["Google Place ID"],
            field=field,
            value=value,
            details=details,
        )
    )


def build_summary(
    selected: list[dict[str, str]],
    selected_counts: Counter[int],
    reason_counts: Counter[str],
    reason_counts_by_arrondissement: dict[int, Counter[str]],
    eligibility_rows: set[int],
    duplicate_place_rows: set[int],
    duplicate_sheet_rows: dict[int, int],
    false_reason_row_arrondissements: dict[int, int | None],
    missing_rows: dict[str, set[int]],
    passed: bool,
) -> list[dict[str, Any]]:
    selected_by_row = {
        int(record["Sheet Row Number"]): record for record in selected
    }

    def rows_in_arrondissement(rows: Iterable[int], arrondissement: int) -> int:
        return sum(
            parse_arrondissement(selected_by_row[row]["Arrondissement"])
            == arrondissement
            for row in rows
            if row in selected_by_row
        )

    def make_row(arrondissement: int | None) -> dict[str, Any]:
        overall = arrondissement is None
        reasons = (
            reason_counts
            if overall
            else reason_counts_by_arrondissement[arrondissement]
        )
        selected_count = len(selected) if overall else selected_counts[arrondissement]
        false_reasons = (
            len(false_reason_row_arrondissements)
            if overall
            else sum(
                row_arrondissement == arrondissement
                for row_arrondissement in false_reason_row_arrondissements.values()
            )
        )
        if overall:
            duplicate_sheet_count = sum(
                count - 1 for count in duplicate_sheet_rows.values()
            )
            eligibility_count = len(eligibility_rows)
            duplicate_place_count = len(duplicate_place_rows)
            missing = {
                field: len(rows) for field, rows in missing_rows.items()
            }
            row_result = "PASS" if passed else "FAIL"
        else:
            duplicate_sheet_count = sum(
                count - 1
                for row, count in duplicate_sheet_rows.items()
                if row in selected_by_row
                and parse_arrondissement(selected_by_row[row]["Arrondissement"])
                == arrondissement
            )
            eligibility_count = rows_in_arrondissement(
                eligibility_rows, arrondissement
            )
            duplicate_place_count = rows_in_arrondissement(
                duplicate_place_rows, arrondissement
            )
            missing = {
                field: rows_in_arrondissement(rows, arrondissement)
                for field, rows in missing_rows.items()
            }
            row_result = (
                "PASS"
                if selected_count == TARGET_PER_ARRONDISSEMENT
                and eligibility_count == 0
                and duplicate_place_count == 0
                and duplicate_sheet_count == 0
                and false_reasons == 0
                else "FAIL"
            )

        return {
            "Scope": "Overall" if overall else "Arrondissement",
            "Arrondissement": "" if overall else arrondissement,
            "Selected Count": selected_count,
            "Target Count": TARGET_TOTAL if overall else TARGET_PER_ARRONDISSEMENT,
            "favorite": reasons["favorite"],
            "manual_essential": reasons["manual_essential"],
            "curated_fill": reasons["curated_fill"],
            "Blank Selection Reason": reasons["(blank)"],
            "Invalid Selection Reason": reasons["(invalid)"],
            "Eligibility-Violating Selected Rows": eligibility_count,
            "Selected Rows With Duplicate Place ID": duplicate_place_count,
            "Duplicate Sheet Rows": duplicate_sheet_count,
            "FALSE Rows With Selection Reason": false_reasons,
            "Missing Website": missing["Website"],
            "Missing Cuisine": missing["Cuisine"],
            "Missing Vibe": missing["Vibe"],
            "Missing Features": missing["Features"],
            "Validation Result": row_result,
        }

    return [make_row(None)] + [
        make_row(arrondissement) for arrondissement in ARRONDISSEMENTS
    ]


def evaluate_rows(values: list[list[str]]) -> Validation:
    """Evaluate the one source snapshot and derive every requested report."""
    column_indexes = validate_headers(values)
    selected: list[dict[str, str]] = []
    issues: list[dict[str, str]] = []
    eligibility_rows: set[int] = set()
    false_reason_rows: list[int] = []
    false_reason_row_arrondissements: dict[int, int | None] = {}
    missing_rows: dict[str, set[int]] = {
        field: set() for field in INFORMATIONAL_FIELDS
    }

    for sheet_row_number, row in enumerate(values[1:], start=2):
        include_value = cell(row, column_indexes, "Include in MVP")
        reason = cell(row, column_indexes, "MVP Selection Reason")
        raw_arrondissement = cell(row, column_indexes, "Arrondissement")
        arrondissement = parse_arrondissement(raw_arrondissement)

        if include_value.casefold() == "false" and reason:
            false_reason_rows.append(sheet_row_number)
            false_reason_row_arrondissements[sheet_row_number] = arrondissement
            issues.append(
                issue_record(
                    "ERROR",
                    "reason_on_unselected_false_row",
                    sheet_row_number=sheet_row_number,
                    name=cell(row, column_indexes, "Name"),
                    arrondissement=raw_arrondissement,
                    place_id=cell(row, column_indexes, "Google Place ID"),
                    field="MVP Selection Reason",
                    value=reason,
                    details=(
                        "Include in MVP is FALSE but MVP Selection Reason is populated."
                    ),
                )
            )

        if include_value.casefold() != "true":
            continue

        record = selected_record(
            row, column_indexes, sheet_row_number, arrondissement
        )
        selected.append(record)

        status = cell(row, column_indexes, "Status")
        if status.casefold() != "active":
            add_selected_error(
                issues,
                eligibility_rows,
                record,
                "selected_status_not_active",
                "Status",
                status,
                "Selected row must have Status = active.",
            )

        needs_review = cell(row, column_indexes, "Needs Review")
        if needs_review.casefold() != "false":
            add_selected_error(
                issues,
                eligibility_rows,
                record,
                "selected_needs_review_not_false",
                "Needs Review",
                needs_review,
                "Selected row must have Needs Review = FALSE.",
            )

        place_id = record["Google Place ID"]
        if not place_id:
            add_selected_error(
                issues,
                eligibility_rows,
                record,
                "selected_missing_google_place_id",
                "Google Place ID",
                place_id,
                "Selected row must have a Google Place ID.",
            )

        latitude = record["Latitude"]
        if parse_coordinate(latitude, -90.0, 90.0) is None:
            add_selected_error(
                issues,
                eligibility_rows,
                record,
                "selected_invalid_latitude",
                "Latitude",
                latitude,
                "Selected row must have a finite latitude from -90 through 90.",
            )

        longitude = record["Longitude"]
        if parse_coordinate(longitude, -180.0, 180.0) is None:
            add_selected_error(
                issues,
                eligibility_rows,
                record,
                "selected_invalid_longitude",
                "Longitude",
                longitude,
                "Selected row must have a finite longitude from -180 through 180.",
            )

        if arrondissement is None:
            add_selected_error(
                issues,
                eligibility_rows,
                record,
                "selected_invalid_arrondissement",
                "Arrondissement",
                raw_arrondissement,
                "Selected row must have an integer arrondissement from 1 through 20.",
            )

        city = cell(row, column_indexes, "City")
        postal_code = record["Postal Code"]
        for concern in paris_location_concerns(city, postal_code):
            field = "City" if concern.startswith("City") else "Postal Code"
            value = city if field == "City" else postal_code
            add_selected_error(
                issues,
                eligibility_rows,
                record,
                "selected_clearly_outside_paris",
                field,
                value,
                concern,
            )

        if not reason:
            add_selected_error(
                issues,
                eligibility_rows,
                record,
                "selected_blank_selection_reason",
                "MVP Selection Reason",
                reason,
                "Selected row must have an MVP Selection Reason.",
            )
        elif reason not in ALLOWED_SELECTION_REASONS:
            add_selected_error(
                issues,
                eligibility_rows,
                record,
                "selected_invalid_selection_reason",
                "MVP Selection Reason",
                reason,
                "Selection reason must be favorite, manual_essential, or curated_fill.",
            )

        for field in INFORMATIONAL_FIELDS:
            if not record[field]:
                missing_rows[field].add(sheet_row_number)
                issues.append(
                    issue_record(
                        "INFO",
                        f"selected_missing_{field.casefold()}",
                        sheet_row_number=sheet_row_number,
                        name=record["Name"],
                        arrondissement=record["Arrondissement"],
                        place_id=record["Google Place ID"],
                        field=field,
                        value="",
                        details=f"Selected row has a blank {field}; informational only.",
                    )
                )

    selected.sort(key=selected_sort_key)
    selected_row_numbers = [int(row["Sheet Row Number"]) for row in selected]
    selected_counts = Counter(
        arrondissement
        for record in selected
        if (arrondissement := parse_arrondissement(record["Arrondissement"]))
        is not None
    )

    if len(selected) != TARGET_TOTAL:
        issues.append(
            issue_record(
                "ERROR",
                "incorrect_total_selected",
                field="Include in MVP",
                value=str(len(selected)),
                details=f"Expected exactly {TARGET_TOTAL} selected rows; found {len(selected)}.",
            )
        )

    for arrondissement in ARRONDISSEMENTS:
        count = selected_counts[arrondissement]
        if count != TARGET_PER_ARRONDISSEMENT:
            issues.append(
                issue_record(
                    "ERROR",
                    "incorrect_arrondissement_selected_count",
                    arrondissement=arrondissement,
                    field="Include in MVP",
                    value=str(count),
                    details=(
                        f"Expected exactly {TARGET_PER_ARRONDISSEMENT} selected rows "
                        f"in arrondissement {arrondissement}; found {count}."
                    ),
                )
            )

    place_id_rows: dict[str, list[int]] = defaultdict(list)
    for record in selected:
        if record["Google Place ID"]:
            place_id_rows[record["Google Place ID"]].append(
                int(record["Sheet Row Number"])
            )
    duplicate_place_ids = {
        place_id: rows
        for place_id, rows in place_id_rows.items()
        if len(rows) > 1
    }
    for place_id, rows in sorted(duplicate_place_ids.items()):
        issues.append(
            issue_record(
                "ERROR",
                "duplicate_selected_google_place_id",
                place_id=place_id,
                field="Google Place ID",
                value=place_id,
                affected_rows=rows,
                details=f"Google Place ID is shared by {len(rows)} selected rows.",
            )
        )

    selected_row_counter = Counter(selected_row_numbers)
    duplicate_sheet_rows = {
        row_number: count
        for row_number, count in selected_row_counter.items()
        if count > 1
    }
    for row_number, count in sorted(duplicate_sheet_rows.items()):
        issues.append(
            issue_record(
                "ERROR",
                "duplicate_selected_sheet_row",
                sheet_row_number=row_number,
                affected_rows=[row_number] * count,
                details=f"Sheet row {row_number} appears {count} times in the selection.",
            )
        )

    reason_counts: Counter[str] = Counter()
    reason_counts_by_arrondissement: dict[int, Counter[str]] = {
        arrondissement: Counter() for arrondissement in ARRONDISSEMENTS
    }
    for record in selected:
        reason = record["MVP Selection Reason"]
        reason_bucket = (
            reason
            if reason in ALLOWED_SELECTION_REASONS
            else "(blank)" if not reason else "(invalid)"
        )
        reason_counts[reason_bucket] += 1
        arrondissement = parse_arrondissement(record["Arrondissement"])
        if arrondissement is not None:
            reason_counts_by_arrondissement[arrondissement][reason_bucket] += 1

    validation_errors = [
        issue for issue in issues if issue["Severity"] == "ERROR"
    ]
    passed = not validation_errors
    duplicate_place_rows = {
        row for rows in duplicate_place_ids.values() for row in rows
    }
    summary = build_summary(
        selected,
        selected_counts,
        reason_counts,
        reason_counts_by_arrondissement,
        eligibility_rows,
        duplicate_place_rows,
        duplicate_sheet_rows,
        false_reason_row_arrondissements,
        missing_rows,
        passed,
    )

    return Validation(
        selected=selected,
        issues=issues,
        summary=summary,
        selected_counts=selected_counts,
        reason_counts=reason_counts,
        reason_counts_by_arrondissement=reason_counts_by_arrondissement,
        eligibility_rows=eligibility_rows,
        duplicate_place_ids=duplicate_place_ids,
        duplicate_sheet_rows=duplicate_sheet_rows,
        false_reason_rows=false_reason_rows,
        missing_counts=Counter(
            {field: len(rows) for field, rows in missing_rows.items()}
        ),
        passed=passed,
    )


def write_csv(
    path: Path,
    fieldnames: tuple[str, ...],
    rows: list[dict[str, Any]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(
            output_file,
            fieldnames=fieldnames,
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as input_file:
        return list(csv.DictReader(input_file))


def assert_source_reconciliation(
    values: list[list[str]], validation: Validation
) -> None:
    """Assert every in-memory total against the unmodified source snapshot."""
    column_indexes = validate_headers(values)
    source_selected_rows = [
        sheet_row_number
        for sheet_row_number, row in enumerate(values[1:], start=2)
        if cell(row, column_indexes, "Include in MVP").casefold() == "true"
    ]
    report_selected_rows = [
        int(record["Sheet Row Number"]) for record in validation.selected
    ]
    assert len(source_selected_rows) == len(validation.selected), (
        "Selected report total does not reconcile with the source sheet."
    )
    assert set(source_selected_rows) == set(report_selected_rows), (
        "Selected report rows do not reconcile with the source sheet."
    )
    assert len(report_selected_rows) == len(set(report_selected_rows)), (
        "A selected sheet row appears more than once."
    )
    assert sum(validation.reason_counts.values()) == len(validation.selected), (
        "Overall selection-reason counts do not reconcile."
    )
    valid_arrondissement_total = sum(validation.selected_counts.values())
    invalid_arrondissement_total = sum(
        parse_arrondissement(record["Arrondissement"]) is None
        for record in validation.selected
    )
    assert valid_arrondissement_total + invalid_arrondissement_total == len(
        validation.selected
    ), "Arrondissement totals do not reconcile."
    for arrondissement in ARRONDISSEMENTS:
        assert (
            sum(validation.reason_counts_by_arrondissement[arrondissement].values())
            == validation.selected_counts[arrondissement]
        ), f"Reason counts do not reconcile for arrondissement {arrondissement}."
    assert validation.selected == sorted(validation.selected, key=selected_sort_key), (
        "selected_300.csv sorting is incorrect."
    )
    assert len(validation.summary) == 21, "Expected one overall and 20 summary rows."
    assert int(validation.summary[0]["Selected Count"]) == len(validation.selected)
    for field in INFORMATIONAL_FIELDS:
        assert validation.missing_counts[field] == sum(
            not record[field] for record in validation.selected
        ), f"Missing-{field} total does not reconcile."

    source_false_reason_rows = [
        sheet_row_number
        for sheet_row_number, row in enumerate(values[1:], start=2)
        if cell(row, column_indexes, "Include in MVP").casefold() == "false"
        and cell(row, column_indexes, "MVP Selection Reason")
    ]
    assert source_false_reason_rows == validation.false_reason_rows, (
        "FALSE-row selection-reason total does not reconcile."
    )

    for offset, arrondissement in enumerate(ARRONDISSEMENTS, start=1):
        summary_row = validation.summary[offset]
        arrondissement_records = [
            record
            for record in validation.selected
            if parse_arrondissement(record["Arrondissement"]) == arrondissement
        ]
        assert int(summary_row["Arrondissement"]) == arrondissement
        assert int(summary_row["Selected Count"]) == len(arrondissement_records)
        for reason in ALLOWED_SELECTION_REASONS:
            assert int(summary_row[reason]) == sum(
                record["MVP Selection Reason"] == reason
                for record in arrondissement_records
            ), f"{reason} summary does not reconcile for arrondissement {arrondissement}."
        for field in INFORMATIONAL_FIELDS:
            assert int(summary_row[f"Missing {field}"]) == sum(
                not record[field] for record in arrondissement_records
            ), f"Missing {field} does not reconcile for arrondissement {arrondissement}."


def assert_written_report_reconciliation(validation: Validation) -> None:
    """Reopen the generated reports and reconcile them with the source derivation."""
    selected_rows = read_csv(REPORT_DIR / "selected_300.csv")
    issue_rows = read_csv(REPORT_DIR / "selection_issues.csv")
    summary_rows = read_csv(REPORT_DIR / "selection_validation_summary.csv")

    expected_row_numbers = [
        record["Sheet Row Number"] for record in validation.selected
    ]
    assert [row["Sheet Row Number"] for row in selected_rows] == expected_row_numbers, (
        "Written selected_300.csv rows do not reconcile with the source sheet."
    )
    assert len(selected_rows) == len(validation.selected)
    assert len(issue_rows) == len(validation.issues)
    assert len(summary_rows) == len(validation.summary) == 21
    assert selected_rows == validation.selected, (
        "Written selected_300.csv content does not match the source derivation."
    )
    assert issue_rows == validation.issues, (
        "Written selection_issues.csv content does not match the source derivation."
    )
    expected_summary_rows = [
        {field: str(row[field]) for field in SUMMARY_COLUMNS}
        for row in validation.summary
    ]
    assert summary_rows == expected_summary_rows, (
        "Written selection_validation_summary.csv content does not match the "
        "source derivation."
    )
    assert int(summary_rows[0]["Selected Count"]) == len(selected_rows)
    assert summary_rows[0]["Validation Result"] == (
        "PASS" if validation.passed else "FAIL"
    )

    for field in INFORMATIONAL_FIELDS:
        assert int(summary_rows[0][f"Missing {field}"]) == sum(
            not row[field] for row in selected_rows
        ), f"Written Missing {field} count does not reconcile."

    for reason in ALLOWED_SELECTION_REASONS:
        assert int(summary_rows[0][reason]) == sum(
            row["MVP Selection Reason"] == reason for row in selected_rows
        ), f"Written {reason} count does not reconcile."

    error_count = sum(row["Severity"] == "ERROR" for row in issue_rows)
    assert (error_count == 0) == validation.passed, (
        "Written issue severity does not reconcile with the final result."
    )


def print_results(validation: Validation) -> None:
    print(f"Total selected: {len(validation.selected)}")
    print("Selected count by arrondissement:")
    for arrondissement in ARRONDISSEMENTS:
        print(f"  {arrondissement}: {validation.selected_counts[arrondissement]}")

    print("Selection-reason counts overall:")
    for reason in (*ALLOWED_SELECTION_REASONS, "(blank)", "(invalid)"):
        print(f"  {reason}: {validation.reason_counts[reason]}")

    eligibility_issues = [
        issue
        for issue in validation.issues
        if issue["Severity"] == "ERROR"
        and issue["Issue Type"].startswith("selected_")
        and issue["Issue Type"]
        not in {"duplicate_selected_google_place_id", "duplicate_selected_sheet_row"}
    ]
    print(
        "Eligibility violations: "
        f"{len(validation.eligibility_rows)} selected rows, "
        f"{len(eligibility_issues)} issue(s)"
    )
    eligibility_types = Counter(issue["Issue Type"] for issue in eligibility_issues)
    for issue_type, count in sorted(eligibility_types.items()):
        print(f"  {issue_type}: {count}")

    print(f"Duplicate Place IDs: {len(validation.duplicate_place_ids)}")
    for place_id, rows in sorted(validation.duplicate_place_ids.items()):
        print(f"  {place_id}: sheet rows {', '.join(map(str, rows))}")

    print(
        "Duplicate sheet rows: "
        f"{len(validation.duplicate_sheet_rows)}"
    )
    print("Informational missing-field counts:")
    for field in INFORMATIONAL_FIELDS:
        print(f"  {field}: {validation.missing_counts[field]}")

    false_reason_count = len(validation.false_reason_rows)
    print(f"FALSE rows with a selection reason: {false_reason_count}")
    print(f"Final result: {'PASS' if validation.passed else 'FAIL'}")


def main() -> int:
    try:
        worksheet = get_worksheet()
        # The source sheet is fetched once. Every report derives from this snapshot.
        values = worksheet.get_all_values()
        validation = evaluate_rows(values)
        assert_source_reconciliation(values, validation)

        write_csv(
            REPORT_DIR / "selection_validation_summary.csv",
            SUMMARY_COLUMNS,
            validation.summary,
        )
        write_csv(
            REPORT_DIR / "selected_300.csv",
            SELECTED_COLUMNS,
            validation.selected,
        )
        write_csv(
            REPORT_DIR / "selection_issues.csv",
            ISSUE_COLUMNS,
            validation.issues,
        )
        assert_written_report_reconciliation(validation)

        print_results(validation)
        print("Report reconciliation assertions: PASS")
        print("Google Sheet access: READ-ONLY")
        print(f"Local reports written to: {REPORT_DIR}")
        return 0 if validation.passed else 1
    except KeyError as error:
        print(
            f"Configuration error: missing environment variable {error.args[0]}",
            file=sys.stderr,
        )
    except AssertionError as error:
        print(f"Report reconciliation failed: {error}", file=sys.stderr)
    except Exception as error:
        print(
            f"Selection validation failed: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
