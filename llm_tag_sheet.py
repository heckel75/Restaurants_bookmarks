import os

import gspread
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
from datetime import datetime
from zoneinfo import ZoneInfo

from website_text import fetch_website_text
from llm_tagger import tag_restaurant, result_to_sheet_values


load_dotenv()

SHEETS_CREDS_FILE = os.environ["GOOGLE_SHEETS_CREDENTIALS"]
SHEET_ID = os.environ["GOOGLE_SHEET_ID"]
SHEET_TAB = os.environ.get("GOOGLE_SHEET_TAB", "Restaurants")

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

LLM_MAX_ROWS = int(os.environ.get("LLM_MAX_ROWS", "10"))
LLM_OVERWRITE_EXISTING = os.environ.get("LLM_OVERWRITE_EXISTING", "false").lower() == "true"

DRY_RUN = True


REQUIRED_HEADERS = [
    "Name",
    "Google Place ID",
    "Status",
    "Needs Review",
    "Address",
    "City",
    "Postal Code",
    "Arrondissement",
    "Town",
    "Website",
    "Instagram",
    "Facebook",
    "Delivery",
    "Takeaway",
    "Cuisine",
    "Vibe",
    "Features",
    "LLM Confidence",
    "LLM Evidence",
    "LLM Tagged at",
    "LLM Model",
    "LLM Review Needed",
]


def connect_sheet():
    creds = Credentials.from_service_account_file(SHEETS_CREDS_FILE, scopes=SCOPES)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(SHEET_ID)
    return spreadsheet.worksheet(SHEET_TAB)


def is_blank(value) -> bool:
    return str(value or "").strip() == ""


def get_cell(row: dict, field: str) -> str:
    return str(row.get(field, "") or "").strip()


def is_eligible(row: dict) -> bool:
    if get_cell(row, "Needs Review").upper() != "FALSE":
        return False

    if get_cell(row, "Status") != "active":
        return False

    if is_blank(get_cell(row, "Google Place ID")):
        return False

    if not LLM_OVERWRITE_EXISTING and not is_blank(get_cell(row, "LLM Tagged at")):
        return False

    if not LLM_OVERWRITE_EXISTING:
        has_empty_tag_field = (
            is_blank(get_cell(row, "Cuisine"))
            or is_blank(get_cell(row, "Vibe"))
            or is_blank(get_cell(row, "Features"))
        )
        if not has_empty_tag_field:
            return False

    return True


def build_updates(row: dict, sheet_values: dict) -> dict:
    updates = {}

    llm_confidence = str(sheet_values.get("LLM Confidence", "") or "").strip().lower()
    llm_review_needed = str(sheet_values.get("LLM Review Needed", "") or "").strip().upper()

    allow_main_tags = (
        llm_confidence != "low"
        and llm_review_needed != "TRUE"
    )

    for field, value in sheet_values.items():
        current_value = get_cell(row, field)

        if field in ["Cuisine", "Vibe", "Features"]:
            if not allow_main_tags:
                continue

            if is_blank(value):
                continue

            if LLM_OVERWRITE_EXISTING or is_blank(current_value):
                updates[field] = value

        elif field in ["Delivery", "Takeaway"]:
            if LLM_OVERWRITE_EXISTING or current_value in ["", "UNKNOWN"]:
                updates[field] = value

        else:
            updates[field] = value

    return updates


def write_updates(worksheet, header: list[str], sheet_row_number: int, updates: dict) -> None:
    for field, value in updates.items():
        if field not in header:
            raise RuntimeError(f"Header not found while writing: {field}")

        col_number = header.index(field) + 1
        worksheet.update_cell(sheet_row_number, col_number, value)


def main():
    worksheet = connect_sheet()

    header = worksheet.row_values(1)
    missing_headers = [name for name in REQUIRED_HEADERS if name not in header]

    if missing_headers:
        raise RuntimeError(f"Missing required headers: {missing_headers}")

    rows = worksheet.get_all_records()
    eligible = []

    for index, row in enumerate(rows, start=2):
        if is_eligible(row):
            eligible.append((index, row))

        if len(eligible) >= LLM_MAX_ROWS:
            break

    print(f"Eligible rows found for this run: {len(eligible)}")
    print(f"LLM_MAX_ROWS: {LLM_MAX_ROWS}")
    print(f"LLM_OVERWRITE_EXISTING: {LLM_OVERWRITE_EXISTING}")
    print(f"DRY_RUN: {DRY_RUN}")

    for sheet_row_number, row in eligible:
        print("\n" + "=" * 80)
        print(f"Row {sheet_row_number}: {get_cell(row, 'Name')}")
        print(f"Website: {get_cell(row, 'Website')}")

        website_text = fetch_website_text(
            get_cell(row, "Website"),
            target_name=get_cell(row, "Name"),
            target_address=get_cell(row, "Address"),
        )
        print(f"Website text characters: {len(website_text)}")

        row_for_llm = dict(row)
        row_for_llm["_website_text"] = website_text

        result = tag_restaurant(row_for_llm)
        sheet_values = result_to_sheet_values(result)
        sheet_values["LLM Tagged at"] = datetime.now(ZoneInfo("Europe/Paris")).isoformat(timespec="seconds")

        updates = build_updates(row, sheet_values)

        print("Proposed updates:")
        for field, value in updates.items():
            print(f"- {field}: {value}")

        if not DRY_RUN:
            write_updates(worksheet, header, sheet_row_number, updates)
            print("Written to sheet.")


if __name__ == "__main__":
    main()
