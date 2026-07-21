# test_checkin.py
from app import db

# Test check-in
user_data = db.get_user_by_username('john')
if user_data:
    print(f"Testing check-in for {user_data['Full Name']}")
    
    # Check current status
    status = db.get_user_working_hours_today('john')
    print(f"Current status: {status['status']}")
    print(f"Check-in time: {status['check_in']}")
    print(f"Check-out time: {status['check_out']}")
    
    # Check today's record
    record = db.get_today_attendance_record('john')
    if record:
        print(f"\nToday's record found:")
        print(f"  Row index: {record['row_index']}")
        print(f"  Check-in: {record['check_in_time']}")
        print(f"  Check-out: {record['check_out_time']}")
        print(f"  Working hours: {record['working_hours']}")
    else:
        print("No record for today")