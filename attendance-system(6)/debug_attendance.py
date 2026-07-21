# debug_attendance.py
import gspread
from oauth2client.service_account import ServiceAccountCredentials

def debug_sheet():
    scope = ['https://spreadsheets.google.com/feeds',
             'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
    client = gspread.authorize(creds)
    
    sheet = client.open('Attendance System')
    
    try:
        att_sheet = sheet.worksheet('Attendance')
        print("=" * 80)
        print("ATTENDANCE SHEET DATA:")
        print("=" * 80)
        
        # Get all values
        all_data = att_sheet.get_all_values()
        
        if len(all_data) > 0:
            print(f"Total rows: {len(all_data)}")
            print(f"Headers: {all_data[0]}")
            print("-" * 80)
            
            for i, row in enumerate(all_data[1:], start=2):
                print(f"Row {i}: {row}")
        else:
            print("Sheet is empty!")
            
    except Exception as e:
        print(f"Error: {str(e)}")

if __name__ == '__main__':
    debug_sheet()