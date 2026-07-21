from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, date, timedelta
import pytz
import requests
import os
import json
from dotenv import load_dotenv
from functools import wraps
from cachetools import TTLCache
import threading

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'your-secret-key-here')

# Flask-Login setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Cache for better performance
user_cache = TTLCache(maxsize=500, ttl=300)
attendance_cache = TTLCache(maxsize=1000, ttl=60)

# Serializes writes to the Users sheet. The server runs with threaded=True,
# so without this lock, two near-simultaneous "Add User" requests can both
# read the same "next empty row" before either has written, and the second
# one silently overwrites the first (this is what caused the Nick/Ahmed
# overwrite bug even after switching to an explicit row calculation).
users_sheet_lock = threading.Lock()

# ==================== Google Sheets Database Class ====================

class GoogleSheetsDB:
    """Google Sheets Database Handler"""
    
    def __init__(self):
        self.scope = ['https://spreadsheets.google.com/feeds',
                      'https://www.googleapis.com/auth/drive']
        self.creds = None
        self.client = None
        self.spreadsheet = None
        self.users_sheet = None
        self.attendance_sheet = None
        self._connect()
    
    def _connect(self):
        """Establish connection to Google Sheets"""
        try:
            creds_json = os.getenv('GOOGLE_CREDENTIALS_JSON')
            if creds_json:
                # Deployed environments (Render, Railway, etc.) usually can't
                # take an uploaded file, so the whole credentials.json content
                # is passed as one environment variable instead.
                creds_dict = json.loads(creds_json)
                self.creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, self.scope)
            else:
                # Local dev / hosts that do support secret files
                self.creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', self.scope)
            self.client = gspread.authorize(self.creds)
            
            sheet_name = os.getenv('GOOGLE_SHEET_NAME', 'Attendance System')
            try:
                self.spreadsheet = self.client.open(sheet_name)
            except gspread.SpreadsheetNotFound:
                self.spreadsheet = self.client.create(sheet_name)
                print(f"Created new spreadsheet: {sheet_name}")
            
            self._init_users_sheet()
            self._init_attendance_sheet()
            
        except Exception as e:
            print(f"Error connecting to Google Sheets: {str(e)}")
            raise
    
    def _init_users_sheet(self):
        """Initialize Users sheet"""
        try:
            self.users_sheet = self.spreadsheet.worksheet("Users")
        except gspread.WorksheetNotFound:
            self.users_sheet = self.spreadsheet.add_worksheet("Users", 1000, 9)
            headers = ['Username', 'Password', 'Full Name', 'Email', 'Department', 
                      'IP Address', 'Designation', 'Phone', 'Is Active', 'Is Admin']
            self.users_sheet.insert_row(headers, 1)
            self.add_user('admin', 'admin123', 'Administrator', 'admin@company.com', 
                         'IT', 'System Admin', '', True, True)
    
    def _init_attendance_sheet(self):
        """Initialize Attendance sheet with Check-In/Check-Out columns"""
        try:
            self.attendance_sheet = self.spreadsheet.worksheet("Attendance")
        except gspread.WorksheetNotFound:
            self.attendance_sheet = self.spreadsheet.add_worksheet("Attendance", 2000, 12)
            headers = ['Date', 'Username', 'Full Name', 'Email', 'Department',
                      'Check-In Time', 'Check-Out Time', 'Working Hours',
                      'IP Address', 'City', 'Country', 'Work Type']
            self.attendance_sheet.insert_row(headers, 1)
            print("Created Attendance sheet with Check-In/Check-Out columns")
    
    # ==================== User Operations ====================
    
    def get_all_users(self):
        """Get all users from sheet"""
        cache_key = 'all_users'
        if cache_key in user_cache:
            return user_cache[cache_key]
        
        try:
            records = self.users_sheet.get_all_records()
            users = [user for user in records if str(user.get('Is Active', 'TRUE')).upper() == 'TRUE']
            user_cache[cache_key] = users
            return users
        except Exception as e:
            print(f"Error fetching users: {str(e)}")
            return []
    
    def get_user_by_username(self, username, use_cache=True):
        """Get specific user by username. Set use_cache=False for security-sensitive
        checks (e.g. login) so a direct sheet edit takes effect immediately instead
        of waiting for the 5-minute cache to expire."""
        if use_cache:
            users = self.get_all_users()
        else:
            try:
                records = self.users_sheet.get_all_records()
                users = [user for user in records if str(user.get('Is Active', 'TRUE')).upper() == 'TRUE']
            except Exception as e:
                print(f"Error fetching users: {str(e)}")
                return None
        for user in users:
            if user['Username'] == username:
                return user
        return None
    
    def add_user(self, username, password, full_name, email, department='', 
                 designation='', phone='', is_active=True, is_admin=False, ip_address=''):
        """Add a new user"""
        try:
            # Hold the lock for the ENTIRE check-then-write sequence, not just
            # the write. Otherwise two requests can both pass the "does this
            # username exist" check and both compute the same next_row before
            # either one writes.
            with users_sheet_lock:
                existing = self.get_user_by_username(username)
                if existing:
                    return False, "Username already exists"
                
                password_hash = generate_password_hash(password)
                
                new_row = [
                    username,
                    password_hash,
                    full_name,
                    email,
                    department,
                    ip_address,
                    designation,
                    phone,
                    str(is_active).upper(),
                    str(is_admin).upper()
                ]
                
                # Explicitly compute the next empty row instead of relying on the
                # Sheets API's auto-detected "last row" — that heuristic gets confused
                # by the Employee_Directory Table object's fixed boundary and can
                # silently overwrite the last existing row instead of appending a new one.
                next_row = len(self.users_sheet.get_all_values()) + 1
                self.users_sheet.update(f'A{next_row}', [new_row])
            user_cache.clear()
            
            return True, "User added successfully"
        except Exception as e:
            return False, f"Error adding user: {str(e)}"
    
    def verify_password(self, username, password):
        """Verify user password"""
        all_users = self.users_sheet.get_all_values()
        
        for row in all_users[1:]:
            if row[0] == username:
                stored_hash = row[1]
                return check_password_hash(stored_hash, password)
        
        return False
    
    def update_password(self, username, new_password):
        """Update an existing user's password (admin action)"""
        try:
            with users_sheet_lock:
                all_values = self.users_sheet.get_all_values()
                for idx, row in enumerate(all_values[1:], start=2):  # row 1 is headers
                    if row[0] == username:
                        password_hash = generate_password_hash(new_password)
                        self.users_sheet.update(f'B{idx}', [[password_hash]])
                        user_cache.clear()
                        return True, f"Password updated for {username}"
            return False, "User not found"
        except Exception as e:
            return False, f"Error updating password: {str(e)}"

    def update_ip_address(self, username, new_ip):
        """Update (or clear) an existing user's registered IP address (admin action).
        new_ip='' removes the IP restriction, letting the user log in from anywhere."""
        try:
            with users_sheet_lock:
                all_values = self.users_sheet.get_all_values()
                for idx, row in enumerate(all_values[1:], start=2):  # row 1 is headers
                    if row[0] == username:
                        self.users_sheet.update(f'F{idx}', [[new_ip]])
                        user_cache.clear()
                        if new_ip:
                            return True, f"IP address for {username} updated to {new_ip}"
                        return True, f"IP restriction removed for {username}"
            return False, "User not found"
        except Exception as e:
            return False, f"Error updating IP address: {str(e)}"
    
    # ==================== Attendance Operations ====================
    
    def get_today_attendance_record(self, username):
        """Get today's attendance record for a user"""
        today = date.today().strftime('%Y-%m-%d')
        
        try:
            attendance_records = self.attendance_sheet.get_all_values()
            
            # Find the row index (1-based) for today's record
            for i, row in enumerate(attendance_records[1:], start=2):
                if row[0] == today and row[1] == username:
                    return {
                        'row_index': i,
                        'date': row[0],
                        'username': row[1],
                        'full_name': row[2],
                        'email': row[3] if len(row) > 3 else '',
                        'department': row[4] if len(row) > 4 else '',
                        'check_in_time': row[5] if len(row) > 5 else '',
                        'check_out_time': row[6] if len(row) > 6 else '',
                        'working_hours': row[7] if len(row) > 7 else '',
                        'ip_address': row[8] if len(row) > 8 else '',
                        'city': row[9] if len(row) > 9 else '',
                        'country': row[10] if len(row) > 10 else '',
                        'work_type': row[11] if len(row) > 11 else ''
                    }
            return None
        except Exception as e:
            print(f"Error getting today's record: {str(e)}")
            return None
    
    def check_in(self, user_data, ip_address, location_info):
        """Record check-in time"""
        try:
            # Check if already checked in today
            existing_record = self.get_today_attendance_record(user_data['Username'])
            
            if existing_record and existing_record['check_in_time']:
                return False, "You have already checked in today"
            
            timezone = pytz.timezone(os.getenv('TIMEZONE', 'UTC'))
            current_time = datetime.now(timezone)
            
            date_str = current_time.strftime('%Y-%m-%d')
            time_str = current_time.strftime('%H:%M:%S')
            
            work_type = 'Office' if location_info.get('is_office', False) else 'Work From Home'
            
            if existing_record:
                # Update existing record with check-in time
                self.attendance_sheet.update_cell(existing_record['row_index'], 6, time_str)
                self.attendance_sheet.update_cell(existing_record['row_index'], 9, ip_address)
                self.attendance_sheet.update_cell(existing_record['row_index'], 10, location_info.get('city', 'Unknown'))
                self.attendance_sheet.update_cell(existing_record['row_index'], 11, location_info.get('country', 'Unknown'))
                self.attendance_sheet.update_cell(existing_record['row_index'], 12, work_type)
            else:
                # Create new attendance record
                new_row = [
                    date_str,
                    user_data['Username'],
                    user_data['Full Name'],
                    user_data.get('Email', ''),
                    user_data.get('Department', ''),
                    time_str,  # Check-In Time
                    '',  # Check-Out Time (empty initially)
                    '',  # Working Hours (calculated later)
                    ip_address,
                    location_info.get('city', 'Unknown'),
                    location_info.get('country', 'Unknown'),
                    work_type
                ]
                self.attendance_sheet.append_row(new_row)
            
            # Clear cache
            self._clear_attendance_cache()
            
            return True, f"Check-in recorded at {time_str}"
        
        except Exception as e:
            return False, f"Error recording check-in: {str(e)}"
    
    def check_out(self, username):
        """Record check-out time and calculate working hours"""
        try:
            # Get today's record
            existing_record = self.get_today_attendance_record(username)
            
            if not existing_record:
                return False, "No check-in record found for today. Please check-in first."
            
            if not existing_record['check_in_time']:
                return False, "Please check-in first before checking out."
            
            if existing_record['check_out_time']:
                return False, "You have already checked out today"
            
            timezone = pytz.timezone(os.getenv('TIMEZONE', 'UTC'))
            current_time = datetime.now(timezone)
            time_str = current_time.strftime('%H:%M:%S')
            
            # Calculate working hours
            check_in_time = datetime.strptime(existing_record['check_in_time'], '%H:%M:%S')
            check_out_time = datetime.strptime(time_str, '%H:%M:%S')
            
            # Handle case where check-out is on next day (night shift)
            if check_out_time < check_in_time:
                check_out_time += timedelta(days=1)
            
            time_diff = check_out_time - check_in_time
            
            # Convert to hours and minutes
            total_seconds = time_diff.total_seconds()
            hours = int(total_seconds // 3600)
            minutes = int((total_seconds % 3600) // 60)
            
            working_hours_str = f"{hours}h {minutes}m"
            
            # Update the record
            self.attendance_sheet.update_cell(existing_record['row_index'], 7, time_str)
            self.attendance_sheet.update_cell(existing_record['row_index'], 8, working_hours_str)
            
            # Clear cache
            self._clear_attendance_cache()
            
            return True, f"Check-out recorded at {time_str}. Working hours: {working_hours_str}"
        
        except Exception as e:
            return False, f"Error recording check-out: {str(e)}"
    
    def get_user_working_hours_today(self, username):
        """Get today's working hours for a user"""
        record = self.get_today_attendance_record(username)
        if record:
            return {
                'check_in': record['check_in_time'] if record['check_in_time'] else None,
                'check_out': record['check_out_time'] if record['check_out_time'] else None,
                'working_hours': record['working_hours'] if record['working_hours'] else None,
                'work_type': record['work_type'] if record['work_type'] else None,
                'status': 'checked_out' if record['check_out_time'] else ('checked_in' if record['check_in_time'] else 'not_checked_in')
            }
        return {'status': 'not_checked_in', 'check_in': None, 'check_out': None, 'working_hours': None, 'work_type': None}
    
    def get_user_attendance_history(self, username, days=30):
        """Get attendance history for a specific user"""
        try:
            all_records = self.attendance_sheet.get_all_records()
            user_records = [record for record in all_records if record['Username'] == username]
            
            # Sort by date (most recent first)
            user_records.sort(key=lambda x: x['Date'], reverse=True)
            
            # Calculate total working hours for the period
            total_hours = 0
            total_minutes = 0
            
            for record in user_records[:days]:
                if record.get('Working Hours'):
                    try:
                        hours_str = record['Working Hours']
                        if 'h' in hours_str and 'm' in hours_str:
                            parts = hours_str.replace('h', ' ').replace('m', '').split()
                            if len(parts) >= 2:
                                total_hours += int(parts[0])
                                total_minutes += int(parts[1])
                    except:
                        pass
            
            # Convert total minutes to hours
            total_hours += total_minutes // 60
            remaining_minutes = total_minutes % 60
            
            return {
                'records': user_records[:days],
                'total_working_hours': f"{total_hours}h {remaining_minutes}m",
                'days_present': len([r for r in user_records[:days] if r.get('Check-In Time')])
            }
        except Exception as e:
            print(f"Error fetching user history: {str(e)}")
            return {'records': [], 'total_working_hours': '0h 0m', 'days_present': 0}
    
    def get_today_all_attendance(self):
        """Get today's attendance records (for admin)"""
        today = date.today().strftime('%Y-%m-%d')
        
        try:
            all_records = self.attendance_sheet.get_all_records()
            today_records = [record for record in all_records if record['Date'] == today]
            return today_records
        except Exception as e:
            print(f"Error fetching today's attendance: {str(e)}")
            return []
    
    def get_attendance_by_date(self, filter_date):
        """Get attendance for a specific date"""
        try:
            all_records = self.attendance_sheet.get_all_records()
            date_records = [record for record in all_records if record['Date'] == filter_date]
            return date_records
        except Exception as e:
            print(f"Error fetching date attendance: {str(e)}")
            return []
    
    def get_attendance_stats(self, filter_date=None):
        """Get attendance statistics (for admin)"""
        if not filter_date:
            filter_date = date.today().strftime('%Y-%m-%d')
        
        try:
            if filter_date == date.today().strftime('%Y-%m-%d'):
                today_records = self.get_today_all_attendance()
            else:
                today_records = self.get_attendance_by_date(filter_date)
            
            total_users = len(self.get_all_users())
            
            checked_in = sum(1 for r in today_records if r.get('Check-In Time'))
            checked_out = sum(1 for r in today_records if r.get('Check-Out Time'))
            
            office_count = sum(1 for r in today_records if r.get('Work Type') == 'Office')
            wfh_count = sum(1 for r in today_records if r.get('Work Type') == 'Work From Home')
            
            return {
                'total_users': total_users,
                'checked_in': checked_in,
                'checked_out': checked_out,
                'not_checked_in': total_users - checked_in,
                'office': office_count,
                'wfh': wfh_count,
                'attendance_percentage': round((checked_in / total_users * 100), 2) if total_users > 0 else 0
            }
        except Exception as e:
            print(f"Error getting stats: {str(e)}")
            return {}
    
    def bulk_import_users(self, users_list):
        """Import multiple users at once"""
        try:
            rows_to_add = []
            for user in users_list:
                password_hash = generate_password_hash(user.get('password', 'Welcome123'))
                row = [
                    user['username'],
                    password_hash,
                    user['full_name'],
                    user.get('email', ''),
                    user.get('department', ''),
                    user.get('ip_address', ''),
                    user.get('designation', ''),
                    user.get('phone', ''),
                    str(user.get('is_active', True)).upper(),
                    str(user.get('is_admin', False)).upper()
                ]
                rows_to_add.append(row)
            
            if rows_to_add:
                with users_sheet_lock:
                    next_row = len(self.users_sheet.get_all_values()) + 1
                    self.users_sheet.update(f'A{next_row}', rows_to_add)
                user_cache.clear()
                return True, f"Successfully imported {len(rows_to_add)} users"
            
            return False, "No users to import"
        except Exception as e:
            return False, f"Error importing users: {str(e)}"
    
    def _clear_attendance_cache(self):
        """Clear attendance cache"""
        attendance_cache.clear()

# Initialize Google Sheets DB
db = GoogleSheetsDB()

# ==================== User Class for Flask-Login ====================

class User(UserMixin):
    def __init__(self, user_data):
        self.id = user_data['Username']
        self.username = user_data['Username']
        self.full_name = user_data['Full Name']
        self.email = user_data.get('Email', '')
        self.department = user_data.get('Department', '')
        self.designation = user_data.get('Designation', '')
        self.is_admin = str(user_data.get('Is Admin', 'FALSE')).upper() == 'TRUE'

# ==================== Helper Functions ====================

def get_client_ip():
    """Get client IP address"""
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    elif request.headers.get('X-Real-IP'):
        return request.headers.get('X-Real-IP')
    return request.remote_addr

def get_location_from_ip(ip_address):
    """Get location information from IP"""
    if ip_address in ['127.0.0.1', 'localhost', '::1']:
        return {'ip': ip_address, 'city': 'Local', 'country': 'Local', 'is_office': True}
    
    if ip_address.startswith(('192.168.', '10.', '172.')):
        is_office = ip_address.startswith(tuple(os.getenv('OFFICE_IP_PREFIXES', '192.168.1.').split(',')))
        return {'ip': ip_address, 'city': 'Private Network', 'country': 'Internal', 'is_office': is_office}
    
    office_ips = os.getenv('OFFICE_PUBLIC_IPS', '').split(',')
    if ip_address in office_ips:
        return {'ip': ip_address, 'city': 'Office', 'country': 'Corporate', 'is_office': True}
    
    try:
        response = requests.get(f'https://ipapi.co/{ip_address}/json/', timeout=5)
        data = response.json()
        return {
            'ip': ip_address,
            'city': data.get('city', 'Unknown'),
            'country': data.get('country_name', 'Unknown'),
            'is_office': False
        }
    except:
        return {'ip': ip_address, 'city': 'Unknown', 'country': 'Unknown', 'is_office': False}

def admin_required(f):
    """Decorator for admin-only routes"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash('Admin access required.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function

# ==================== Flask-Login Setup ====================

@login_manager.user_loader
def load_user(username):
    user_data = db.get_user_by_username(username)
    if user_data:
        return User(user_data)
    return None

# ==================== Routes ====================

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        
        if not username or not password:
            flash('Please enter username and password', 'warning')
            return render_template('login.html')
        
        user_data = db.get_user_by_username(username, use_cache=False)
        
        if user_data and db.verify_password(username, password):
            registered_ip = user_data.get('IP Address', '').strip()
            client_ip = get_client_ip()
            
            if registered_ip and client_ip != registered_ip:
                flash('Access denied. This account IP does not match with the registered IP', 'danger')
                return render_template('login.html')
            
            user = User(user_data)
            login_user(user, remember=True)
            flash(f'Welcome back, {user.full_name}!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password', 'danger')
    
    return render_template('login.html')

@app.route('/dashboard')
@login_required
def dashboard():
    # Get user's today's attendance status
    today_status = db.get_user_working_hours_today(current_user.username)
    
    # Get user's attendance history (last 30 days)
    history = db.get_user_attendance_history(current_user.username, days=30)
    
    # Get current time
    timezone = pytz.timezone(os.getenv('TIMEZONE', 'UTC'))
    current_time = datetime.now(timezone)
    
    return render_template('dashboard.html',
                         user=current_user,
                         today_status=today_status,
                         history=history,
                         current_time=current_time.strftime('%H:%M:%S'),
                         current_date=current_time.strftime('%Y-%m-%d'))

@app.route('/check-in')
@login_required
def check_in():
    """Handle check-in"""
    user_data = db.get_user_by_username(current_user.username)
    
    if not user_data:
        flash('User not found', 'danger')
        return redirect(url_for('login'))
    
    client_ip = get_client_ip()
    location_info = get_location_from_ip(client_ip)
    success, message = db.check_in(user_data, client_ip, location_info)
    
    flash(message, 'success' if success else 'warning')
    return redirect(url_for('dashboard'))

@app.route('/check-out')
@login_required
def check_out():
    """Handle check-out"""
    success, message = db.check_out(current_user.username)
    
    flash(message, 'success' if success else 'warning')
    return redirect(url_for('dashboard'))

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# ==================== Admin Routes ====================

@app.route('/admin')
@login_required
@admin_required
def admin_panel():
    """Admin dashboard"""
    today = date.today().strftime('%Y-%m-%d')
    stats = db.get_attendance_stats(today)
    today_attendance = db.get_today_all_attendance()
    all_users = db.get_all_users()
    
    return render_template('admin.html',
                         stats=stats,
                         today_attendance=today_attendance,
                         all_users=all_users,
                         today=today)

@app.route('/admin/stats-json')
@login_required
@admin_required
def admin_stats_json():
    """Return today's stats + attendance as JSON, for the admin panel's
    AJAX auto-refresh (avoids a full page reload that would wipe open
    modals or in-progress form input)."""
    today = date.today().strftime('%Y-%m-%d')
    stats = db.get_attendance_stats(today)
    today_attendance = db.get_today_all_attendance()
    return jsonify({'stats': stats, 'attendance': today_attendance})

@app.route('/admin/add-user', methods=['POST'])
@login_required
@admin_required
def add_user():
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')
    full_name = request.form.get('full_name', '')
    email = request.form.get('email', '')
    department = request.form.get('department', '')
    designation = request.form.get('designation', '')
    phone = request.form.get('phone', '')
    ip_address = request.form.get('ip_address', '')
    
    if not all([username, password, full_name, email]):
        flash('Please fill all required fields', 'danger')
        return redirect(url_for('admin_panel'))
    
    success, message = db.add_user(username, password, full_name, email, 
                                     department, designation, phone, ip_address=ip_address)
    flash(message, 'success' if success else 'danger')
    return redirect(url_for('admin_panel'))

@app.route('/admin/user/<username>/change-password', methods=['POST'])
@login_required
@admin_required
def change_user_password(username):
    """Admin: reset/change a specific user's password"""
    new_password = request.form.get('new_password', '')

    if not new_password or len(new_password) < 6:
        flash('Password must be at least 6 characters', 'danger')
        return redirect(url_for('admin_panel'))

    success, message = db.update_password(username, new_password)
    flash(message, 'success' if success else 'danger')
    return redirect(url_for('admin_panel'))

@app.route('/admin/user/<username>/change-ip', methods=['POST'])
@login_required
@admin_required
def change_user_ip(username):
    """Admin: change (or clear) a specific user's registered IP address"""
    new_ip = request.form.get('new_ip', '').strip()

    if new_ip:
        # Basic sanity check - four dot-separated numeric octets (0-255)
        parts = new_ip.split('.')
        valid = len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)
        if not valid:
            flash('Please enter a valid IPv4 address (e.g. 192.168.1.10), or leave it blank to remove the restriction.', 'danger')
            return redirect(url_for('admin_panel'))

    success, message = db.update_ip_address(username, new_ip)
    flash(message, 'success' if success else 'danger')
    return redirect(url_for('admin_panel'))

@app.route('/admin/import-users', methods=['POST'])
@login_required
@admin_required
def import_users():
    if 'file' not in request.files:
        flash('No file uploaded', 'danger')
        return redirect(url_for('admin_panel'))
    
    file = request.files['file']
    if file.filename == '' or not file.filename.endswith('.csv'):
        flash('Please upload a CSV file', 'danger')
        return redirect(url_for('admin_panel'))
    
    try:
        import csv
        import io
        
        stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
        csv_input = csv.DictReader(stream)
        
        users_to_import = []
        for row in csv_input:
            users_to_import.append({
                'username': row.get('Username', row.get('username', '')),
                'password': row.get('Password', row.get('password', 'Welcome123')),
                'full_name': row.get('Full Name', row.get('full_name', '')),
                'email': row.get('Email', row.get('email', '')),
                'department': row.get('Department', row.get('department', '')),
                'designation': row.get('Designation', row.get('designation', '')),
                'phone': row.get('Phone', row.get('phone', '')),
            })
        
        if users_to_import:
            success, message = db.bulk_import_users(users_to_import)
            flash(message, 'success' if success else 'danger')
        else:
            flash('No valid users found in CSV', 'warning')
    
    except Exception as e:
        flash(f'Error importing users: {str(e)}', 'danger')
    
    return redirect(url_for('admin_panel'))

@app.route('/admin/attendance/<date>')
@login_required
@admin_required
def view_attendance_by_date(date):
    """View attendance for specific date"""
    try:
        # Validate date format
        datetime.strptime(date, '%Y-%m-%d')
        
        # Get attendance records for this date
        attendance_records = db.get_attendance_by_date(date)
        stats = db.get_attendance_stats(date)
        
        return render_template('admin_attendance.html',
                             date=date,
                             attendance=attendance_records,
                             stats=stats)
    except ValueError:
        flash('Invalid date format. Please use YYYY-MM-DD.', 'danger')
        return redirect(url_for('admin_panel'))
    except Exception as e:
        flash(f'Error loading attendance: {str(e)}', 'danger')
        return redirect(url_for('admin_panel'))

@app.route('/admin/user/<username>')
@login_required
@admin_required
def view_user_attendance(username):
    """View attendance history for a user (admin)"""
    user = db.get_user_by_username(username)
    if not user:
        flash('User not found', 'danger')
        return redirect(url_for('admin_panel'))
    
    attendance_history = db.get_user_attendance_history(username, days=90)
    
    return render_template('user_attendance.html',
                         user=user,
                         attendance_history=attendance_history)

# ==================== API Endpoints ====================

@app.route('/api/attendance/today')
@login_required
def api_today_attendance():
    """API endpoint for today's attendance"""
    today_attendance = db.get_today_all_attendance()
    return jsonify(today_attendance)

@app.route('/api/attendance/stats')
@login_required
def api_attendance_stats():
    """API endpoint for attendance statistics"""
    date_param = request.args.get('date', date.today().strftime('%Y-%m-%d'))
    stats = db.get_attendance_stats(date_param)
    return jsonify(stats)

@app.route('/api/my-attendance')
@login_required
def api_my_attendance():
    """API endpoint for current user's today status"""
    status = db.get_user_working_hours_today(current_user.username)
    return jsonify(status)

# ==================== Error Handlers ====================

@app.errorhandler(404)
def not_found_error(error):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    return render_template('500.html'), 500

# ==================== Main ====================

if __name__ == '__main__':
    debug_mode = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'
    port = int(os.getenv('PORT', 5000))
    app.run(debug=debug_mode, host='0.0.0.0', port=port, threaded=True)