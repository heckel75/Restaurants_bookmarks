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

print("Connected successfully.")
print("Rows:", worksheet.row_count)
print("Cols:", worksheet.col_count)
print("Headers:", worksheet.row_values(1))