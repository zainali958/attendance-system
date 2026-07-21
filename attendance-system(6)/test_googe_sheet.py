import gspread
from oauth2client.service_account import ServiceAccountCredentials

def test_connection():
    try:
        scope = ['https://spreadsheets.google.com/feeds',
                 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
        client = gspread.authorize(creds)
        
        # Try to open or create sheet
        try:
            sheet = client.open('Attendance Records').sheet1
            print("✅ Successfully connected to Google Sheets!")
        except:
            sheet = client.create('Attendance Records').sheet1
            print("✅ Created new Attendance Records sheet!")
            
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == '__main__':
    test_connection()