# fix_attendance_sheet.py
import gspread
from oauth2client.service_account import ServiceAccountCredentials

def fix_attendance_sheet():
    """Fix the attendance sheet structure"""
    
    scope = ['https://spreadsheets.google.com/feeds',
             'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
    client = gspread.authorize(creds)
    
    try:
        sheet = client.open('Attendance System')
        print("✅ Connected to spreadsheet")
    except:
        print("❌ Cannot find spreadsheet")
        return
    
    try:
        # Delete old attendance sheet if it exists
        try:
            old_sheet = sheet.worksheet("Attendance")
            sheet.del_worksheet(old_sheet)
            print("✅ Deleted old Attendance sheet")
        except:
            print("ℹ️ No existing Attendance sheet found")
        
        # Create new attendance sheet with proper headers
        attendance_sheet = sheet.add_worksheet("Attendance", 2000, 12)
        
        # Add headers
        headers = [
            'Date',
            'Username', 
            'Full Name', 
            'Email', 
            'Department',
            'Check-In Time', 
            'Check-Out Time', 
            'Working Hours',
            'IP Address', 
            'City', 
            'Country', 
            'Work Type'
        ]
        
        attendance_sheet.insert_row(headers, 1)
        
        # Format the header row (make it bold and colored)
        attendance_sheet.format('A1:L1', {
            "backgroundColor": {"red": 0.4, "green": 0.4, "blue": 0.8},
            "textFormat": {"bold": True, "foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0}},
            "horizontalAlignment": "CENTER"
        })
        
        # Freeze the header row
        attendance_sheet.freeze(rows=1)
        
        # Note: gspread doesn't support direct column width setting easily
        # Column widths will be auto-adjusted by Google Sheets
        
        print("✅ Created new Attendance sheet with proper headers")
        print("\n📋 Headers added:")
        for i, header in enumerate(headers, 1):
            print(f"   Column {i}: {header}")
        
        print("\n✅ Sheet is ready to use!")
        print("🔗 Open your Google Sheet to verify: https://sheets.google.com")
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")

if __name__ == '__main__':
    fix_attendance_sheet()