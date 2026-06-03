import os
import gspread
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials

load_dotenv()

CREDS_FILE = os.environ["GOOGLE_SHEETS_CREDENTIALS"]
SHEET_ID = os.environ["GOOGLE_SHEET_ID"]
TAB_NAME = os.environ.get("GOOGLE_SHEET_TAB", "Restaurants")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

credentials = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPES)
client = gspread.authorize(credentials)

spreadsheet = client.open_by_key(SHEET_ID)
worksheet = spreadsheet.worksheet(TAB_NAME)

headers = worksheet.row_values(1)
header_index = {h: i + 1 for i, h in enumerate(headers)}

test_name = "TEST_RESTAURANT_DO_NOT_KEEP"

# Find existing row by Name
name_col = header_index["Name"]
matches = worksheet.findall(test_name, in_column=name_col)

if matches:
    row_num = matches[0].row
    print(f"Found existing test row at row {row_num}, updating it.")
else:
    print("Appending new test row...")
    values = [""] * len(headers)
    values[name_col - 1] = test_name
    worksheet.append_row(values, value_input_option="USER_ENTERED")

    # Find it again after append
    matches = worksheet.findall(test_name, in_column=name_col)
    if not matches:
        raise RuntimeError("Could not find appended test row.")
    row_num = matches[0].row
    print(f"Appended test row at row {row_num}.")

# Update a few cells
worksheet.update_cell(row_num, header_index["Status"], "active")
worksheet.update_cell(row_num, header_index["City"], "Paris")
worksheet.update_cell(row_num, header_index["Latitude"], 48.8566)
worksheet.update_cell(row_num, header_index["Longitude"], 2.3522)
worksheet.update_cell(row_num, header_index["Cuisine"], "french")
worksheet.update_cell(row_num, header_index["Vibe"], "casual")
worksheet.update_cell(row_num, header_index["Features"], "takeaway")

print("Write test completed.")