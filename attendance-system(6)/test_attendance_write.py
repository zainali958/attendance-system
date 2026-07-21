"""
Standalone diagnostic script to test writing to the Attendance sheet,
independent of the Flask app. Run this from the same folder as app.py
(so it can find credentials.json and .env).

    python test_attendance_write.py
"""

import os
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from dotenv import load_dotenv

load_dotenv()

SCOPE = [
    'https://spreadsheets.google.com/feeds',
    'https://www.googleapis.com/auth/drive'
]

sheet_name = os.getenv('GOOGLE_SHEET_NAME', 'Attendance System')

print(f"1. Looking for spreadsheet named: '{sheet_name}'")
print("   (this comes from GOOGLE_SHEET_NAME in your .env, or the default)\n")

creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', SCOPE)
client = gspread.authorize(creds)

try:
    spreadsheet = client.open(sheet_name)
    print(f"2. ✅ Opened spreadsheet: '{spreadsheet.title}'")
    print(f"   URL: {spreadsheet.url}\n")
except gspread.SpreadsheetNotFound:
    print(f"2. ❌ No spreadsheet named '{sheet_name}' is visible to the service account.")
    print("   -> Either the name doesn't match, or it hasn't been shared with the")
    print("      service account's client_email (check credentials.json).")
    raise SystemExit(1)

# List every tab in the spreadsheet
worksheets = spreadsheet.worksheets()
print("3. Tabs found in this spreadsheet:")
for ws in worksheets:
    print(f"   - '{ws.title}'  ({ws.row_count} rows x {ws.col_count} cols)")
print()

# Check specifically for the "Attendance" tab the app expects
target_tab = "Attendance"
matching = [ws for ws in worksheets if ws.title == target_tab]

if not matching:
    print(f"4. ❌ No tab named exactly '{target_tab}' exists yet.")
    print("   The app would auto-create one on next run. If you expected your")
    print("   manually-created tab to be used, rename it to exactly 'Attendance'.\n")
    raise SystemExit(1)

attendance_sheet = matching[0]
print(f"4. ✅ Found the '{target_tab}' tab. Current headers:")
headers = attendance_sheet.row_values(1)
print(f"   {headers}\n")

# ---- Sample data set -------------------------------------------------
# Add/edit rows here to generate more test records at once.
# Each entry: (username, full_name, email, department, ip, city, country, work_type, status)
SAMPLE_USERS = [
    ('jdoe',    'John Doe',    'jdoe@example.com',    'Engineering', '192.168.1.10', 'Lahore',    'Pakistan', 'Office',          'Present'),
    ('asmith',  'Alice Smith', 'asmith@example.com',  'Marketing',   '192.168.1.11', 'Karachi',   'Pakistan', 'Work From Home',  'Present'),
    ('mkhan',   'Musa Khan',   'mkhan@example.com',   'Sales',       '192.168.1.12', 'Islamabad', 'Pakistan', 'Office',          'Present'),
    ('rgreen',  'Rita Green',  'rgreen@example.com',  'HR',          '192.168.1.13', 'Rawalpindi','Pakistan', 'Work From Home',  'Late'),
    ('tuser',   'Tom User',    'tuser@example.com',   'Finance',     '192.168.1.14', 'Lahore',    'Pakistan', 'Office',          'Present'),
]

NUM_SAMPLES = 5  # how many rows to write from the list above (max = len(SAMPLE_USERS))

print(f"5. Writing {NUM_SAMPLES} sample rows...")
rows_to_write = []
for username, full_name, email, dept, ip, city, country, work_type, status in SAMPLE_USERS[:NUM_SAMPLES]:
    rows_to_write.append([
        datetime.now().strftime('%Y-%m-%d'),
        datetime.now().strftime('%H:%M:%S'),
        username,
        full_name,
        email,
        dept,
        ip,
        city,
        country,
        work_type,
        status
    ])

# append_rows (plural) writes them all in a single API call — faster and
# avoids hitting Google Sheets' per-minute write quota when testing many rows.
attendance_sheet.append_rows(rows_to_write)
print(f"   {len(rows_to_write)} rows sent, no exception raised.\n")

# Read back to confirm they actually landed
print("6. Reading back the last rows to confirm...")
all_values = attendance_sheet.get_all_values()
last_rows = all_values[-len(rows_to_write):]
for row in last_rows:
    print(f"   {row}")

written_usernames = {r[2] for r in rows_to_write}
found_usernames = {r[2] for r in last_rows}

if written_usernames == found_usernames:
    print(f"\n✅ SUCCESS: all {len(rows_to_write)} rows were confirmed in the sheet.")
    print(f"   Go check tab '{target_tab}' in spreadsheet '{sheet_name}'.")
else:
    print("\n⚠️ Mismatch between what we wrote and what we read back.")
    print("   Something odd is happening (caching, wrong tab, or a race condition).")

print("\nTip: to remove these test rows afterward, just delete them manually in Google Sheets,")
print("or add a cleanup step here using attendance_sheet.delete_rows(row_number).")
