import os
import subprocess
import sys


def maybe_reexec_with_venv():
    repo_dir = os.path.dirname(os.path.abspath(__file__))
    venv_python = os.path.join(repo_dir, ".venv", "Scripts", "python.exe")

    if os.name != "nt" or not os.path.exists(venv_python):
        return

    if os.path.abspath(sys.executable).lower() == os.path.abspath(venv_python).lower():
        return

    completed = subprocess.run([venv_python] + sys.argv)
    sys.exit(completed.returncode)


maybe_reexec_with_venv()

import gspread
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials


SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def get_worksheet():
    load_dotenv()

    creds_file = os.environ["GOOGLE_SHEETS_CREDENTIALS"]
    sheet_id = os.environ["GOOGLE_SHEET_ID"]
    sheet_tab = os.environ.get("GOOGLE_SHEET_TAB", "Restaurants")

    credentials = Credentials.from_service_account_file(creds_file, scopes=SCOPES)
    client = gspread.authorize(credentials)
    spreadsheet = client.open_by_key(sheet_id)
    return spreadsheet.worksheet(sheet_tab)


def get_value(row, header_index, field_name):
    idx = header_index.get(field_name)

    if idx is None or idx >= len(row):
        return ""

    return (row[idx] or "").strip()


def has_any(row, header_index, fields):
    return any(get_value(row, header_index, field) for field in fields)


def website_instagram_presence(row, header_index):
    website = bool(get_value(row, header_index, "Website"))
    instagram = bool(get_value(row, header_index, "Instagram"))

    if website and instagram:
        return "Website+Instagram"

    if website:
        return "Website"

    if instagram:
        return "Instagram"

    return ""


def row_record(sheet_row_num, row, header_index):
    return {
        "row": sheet_row_num,
        "name": get_value(row, header_index, "Name"),
        "arrondissement": get_value(row, header_index, "Arrondissement"),
        "town": get_value(row, header_index, "Town"),
        "presence": website_instagram_presence(row, header_index),
        "review_reason": get_value(row, header_index, "Review Reason"),
    }


def print_rows(title, rows):
    print(title)

    if not rows:
        print("(none)")
        print()
        return

    for item in rows:
        print(
            "\t".join(
                [
                    str(item["row"]),
                    item["name"],
                    item["arrondissement"],
                    item["town"],
                    item["presence"],
                    item["review_reason"],
                ]
            )
        )

    print()


def main():
    worksheet = get_worksheet()
    values = worksheet.get_all_values()

    if not values:
        raise RuntimeError("Google Sheet is empty.")

    headers = values[0]
    rows = values[1:]
    header_index = {header: idx for idx, header in enumerate(headers)}

    review_targets = []
    skip_targets = []

    for sheet_row_num, row in enumerate(rows, start=2):
        needs_review = get_value(row, header_index, "Needs Review").upper()
        place_id = get_value(row, header_index, "Google Place ID")
        status = get_value(row, header_index, "Status").lower()
        name = get_value(row, header_index, "Name")
        review_reason = get_value(row, header_index, "Review Reason").lower()

        if (
            needs_review == "TRUE"
            and not place_id
            and status in {"active", "to_review"}
            and name
            and has_any(row, header_index, ["Arrondissement", "Town"])
            and review_reason != "missing_location_hint"
        ):
            review_targets.append(row_record(sheet_row_num, row, header_index))

        if needs_review == "FALSE" and place_id:
            skip_targets.append(row_record(sheet_row_num, row, header_index))

    review_targets.sort(key=lambda item: (item["presence"] == "", item["row"]))

    print_rows("Matching-test target rows", review_targets[:20])
    print_rows("Validated rows for TARGET_ROW skip testing", skip_targets[:10])


if __name__ == "__main__":
    main()
