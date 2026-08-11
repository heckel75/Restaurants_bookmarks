"""Add the Map MVP selection controls to the Restaurants worksheet once.

The migration is deliberately narrow: it duplicates the worksheet before the
first modification, inserts only missing control columns, initializes blank
Include in MVP cells to FALSE for existing restaurant rows, applies dropdown
validation, and verifies that the original restaurant data stayed aligned.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import gspread
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials


SCRIPT_DIR = Path(__file__).resolve().parent
SHEET_TAB_DEFAULT = "Restaurants"
BACKUP_TAB = "Restaurants backup before Map MVP columns"
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"

INCLUDE_HEADER = "Include in MVP"
REASON_HEADER = "MVP Selection Reason"
TARGET_HEADERS = (INCLUDE_HEADER, REASON_HEADER)
LEGACY_HEADERS = ("Map Location", "Geocode Cache")
INCLUDE_VALUES = ("TRUE", "FALSE")
REASON_VALUES = ("favorite", "manual_essential", "curated_fill")


@dataclass(frozen=True)
class SheetSnapshot:
    values: list[list[str]]
    formulas: list[list[str]]
    headers: list[str]
    grid_row_count: int
    grid_column_count: int
    existing_row_numbers: list[int]


@dataclass(frozen=True)
class InsertionPlan:
    headers_to_insert: tuple[str, ...]
    zero_based_index: int | None
    used_legacy_anchor: bool
    assumption: str


def cell_at(rows: list[list[str]], row_index: int, column_index: int) -> str:
    if row_index >= len(rows) or column_index >= len(rows[row_index]):
        return ""
    return str(rows[row_index][column_index])


def column_number_to_letter(column_number: int) -> str:
    result = ""
    while column_number:
        column_number, remainder = divmod(column_number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def header_positions(headers: list[str], header: str) -> list[int]:
    """Return one-based positions for an exact header match."""
    return [index + 1 for index, value in enumerate(headers) if value == header]


def connect_spreadsheet() -> tuple[gspread.Spreadsheet, gspread.Worksheet]:
    """Reuse the repository's service-account and environment configuration."""
    load_dotenv(SCRIPT_DIR / ".env")
    credentials_file = os.environ["GOOGLE_SHEETS_CREDENTIALS"]
    sheet_id = os.environ["GOOGLE_SHEET_ID"]
    sheet_tab = os.environ.get("GOOGLE_SHEET_TAB", SHEET_TAB_DEFAULT)

    credentials = Credentials.from_service_account_file(
        credentials_file,
        scopes=[SHEETS_SCOPE],
    )
    client = gspread.authorize(credentials)
    spreadsheet = client.open_by_key(sheet_id)
    return spreadsheet, spreadsheet.worksheet(sheet_tab)


def capture_snapshot(worksheet: gspread.Worksheet) -> SheetSnapshot:
    values = worksheet.get_all_values(pad_values=True)
    formulas = worksheet.get_all_values(
        value_render_option=gspread.utils.ValueRenderOption.formula,
        pad_values=True,
    )
    if not values:
        raise RuntimeError(f"Worksheet {worksheet.title!r} is empty.")

    existing_row_numbers = [
        row_number
        for row_number, row in enumerate(values[1:], start=2)
        if any(str(value).strip() for value in row)
    ]
    return SheetSnapshot(
        values=values,
        formulas=formulas,
        headers=[str(value).strip() for value in values[0]],
        grid_row_count=worksheet.row_count,
        grid_column_count=worksheet.col_count,
        existing_row_numbers=existing_row_numbers,
    )


def make_insertion_plan(snapshot: SheetSnapshot) -> InsertionPlan:
    for header in TARGET_HEADERS:
        positions = header_positions(snapshot.headers, header)
        if len(positions) > 1:
            raise RuntimeError(
                f"Header {header!r} already appears {len(positions)} times; "
                "refusing to add another copy."
            )

    missing_headers = tuple(
        header for header in TARGET_HEADERS if header not in snapshot.headers
    )
    legacy_positions = {
        header: header_positions(snapshot.headers, header) for header in LEGACY_HEADERS
    }
    legacy_anchor_available = all(
        len(legacy_positions[header]) == 1 for header in LEGACY_HEADERS
    )

    if not missing_headers:
        return InsertionPlan(
            headers_to_insert=(),
            zero_based_index=None,
            used_legacy_anchor=legacy_anchor_available,
            assumption=(
                "Both Map MVP columns already exist; no column insertion is needed."
            ),
        )

    if legacy_anchor_available:
        legacy_anchor = min(
            legacy_positions[header][0] - 1 for header in LEGACY_HEADERS
        )

        # Handle a partially completed prior migration without duplicating or
        # moving the existing control column.
        if missing_headers == (INCLUDE_HEADER,):
            reason_index = snapshot.headers.index(REASON_HEADER)
            if reason_index != legacy_anchor - 1:
                raise RuntimeError(
                    "MVP Selection Reason exists away from the legacy-column "
                    "anchor; refusing to move existing data automatically."
                )
            legacy_anchor = reason_index
        elif missing_headers == (REASON_HEADER,):
            include_index = snapshot.headers.index(INCLUDE_HEADER)
            if include_index != legacy_anchor - 1:
                raise RuntimeError(
                    "Include in MVP exists away from the legacy-column anchor; "
                    "refusing to move existing data automatically."
                )

        return InsertionPlan(
            headers_to_insert=missing_headers,
            zero_based_index=legacy_anchor,
            used_legacy_anchor=True,
            assumption=(
                "Found Map Location and Geocode Cache exactly once; inserting "
                "the missing Map MVP columns immediately before them."
            ),
        )

    return InsertionPlan(
        headers_to_insert=missing_headers,
        zero_based_index=len(snapshot.headers),
        used_legacy_anchor=False,
        assumption=(
            "Could not find both legacy headers exactly once; appending the "
            "missing Map MVP columns after the rightmost used column."
        ),
    )


def ensure_backup(
    spreadsheet: gspread.Spreadsheet,
    worksheet: gspread.Worksheet,
    snapshot: SheetSnapshot,
) -> tuple[gspread.Worksheet, bool]:
    matching_backups = [
        candidate
        for candidate in spreadsheet.worksheets()
        if candidate.title == BACKUP_TAB
    ]
    if matching_backups:
        return matching_backups[0], False

    backup = spreadsheet.duplicate_sheet(
        source_sheet_id=worksheet.id,
        new_sheet_name=BACKUP_TAB,
    )

    # Verify the new backup before touching the source worksheet.
    backup_values = backup.get_all_values(pad_values=True)
    if (
        backup.row_count != snapshot.grid_row_count
        or backup.col_count != snapshot.grid_column_count
        or backup_values != snapshot.values
    ):
        raise RuntimeError(
            "The backup worksheet did not match the pre-migration source; "
            "the Restaurants worksheet has not been modified."
        )
    return backup, True


def insert_missing_columns(
    spreadsheet: gspread.Spreadsheet,
    worksheet: gspread.Worksheet,
    plan: InsertionPlan,
) -> gspread.Worksheet:
    if not plan.headers_to_insert:
        return worksheet
    assert plan.zero_based_index is not None

    spreadsheet.batch_update(
        {
            "requests": [
                {
                    "insertDimension": {
                        "range": {
                            "sheetId": worksheet.id,
                            "dimension": "COLUMNS",
                            "startIndex": plan.zero_based_index,
                            "endIndex": (
                                plan.zero_based_index + len(plan.headers_to_insert)
                            ),
                        },
                        # The insertion point is never the first column in this
                        # migration. This gives the new controls the neighboring
                        # sheet formatting without changing existing columns.
                        "inheritFromBefore": True,
                    }
                }
            ]
        }
    )

    worksheet = spreadsheet.worksheet(worksheet.title)
    first_column = plan.zero_based_index + 1
    last_column = first_column + len(plan.headers_to_insert) - 1
    header_range = (
        f"{column_number_to_letter(first_column)}1:"
        f"{column_number_to_letter(last_column)}1"
    )
    worksheet.update(
        values=[list(plan.headers_to_insert)],
        range_name=header_range,
        value_input_option="RAW",
    )
    return worksheet


def consecutive_groups(row_numbers: Iterable[int]) -> list[list[int]]:
    groups: list[list[int]] = []
    for row_number in sorted(row_numbers):
        if not groups or row_number != groups[-1][-1] + 1:
            groups.append([row_number])
        else:
            groups[-1].append(row_number)
    return groups


def initialize_include_false(
    worksheet: gspread.Worksheet,
    existing_row_numbers: list[int],
) -> int:
    values = worksheet.get_all_values(pad_values=True)
    headers = [str(value).strip() for value in values[0]]
    include_column = headers.index(INCLUDE_HEADER) + 1
    rows_to_initialize = [
        row_number
        for row_number in existing_row_numbers
        if not cell_at(values, row_number - 1, include_column - 1).strip()
    ]

    updates: list[dict[str, Any]] = []
    column_letter = column_number_to_letter(include_column)
    for group in consecutive_groups(rows_to_initialize):
        updates.append(
            {
                "range": f"{column_letter}{group[0]}:{column_letter}{group[-1]}",
                "values": [["FALSE"] for _ in group],
            }
        )
    if updates:
        worksheet.batch_update(updates, value_input_option="USER_ENTERED")
    return len(rows_to_initialize)


def apply_validation(
    spreadsheet: gspread.Spreadsheet,
    worksheet: gspread.Worksheet,
) -> None:
    headers = [str(value).strip() for value in worksheet.row_values(1)]
    include_index = headers.index(INCLUDE_HEADER)
    reason_index = headers.index(REASON_HEADER)

    def validation_request(column_index: int, allowed_values: tuple[str, ...]) -> dict:
        return {
            "setDataValidation": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": 1,
                    "endRowIndex": worksheet.row_count,
                    "startColumnIndex": column_index,
                    "endColumnIndex": column_index + 1,
                },
                "rule": {
                    "condition": {
                        "type": "ONE_OF_LIST",
                        "values": [
                            {"userEnteredValue": value} for value in allowed_values
                        ],
                    },
                    "strict": True,
                    "showCustomUi": True,
                },
            }
        }

    spreadsheet.batch_update(
        {
            "requests": [
                validation_request(include_index, INCLUDE_VALUES),
                validation_request(reason_index, REASON_VALUES),
            ]
        }
    )


def mapped_column_index(original_index: int, plan: InsertionPlan) -> int:
    if (
        plan.zero_based_index is not None
        and plan.headers_to_insert
        and original_index >= plan.zero_based_index
    ):
        return original_index + len(plan.headers_to_insert)
    return original_index


def verify_migration(
    spreadsheet: gspread.Spreadsheet,
    worksheet: gspread.Worksheet,
    before: SheetSnapshot,
    plan: InsertionPlan,
) -> tuple[dict[str, bool], list[str], SheetSnapshot]:
    after = capture_snapshot(worksheet)
    problems: list[str] = []

    exact_headers = all(
        len(header_positions(after.headers, header)) == 1 for header in TARGET_HEADERS
    )
    if not exact_headers:
        problems.append("One or both Map MVP headers do not exist exactly once.")

    row_count_unchanged = (
        after.grid_row_count == before.grid_row_count
        and len(after.values) == len(before.values)
    )
    if not row_count_unchanged:
        problems.append(
            "The source worksheet row count or populated row extent changed."
        )

    data_alignment_ok = True
    for row_index in range(len(before.values)):
        for original_index, header in enumerate(before.headers):
            if header in TARGET_HEADERS:
                continue
            current_index = mapped_column_index(original_index, plan)
            before_value = cell_at(before.values, row_index, original_index)
            after_value = cell_at(after.values, row_index, current_index)
            if before_value != after_value:
                data_alignment_ok = False
                problems.append(
                    "Original data mismatch at source "
                    f"row {row_index + 1}, column {original_index + 1} "
                    f"({header or '<blank header>'!r})."
                )
                break

            before_formula = cell_at(before.formulas, row_index, original_index)
            after_formula = cell_at(after.formulas, row_index, current_index)
            if before_formula.startswith("=") and not after_formula.startswith("="):
                data_alignment_ok = False
                problems.append(
                    "A formula disappeared at source "
                    f"row {row_index + 1}, column {original_index + 1}."
                )
                break
        if not data_alignment_ok:
            break

    existing_nonempty_controls_preserved = True
    for header in TARGET_HEADERS:
        positions = header_positions(before.headers, header)
        if not positions:
            continue
        original_index = positions[0] - 1
        current_index = mapped_column_index(original_index, plan)
        for row_index in range(len(before.values)):
            original_value = cell_at(before.values, row_index, original_index)
            if original_value and original_value != cell_at(
                after.values, row_index, current_index
            ):
                existing_nonempty_controls_preserved = False
                problems.append(
                    f"A non-empty {header!r} value changed at row {row_index + 1}."
                )
                break

    include_index = after.headers.index(INCLUDE_HEADER)
    reason_index = after.headers.index(REASON_HEADER)
    invalid_include_rows: list[int] = []
    invalid_reason_rows: list[int] = []
    reason_while_false_rows: list[int] = []
    for row_number in before.existing_row_numbers:
        include_value = cell_at(after.values, row_number - 1, include_index).strip()
        normalized_include = include_value.upper()
        reason_value = cell_at(after.values, row_number - 1, reason_index).strip()
        if normalized_include not in INCLUDE_VALUES:
            invalid_include_rows.append(row_number)
        if reason_value and reason_value not in REASON_VALUES:
            invalid_reason_rows.append(row_number)
        if reason_value and normalized_include == "FALSE":
            reason_while_false_rows.append(row_number)

    if invalid_include_rows:
        problems.append(
            "Invalid Include in MVP values at rows: "
            + ", ".join(map(str, invalid_include_rows[:10]))
        )
    if invalid_reason_rows:
        problems.append(
            "Invalid MVP Selection Reason values at rows: "
            + ", ".join(map(str, invalid_reason_rows[:10]))
        )
    if reason_while_false_rows:
        problems.append(
            "Selection reason present while Include in MVP is FALSE at rows: "
            + ", ".join(map(str, reason_while_false_rows[:10]))
        )

    before_true_count = 0
    before_include_positions = header_positions(before.headers, INCLUDE_HEADER)
    if before_include_positions:
        original_include_index = before_include_positions[0] - 1
        before_true_count = sum(
            cell_at(before.values, row_number - 1, original_include_index)
            .strip()
            .upper()
            == "TRUE"
            for row_number in before.existing_row_numbers
        )
    after_true_count = sum(
        cell_at(after.values, row_number - 1, include_index).strip().upper() == "TRUE"
        for row_number in before.existing_row_numbers
    )
    no_new_true_values = after_true_count == before_true_count
    if not no_new_true_values:
        problems.append("The number of TRUE Include in MVP values changed.")

    if plan.used_legacy_anchor:
        legacy_after = [
            after.headers.index(header) for header in LEGACY_HEADERS
        ]
        first_legacy_index = min(legacy_after)
        placement_ok = (
            include_index == first_legacy_index - 2
            and reason_index == first_legacy_index - 1
        )
    else:
        nonempty_header_indexes = [
            index for index, header in enumerate(after.headers) if header
        ]
        placement_ok = (
            include_index == nonempty_header_indexes[-2]
            and reason_index == nonempty_header_indexes[-1]
        )
    if not placement_ok:
        problems.append("The Map MVP columns are not at the planned final position.")

    backup_count = sum(
        candidate.title == BACKUP_TAB for candidate in spreadsheet.worksheets()
    )
    backup_exists_once = backup_count == 1
    if not backup_exists_once:
        problems.append(f"Expected one backup worksheet, found {backup_count}.")

    checks = {
        "headers_exist_exactly_once": exact_headers,
        "row_count_unchanged": row_count_unchanged,
        "restaurant_data_alignment_preserved": data_alignment_ok,
        "existing_nonempty_control_values_preserved": (
            existing_nonempty_controls_preserved
        ),
        "all_include_values_valid": not invalid_include_rows,
        "all_reason_values_valid": not invalid_reason_rows,
        "no_reason_while_include_false": not reason_while_false_rows,
        "no_new_true_values": no_new_true_values,
        "column_placement_correct": placement_ok,
        "backup_exists_exactly_once": backup_exists_once,
    }
    return checks, problems, after


def print_results(
    backup_created: bool,
    plan: InsertionPlan,
    initialized_count: int,
    checks: dict[str, bool],
    problems: list[str],
    after: SheetSnapshot,
) -> None:
    print(f"Backup tab: {BACKUP_TAB}")
    print(f"Backup status: {'created' if backup_created else 'already existed'}")
    print(f"Placement: {plan.assumption}")
    print(
        "Columns inserted: "
        + (", ".join(plan.headers_to_insert) if plan.headers_to_insert else "none")
    )
    for header in TARGET_HEADERS + LEGACY_HEADERS:
        positions = header_positions(after.headers, header)
        print(
            f"Final header position - {header}: "
            + (", ".join(map(str, positions)) if positions else "not found")
        )
    print(f"Rows initialized to FALSE: {initialized_count}")
    print(f"Final source row count: {after.grid_row_count}")
    print("Verification:")
    for check, passed in checks.items():
        print(f"  {'PASS' if passed else 'FAIL'} {check}")
    if problems:
        print("Issues:")
        for problem in problems:
            print(f"  - {problem}")


def main() -> int:
    try:
        spreadsheet, worksheet = connect_spreadsheet()
        before = capture_snapshot(worksheet)
        plan = make_insertion_plan(before)

        _, backup_created = ensure_backup(spreadsheet, worksheet, before)
        worksheet = insert_missing_columns(spreadsheet, worksheet, plan)
        initialized_count = initialize_include_false(
            worksheet,
            before.existing_row_numbers,
        )
        apply_validation(spreadsheet, worksheet)

        checks, problems, after = verify_migration(
            spreadsheet,
            worksheet,
            before,
            plan,
        )
        print_results(
            backup_created,
            plan,
            initialized_count,
            checks,
            problems,
            after,
        )
        return 0 if all(checks.values()) else 1
    except KeyError as error:
        print(
            f"Configuration error: missing environment variable {error.args[0]}",
            file=sys.stderr,
        )
    except Exception as error:
        print(f"Migration failed: {type(error).__name__}: {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
