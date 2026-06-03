import os
import time
import random
import gspread
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
from gspread.exceptions import APIError
from requests.exceptions import ConnectionError, Timeout

load_dotenv()

SHEETS_CREDS_FILE = os.environ["GOOGLE_SHEETS_CREDENTIALS"]
SHEET_ID = os.environ["GOOGLE_SHEET_ID"]
SHEET_TAB = os.environ.get("GOOGLE_SHEET_TAB", "Restaurants")

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def get_worksheet():
    credentials = Credentials.from_service_account_file(
        SHEETS_CREDS_FILE, scopes=SCOPES
    )
    client = gspread.authorize(credentials)
    spreadsheet = client.open_by_key(SHEET_ID)
    return spreadsheet.worksheet(SHEET_TAB)


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
                wait = min(60, (2 ** attempt) + random.uniform(0, 1))
                print(f"Rate limited. Waiting {wait:.1f}s...")
                time.sleep(wait)
                continue
            raise
        except (ConnectionError, Timeout):
            wait = min(60, (2 ** attempt) + random.uniform(0, 1))
            print(f"Network error. Waiting {wait:.1f}s...")
            time.sleep(wait)
            continue
    raise RuntimeError("Failed to update row after retries")


def main():
    ws = get_worksheet()
    all_values = ws.get_all_values()
    headers = all_values[0]
    rows = all_values[1:]

    idx = {h: i for i, h in enumerate(headers)}

    required = ["Needs Review", "Google Place ID", "Status", "Confidence"]
    for col in required:
        if col not in idx:
            raise RuntimeError(f"Missing column: {col}")

    updated = 0

    for sheet_row_num, row in enumerate(rows, start=2):
        row = row + [""] * (len(headers) - len(row))

        needs_review = row[idx["Needs Review"]].strip()
        place_id = row[idx["Google Place ID"]].strip()
        status = row[idx["Status"]].strip().lower()
        confidence = row[idx["Confidence"]].strip().lower()

        if needs_review:
            continue

        # Rule:
        # if it has a Google Place ID and is not to_review => FALSE
        # otherwise => TRUE
        if place_id and status != "to_review":
            row[idx["Needs Review"]] = "FALSE"
        else:
            row[idx["Needs Review"]] = "TRUE"

        # Optional cleanup of blanks
        if not confidence:
            row[idx["Confidence"]] = "high" if place_id else "low"
        if not status:
            row[idx["Status"]] = "active" if place_id else "to_review"

        end_col = column_number_to_letter(len(headers))
        range_name = f"A{sheet_row_num}:{end_col}{sheet_row_num}"
        safe_update_row(ws, range_name, row)

        updated += 1
        print(f"Updated row {sheet_row_num}")
        time.sleep(1.2)

    print(f"Done. Backfilled {updated} rows.")


if __name__ == "__main__":
    main()