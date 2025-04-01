from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file, make_response
import sqlite3
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
import os
import csv
from io import StringIO, BytesIO
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
import io
import xlsxwriter
import pandas as pd
import shutil

app = Flask(__name__)
app.secret_key = 'your_secret_key' 

def get_db_connection():
    conn = sqlite3.connect("users.db")
    conn.row_factory = sqlite3.Row
    return conn

# Create admin_logs table if it doesn't exist
def create_admin_logs_table():
    conn = get_db_connection()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS admin_logs (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id TEXT NOT NULL,
            action TEXT NOT NULL,
            timestamp DATETIME NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

# Create the admin logs table when the app starts
create_admin_logs_table()

@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


def format_time(value):
    try:
        # Try parsing as datetime first
        dt = datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
        return dt.strftime('%I:%M %p')
    except ValueError:
        try:
            # If that fails, try parsing as time
            if ':' in value:
                if len(value.split(':')) == 2:
                    dt = datetime.strptime(value, '%H:%M')
                else:
                    dt = datetime.strptime(value, '%H:%M:%S')
                return dt.strftime('%I:%M %p')
        except ValueError:
            # If all parsing fails, return the original value
            return value

def format_date(value):
    try:
        # Handle datetime string with timezone info (T)
        if 'T' in value:
            dt = datetime.strptime(value, '%Y-%m-%dT%H:%M')
        else:
            dt = datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
        # Format date and time separately
        date_str = dt.strftime('%B %d, %Y')
        time_str = dt.strftime('%I:%M %p')
        return date_str, time_str
    except ValueError:
        return value, value

app.jinja_env.filters['format_time'] = format_time
app.jinja_env.filters['format_date'] = format_date


@app.route('/')
def home():

    if 'user' in session:
        flash("Logged in successfully.", "success")
        return render_template('dashboard.html')
    else:
        return render_template('login.html')

@app.route('/login', methods=['POST'])
def login():
    email = request.form.get('email')
    password = request.form.get('pass')
    conn = get_db_connection()
    
    try:
        user = conn.execute("SELECT * FROM users WHERE email = ? AND password = ?", (email, password)).fetchone()
        
        if user:
            if user['role'] == 'admin':
                session['user'] = 'admin'
                session['is_admin'] = True
                flash("Logged in as admin.", "success")
                return redirect(url_for('admin_dashboard'))
            else:
                student = conn.execute("SELECT * FROM students WHERE idno = ?", (email,)).fetchone()
                if student:
                    session['user'] = student['idno']
                session['is_admin'] = False
                flash("Logged in successfully.", "success")
                return redirect(url_for('dashboard'))
        else:
            flash("Student record not found.", "danger")
            return redirect(url_for('home'))
    except sqlite3.Error as e:
        flash(f"Database error: {str(e)}", "danger")
        return redirect(url_for('home'))
    finally:
        conn.close()



@app.route('/logout')
def logout():
    session.pop('user', None)
    flash("You have been logged out.", "info")
    return redirect(url_for('home'))

@app.route('/dashboard')
def dashboard():
    if 'user' not in session:
        flash("Please log in to access the dashboard.", "warning")
        return redirect(url_for('home'))
    
    conn = get_db_connection()
    
    # 1) Retrieve the student's course and other info
    student = conn.execute("SELECT * FROM students WHERE idno = ?", (session['user'],)).fetchone()
    if not student:
        conn.close()
        flash("Student record not found.", "danger")
        return redirect(url_for('home'))
    
    # 2) Determine max sessions based on course
    if student['course'] in ("BSIT", "BSCS"):
        max_sessions = 30
    else:
        max_sessions = 15

    # 3) Count completed lab sessions
    used_sessions = conn.execute("""
        SELECT COUNT(*) as c 
        FROM lab_sessions 
        WHERE student_id = ? AND status = 'Completed'
    """, (session['user'],)).fetchone()['c']
    
    # 4) Calculate remaining sessions
    remaining_sessions = max_sessions - used_sessions
    if remaining_sessions < 0:
        remaining_sessions = 0
        
    # 5) Calculate behavior points and free sessions
    behavior_points = conn.execute("""
        SELECT COALESCE(SUM(behavior_points), 0) as total
        FROM lab_sessions
        WHERE student_id = ? AND behavior_points > 0
    """, (session['user'],)).fetchone()['total']
    
    free_sessions = behavior_points // 3
    points_until_next = 3 - (behavior_points % 3) if behavior_points % 3 > 0 else 0
    
    # Add free sessions to remaining sessions
    if free_sessions > 0:
        remaining_sessions += free_sessions

    # 6) Get active announcements
    announcements = conn.execute("""
        SELECT * FROM announcements 
        WHERE is_active = 1 
        AND (expiry_date IS NULL OR expiry_date >= datetime('now'))
        ORDER BY posted_date DESC
    """).fetchall()

    # 7) Get the next upcoming reservation
    upcoming_reservation = conn.execute("""
        SELECT lr.*, l.room_number, l.building
        FROM lab_reservations lr
        JOIN laboratories l ON lr.lab_id = l.lab_id
        WHERE lr.student_id = ? 
        AND lr.reservation_date >= date('now')
        AND lr.status = 'Approved'
        ORDER BY lr.reservation_date, lr.start_time 
        LIMIT 1
    """, (session['user'],)).fetchone()
    
    # 8) Get active session if any
    active_session = conn.execute("""
        SELECT ls.*, l.room_number, l.building
        FROM lab_sessions ls
        JOIN laboratories l ON ls.lab_id = l.lab_id
        WHERE ls.student_id = ? AND ls.status = 'Active'
        LIMIT 1
    """, (session['user'],)).fetchone()
    
    conn.close()
    
    return render_template(
        'dashboard.html',
        student=student,
        reservation=upcoming_reservation,
        active_session=active_session,
        remaining_sessions=remaining_sessions,
        announcements=announcements,
        behavior_points=behavior_points,
        free_sessions=free_sessions,
        points_until_next=points_until_next
    )



@app.route('/profile')
def profile():
    if 'user' not in session:
        flash("Please log in to access your profile.", "warning")
        return redirect(url_for('home'))

    conn = get_db_connection()
    student = conn.execute("SELECT * FROM students WHERE idno = ?", (session['user'],)).fetchone()
    conn.close()

    if not student:
        flash("Student information not found.", "danger")
        return redirect(url_for('dashboard'))
    
    return render_template('profile.html', student=student)


@app.route('/edit_profile', methods=['GET', 'POST'])
def edit_profile():
    if 'user' not in session:
        flash("Please log in to access your profile.", "warning")
        return redirect(url_for('home'))
    
    conn = get_db_connection()
    
    if request.method == 'POST':
        # Retrieve form data
        idno = request.form.get('idno')
        lastname = request.form.get('lastname')
        firstname = request.form.get('firstname')
        midname = request.form.get('midname')
        course = request.form.get('course')
        year_level = request.form.get('year_level')
        email_address = request.form.get('email_address')
        delete_image = request.form.get('delete_image') == 'yes'  # Check if delete checkbox is checked

        # Check for email conflicts
        existing_email = conn.execute(
            "SELECT * FROM students WHERE email_address = ? AND idno != ?",
            (email_address, idno)
        ).fetchone()
        if existing_email:
            flash("Email is already used by another account.", "danger")
            conn.close()
            return redirect(url_for('edit_profile'))
        
        # Get current student info to access current image path
        current_student = conn.execute("SELECT image_path FROM students WHERE idno = ?", (idno,)).fetchone()
        image_path = current_student['image_path']  # Keep current path by default
        
        # Handle profile image upload or deletion
        profile_image = request.files.get('profile_image')
        
        if delete_image and image_path:
            # Delete the physical file if it exists
            if image_path:
                file_path = os.path.join('static', image_path)
                if os.path.exists(file_path):
                    os.remove(file_path)
            # Set image_path to None in the database
            image_path = None
            
        elif profile_image and profile_image.filename:
            # Delete old image file if exists
            if image_path:
                old_file_path = os.path.join('static', image_path)
                if os.path.exists(old_file_path):
                    os.remove(old_file_path)
                    
            # Process new image
            filename = secure_filename(profile_image.filename)
            upload_folder = os.path.join('static', 'uploads')
            if not os.path.exists(upload_folder):
                os.makedirs(upload_folder)
            save_path = os.path.join(upload_folder, filename)
            profile_image.save(save_path)
            image_path = os.path.join('uploads', filename).replace(os.sep, '/')

        # Update student record including image path
        conn.execute("""
            UPDATE students
            SET lastname = ?,
                firstname = ?,
                midname = ?,
                course = ?,
                year_level = ?,
                email_address = ?,
                image_path = ?
            WHERE idno = ?
        """, (lastname, firstname, midname, course, year_level, email_address, image_path, idno))
        
        conn.commit()
        conn.close()
        flash("Profile updated successfully!", "success")
        return redirect(url_for('profile'))
    
    else:
        # For GET requests, retrieve the current student info
        student = conn.execute("SELECT * FROM students WHERE idno = ?", (session['user'],)).fetchone()
        conn.close()
        if not student:
            flash("Student information not found.", "danger")
            return redirect(url_for('dashboard'))
        return render_template('edit_profile.html', student=student)    



@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        try:
            idno = request.form.get('idno')
            lastname = request.form.get('lastname')
            firstname = request.form.get('firstname')
            midname = request.form.get('midname')
            course = request.form.get('course')
            year_level = request.form.get('year_level')  # This will now get the text format (e.g., "1st Year")
            email = request.form.get('email')
            password = request.form.get('password')

            # Check that all required fields are provided
            if not all([idno, lastname, firstname, course, year_level, email, password]):
                flash("Missing required fields", "danger")
                return redirect(url_for('register'))
            
            conn = get_db_connection()

            # Check if the student ID is already registered
            existing_student = conn.execute("SELECT * FROM students WHERE idno = ?", (idno,)).fetchone()
            if existing_student:
                flash("Student ID is already used by another account.", "danger")
                conn.close()
                return redirect(url_for('register'))

            # Check if the email is already used
            existing_email = conn.execute("SELECT * FROM students WHERE email_address = ?", (email,)).fetchone()
            if existing_email:
                flash("Email is already used by another account.", "danger")
                conn.close()
                return redirect(url_for('register'))

            # Begin transaction
            conn.execute("BEGIN TRANSACTION")
            
            try:
                # Insert into users table first with idno as email/username
                conn.execute("""
                    INSERT INTO users (email, password, role) 
                    VALUES (?, ?, 'student')
                """, (idno, password))

                # Then insert into students table with year_level as text
                conn.execute("""
                    INSERT INTO students (idno, lastname, firstname, midname, course, year_level, email_address) 
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (idno, lastname, firstname, midname, course, year_level, email))
                
                # Commit the transaction
                conn.commit()
                flash("Registration successful! You can now log in.", "success")
                return redirect(url_for('home'))
                
            except sqlite3.Error as e:
                # Roll back in case of error
                conn.execute("ROLLBACK")
                flash(f"Database error: {str(e)}", "danger")
                return redirect(url_for('register'))

        except Exception as e:
            flash(f"Error: {str(e)}", "danger")
            return redirect(url_for('register'))
        
        finally:
            conn.close()

    return render_template('register.html')




@app.route('/remaining_sessions')
def remaining_sessions():
    if 'user' not in session:
        flash("Please log in to view remaining sessions.", "warning")
        return redirect(url_for('home'))

    conn = get_db_connection()

    # 1) Retrieve the student's course
    student = conn.execute(
        "SELECT course FROM students WHERE idno = ?",
        (session['user'],)
    ).fetchone()

    if not student:
        conn.close()
        flash("Student record not found.", "danger")
        return redirect(url_for('home'))

    # 2) Determine allocated sessions based on the course
    if student['course'] in ("BSIT", "BSCS"):
        allocated_sessions = 30
    else:
        allocated_sessions = 15

    # 3) Count how many sessions the student has used
    used_sessions = conn.execute("""
        SELECT COUNT(*) as c 
        FROM lab_sessions 
        WHERE student_id = ? AND status = 'Completed'
    """, (session['user'],)).fetchone()['c']

    # 4) Calculate remaining sessions
    remaining_sessions = allocated_sessions - used_sessions
    if remaining_sessions < 0:
        remaining_sessions = 0  # clamp to 0 if they exceed
        
    # 5) Calculate behavior points and free sessions
    behavior_points = conn.execute("""
        SELECT COALESCE(SUM(behavior_points), 0) as total
        FROM lab_sessions
        WHERE student_id = ? AND behavior_points > 0
    """, (session['user'],)).fetchone()['total']
    
    free_sessions = behavior_points // 3
    points_until_next = 3 - (behavior_points % 3) if behavior_points % 3 > 0 else 0
    
    # Add free sessions to remaining sessions
    if free_sessions > 0:
        remaining_sessions += free_sessions

    # 6) Retrieve an upcoming reservation (if any)
    reservation = conn.execute("""
        SELECT lr.*, l.room_number, l.building
        FROM lab_reservations lr
        JOIN laboratories l ON lr.lab_id = l.lab_id
        WHERE lr.student_id = ? 
        AND lr.reservation_date >= date('now')
        AND lr.status = 'Approved'
        ORDER BY lr.reservation_date, lr.start_time 
        LIMIT 1
    """, (session['user'],)).fetchone()

    # 7) Retrieve lab session history
    sessions = conn.execute("""
        SELECT 
            ls.*,
            l.room_number,
            l.building,
            ROUND(CAST((julianday(ls.check_out_time) - julianday(ls.check_in_time)) * 24 AS REAL), 2) as duration
        FROM lab_sessions ls
        JOIN laboratories l ON ls.lab_id = l.lab_id
        WHERE ls.student_id = ? AND ls.status = 'Completed'
        ORDER BY ls.check_in_time DESC
    """, (session['user'],)).fetchall()

    conn.close()

    return render_template(
        'remaining_sessions.html',
        remaining_sessions=remaining_sessions,
        reservation=reservation,
        sessions=sessions,
        behavior_points=behavior_points,
        free_sessions=free_sessions,
        points_until_next=points_until_next
    )



@app.route('/sit_in_history')
def sit_in_history():
    if 'user' not in session:
        flash("Please log in to view your sit-in history.", "warning")
        return redirect(url_for('home'))
    
    conn = get_db_connection()
    
    # Get all completed lab sessions with location details and feedback status
    sessions = conn.execute("""
        SELECT 
            ls.*,
            l.room_number,
            l.building,
            ROUND(CAST((julianday(ls.check_out_time) - julianday(ls.check_in_time)) * 24 AS REAL), 2) as duration,
            CASE WHEN sf.feedback_id IS NOT NULL THEN 1 ELSE 0 END as has_feedback
        FROM lab_sessions ls
        JOIN laboratories l ON ls.lab_id = l.lab_id
        LEFT JOIN session_feedback sf ON ls.session_id = sf.session_id AND sf.student_id = ls.student_id
        WHERE ls.student_id = ? AND ls.status = 'Completed'
        ORDER BY ls.check_in_time DESC
    """, (session['user'],)).fetchall()
    
    conn.close()
    
    return render_template('sit_in_history.html', sessions=sessions)




@app.route('/reservation', methods=['GET', 'POST'])
def reservation():
    if 'user' not in session:
        flash("Please log in to make a reservation.", "warning")
        return redirect(url_for('home'))
    
    conn = get_db_connection()
    
    if request.method == 'POST':
        lab_id = request.form.get('lab_id')
        reservation_date = request.form.get('reservation_date')
        start_time = request.form.get('start_time')
        end_time = request.form.get('end_time')
        student_id = session['user']
        
        try:
            # Check if end time is after start time
            start = datetime.strptime(start_time, '%H:%M')
            end = datetime.strptime(end_time, '%H:%M')
            if end <= start:
                flash("End time must be after start time.", "danger")
                return redirect(url_for('reservation'))
            
            # Check for overlapping reservations
            overlapping = conn.execute("""
                SELECT * FROM lab_reservations 
                WHERE lab_id = ? 
                AND reservation_date = ? 
                AND status = 'Approved'
                AND (
                    (start_time <= ? AND end_time > ?) OR
                    (start_time < ? AND end_time >= ?) OR
                    (start_time >= ? AND end_time <= ?)
                )
            """, (lab_id, reservation_date, start_time, start_time, 
                  end_time, end_time, start_time, end_time)).fetchone()
            
            if overlapping:
                flash("This time slot is already reserved.", "danger")
                return redirect(url_for('reservation'))
            
            # Create the reservation
            conn.execute("""
                    INSERT INTO lab_reservations (student_id, lab_id, reservation_date, start_time, end_time, status)
                    VALUES (?, ?, ?, ?, ?, 'Pending')
                """, (student_id, lab_id, reservation_date, start_time, end_time))
            conn.commit()
            flash("Reservation submitted successfully!", "success")
                
        except ValueError:
            flash("Invalid time format.", "danger")
        except sqlite3.Error as e:
            flash(f"Database error: {str(e)}", "danger")
        
        return redirect(url_for('reservation'))
    
    # For GET request, retrieve available labs and upcoming reservations
    try:
        # Get all labs
        labs = conn.execute("SELECT * FROM laboratories ORDER BY building, room_number").fetchall()
        
        # Get upcoming reservations for the student
        reservations = conn.execute("""
                SELECT lr.*, l.building, l.room_number, 
                    CASE 
                        WHEN lr.status = 'Approved' THEN 'badge-success'
                        WHEN lr.status = 'Pending' THEN 'badge-warning'
                        ELSE 'badge-danger'
                    END as status_badge
                FROM lab_reservations lr
                JOIN laboratories l ON lr.lab_id = l.lab_id
                WHERE lr.student_id = ? 
                AND (lr.status = 'Pending' OR 
                    (lr.status = 'Approved' AND lr.reservation_date >= date('now')))
                ORDER BY lr.reservation_date ASC, lr.start_time ASC
        """, (session['user'],)).fetchall()
        
    except sqlite3.Error as e:
        flash(f"Database error: {str(e)}", "danger")
        labs = []
        reservations = []
    
    conn.close()
    return render_template('reservation.html', 
                         labs=labs, 
                         reservations=reservations,
                         now=datetime.now().strftime('%Y-%m-%d'))

@app.route('/admin')
def admin_dashboard():
    # Check if user is logged in
    if 'user' not in session:
        flash("Please log in to access the admin panel.", "warning")
        return redirect(url_for('home'))
    
    # Check if user is admin
    if not session.get('is_admin'):
        flash("Access denied. Admin privileges required.", "danger")
        return redirect(url_for('dashboard'))
    
    try:
        # Get database connection
        conn = get_db_connection()
        
        # Get student statistics
        total_students = conn.execute("SELECT COUNT(*) as count FROM students").fetchone()['count']
        active_sessions = conn.execute("SELECT COUNT(*) as count FROM lab_sessions WHERE status = 'Active'").fetchone()['count']
        total_sit_ins = conn.execute("SELECT COUNT(*) as count FROM lab_sessions WHERE status = 'Completed'").fetchone()['count']
        
        # Fetch all students with their details
        students_basic = conn.execute("""
            SELECT idno, lastname, firstname, midname, course, year_level, email_address 
            FROM students 
            ORDER BY lastname, firstname
        """).fetchall()
        
        # Convert to list of dictionaries for modification
        students = []
        for student in students_basic:
            # Create a mutable dictionary from the row
            student_dict = dict(student)
            
            # Get used sessions count
            used_sessions = conn.execute("""
                SELECT COUNT(*) as count 
                FROM lab_sessions 
                WHERE student_id = ? AND status = 'Completed'
            """, (student['idno'],)).fetchone()['count']
            student_dict['used_sessions'] = used_sessions
            
            # Get total behavior points
            behavior_points = conn.execute("""
                SELECT COALESCE(SUM(behavior_points), 0) as total_points
                FROM lab_sessions 
                WHERE student_id = ? AND behavior_points > 0
            """, (student['idno'],)).fetchone()['total_points']
            student_dict['behavior_points'] = behavior_points
            
            students.append(student_dict)
        
        # Fetch all available laboratories
        labs = conn.execute("""
            SELECT lab_id, room_number, building 
            FROM laboratories 
            WHERE status = 'Available'
            ORDER BY building, room_number
        """).fetchall()
        
        # Close the connection
        conn.close()

        # Render admin template with student and lab data
        return render_template('admin.html', 
                             students=students, 
                             labs=labs,
                             total_students=total_students,
                             active_sessions=active_sessions,
                             total_sit_ins=total_sit_ins)
        
    except sqlite3.Error as e:
        flash(f"Database error: {str(e)}", "danger")
        return redirect(url_for('home'))

@app.route('/admin/announcements', methods=['GET', 'POST'])
def admin_announcements():
    if 'user' not in session or not session.get('is_admin'):
        flash("Access denied. Admin privileges required.", "danger")
        return redirect(url_for('home'))
    
    conn = get_db_connection()
    
    if request.method == 'POST':
        title = request.form.get('title')
        content = request.form.get('content')
        expiry_date = request.form.get('expiry_date')
        
        try:
            # Get current time in Philippines timezone
            current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            conn.execute("""
                INSERT INTO announcements (title, content, posted_by, posted_date, expiry_date, is_active)
                VALUES (?, ?, ?, ?, ?, 1)
            """, (title, content, 1, current_time, expiry_date))  # Using 1 as admin's user ID
            conn.commit()
            flash("Announcement posted successfully!", "success")
        except sqlite3.Error as e:
            flash(f"Error posting announcement: {str(e)}", "danger")
        
        return redirect(url_for('admin_announcements'))
    
    try:
        # First, update expired announcements to inactive
        conn.execute("""
            UPDATE announcements 
            SET is_active = 0 
            WHERE expiry_date IS NOT NULL 
            AND expiry_date < datetime('now')
            AND is_active = 1
        """)
        conn.commit()
        
        # Then fetch all announcements for admin view
        announcements = conn.execute("""
            SELECT *,
                CASE 
                    WHEN expiry_date IS NOT NULL AND expiry_date < datetime('now') THEN 0
                    ELSE is_active 
                END as display_status,
                CASE
                    WHEN expiry_date IS NOT NULL AND expiry_date < datetime('now') THEN 1
                    ELSE 0
                END as is_expired
            FROM announcements 
            ORDER BY posted_date DESC
        """).fetchall()
        
        return render_template('admin_announcements.html', announcements=announcements)
        
    except sqlite3.Error as e:
        flash(f"Database error: {str(e)}", "danger")
        return redirect(url_for('admin_dashboard'))
    finally:
        conn.close()

@app.route('/admin/announcements/toggle/<int:announcement_id>', methods=['GET', 'POST'])
def toggle_announcement(announcement_id):
    if 'user' not in session or not session.get('is_admin'):
        flash("Access denied. Admin privileges required.", "danger")
        return redirect(url_for('home'))
    
    conn = get_db_connection()
    try:
        # Get current announcement status
        current = conn.execute(
            "SELECT is_active FROM announcements WHERE announcement_id = ?", 
            (announcement_id,)
        ).fetchone()
        
        if request.method == 'POST' and not current['is_active']:
            # Activating with new expiry date
            expiry_date = request.form.get('expiry_date')
            conn.execute("""
                UPDATE announcements 
                SET is_active = 1, expiry_date = ?
                WHERE announcement_id = ?
            """, (expiry_date, announcement_id))
        else:
            # Simple toggle (deactivation)
            conn.execute("""
                UPDATE announcements 
                SET is_active = CASE WHEN is_active = 1 THEN 0 ELSE 1 END 
                WHERE announcement_id = ?
            """, (announcement_id,))
        
        conn.commit()
        flash("Announcement status updated successfully!", "success")
    except sqlite3.Error as e:
        flash(f"Error updating announcement: {str(e)}", "danger")
    finally:
        conn.close()
    
    return redirect(url_for('admin_announcements'))

@app.route('/admin/announcements/delete/<int:announcement_id>')
def delete_announcement(announcement_id):
    if 'user' not in session or not session.get('is_admin'):
        flash("Access denied. Admin privileges required.", "danger")
        return redirect(url_for('home'))
    
    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM announcements WHERE announcement_id = ?", (announcement_id,))
        conn.commit()
        flash("Announcement deleted successfully!", "success")
    except sqlite3.Error as e:
        flash(f"Error deleting announcement: {str(e)}", "danger")
    finally:
        conn.close()
    
    return redirect(url_for('admin_announcements'))

@app.route('/api/student_sessions/<student_id>')
def get_student_sessions(student_id):
    if 'user' not in session or not session.get('is_admin'):
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = get_db_connection()
    try:
        # Get student's course
        student = conn.execute("SELECT course FROM students WHERE idno = ?", (student_id,)).fetchone()
        if not student:
            return jsonify({'error': 'Student not found'}), 404

        # Calculate max sessions based on course
        max_sessions = 30 if student['course'] in ("BSIT", "BSCS") else 15

        # Count used sessions
        used_sessions = conn.execute("""
            SELECT COUNT(*) as count 
            FROM lab_sessions 
            WHERE student_id = ? AND status = 'Completed'
        """, (student_id,)).fetchone()['count']

        # Calculate remaining sessions
        remaining_sessions = max(0, max_sessions - used_sessions)

        return jsonify({
            'remaining_sessions': remaining_sessions
        })

    except sqlite3.Error as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@app.route('/api/start_session', methods=['POST'])
def start_session():
    if 'user' not in session or not session.get('is_admin'):
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    student_id = data.get('student_id')
    lab_id = data.get('lab_id')
    purpose = data.get('purpose')

    if not all([student_id, lab_id, purpose]):
        return jsonify({'error': 'Missing required fields'}), 400

    conn = get_db_connection()
    try:
        # Check if student has any active sessions
        active_session = conn.execute("""
            SELECT * FROM lab_sessions 
            WHERE student_id = ? AND status = 'Active'
        """, (student_id,)).fetchone()

        if active_session:
            return jsonify({
                'success': False,
                'message': 'Student already has an active session'
            }), 400

        # Check if student has remaining sessions
        student = conn.execute("SELECT course FROM students WHERE idno = ?", (student_id,)).fetchone()
        max_sessions = 30 if student['course'] in ("BSIT", "BSCS") else 15
        used_sessions = conn.execute("""
            SELECT COUNT(*) as count 
            FROM lab_sessions 
            WHERE student_id = ? AND status = 'Completed'
        """, (student_id,)).fetchone()['count']

        if used_sessions >= max_sessions:
            return jsonify({
                'success': False,
                'message': 'No remaining sessions available'
            }), 400

        # Get current time in Philippines timezone
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # Start new session with current time
        conn.execute("""
            INSERT INTO lab_sessions (
                student_id, lab_id, check_in_time, status, notes, created_by
            ) VALUES (?, ?, ?, 'Active', ?, ?)
        """, (student_id, lab_id, current_time, purpose, session['user']))
        
        conn.commit()
        return jsonify({'success': True})

    except sqlite3.Error as e:
        return jsonify({
            'success': False,
            'message': f"Database error: {str(e)}"
        }), 500
    finally:
        conn.close()

@app.route('/admin/current-sit-in')
def admin_current_sit_in():
    if 'user' not in session or not session.get('is_admin'):
        flash("Access denied. Admin privileges required.", "danger")
        return redirect(url_for('home'))
    
    try:
        conn = get_db_connection()
        
        # Fetch all active sessions with student details
        active_sessions = conn.execute("""
            SELECT 
                ls.session_id,
                s.idno,
                s.lastname,
                s.firstname,
                s.midname,
                s.course,
                s.year_level,
                ls.check_in_time,
                ls.notes as purpose,
                l.room_number,
                l.building,
                ls.behavior_points
            FROM lab_sessions ls
            JOIN students s ON ls.student_id = s.idno
            JOIN laboratories l ON ls.lab_id = l.lab_id
            WHERE ls.status = 'Active'
            ORDER BY ls.check_in_time DESC
        """).fetchall()
        
        conn.close()
        
        return render_template('admin_current_sit_in.html', active_sessions=active_sessions)
        
    except sqlite3.Error as e:
        flash(f"Database error: {str(e)}", "danger")
        return redirect(url_for('admin_dashboard'))

@app.route('/admin/current-sit-in/logout/<student_id>')
def logout_session(student_id):
    if 'user' not in session or not session.get('is_admin'):
        flash("Access denied. Admin privileges required.", "danger")
        return redirect(url_for('home'))
    
    conn = get_db_connection()
    try:
        # Get current time in Philippines timezone
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        conn.execute("""
            UPDATE lab_sessions 
            SET status = 'Completed',
                check_out_time = ?
            WHERE student_id = ? AND status = 'Active'
        """, (current_time, student_id))
        conn.commit()
        flash("Student logged out successfully!", "success")
    except sqlite3.Error as e:
        flash(f"Error logging out student: {str(e)}", "danger")
    finally:
        conn.close()
    
    return redirect(url_for('admin_current_sit_in'))

@app.route('/admin/sit-in-records')
def admin_sit_in_records():
    if 'user' not in session or not session.get('is_admin'):
        flash("Access denied. Admin privileges required.", "danger")
        return redirect(url_for('home'))
    
    try:
        conn = get_db_connection()
        
        # Get purpose/language statistics
        purpose_stats = conn.execute("""
            SELECT 
                notes as purpose,
                COUNT(*) as count
            FROM lab_sessions
            WHERE status = 'Completed'
            GROUP BY notes
            ORDER BY count DESC
        """).fetchall()
        
        # Get laboratory usage statistics
        lab_stats = conn.execute("""
            SELECT 
                l.building,
                l.room_number,
                COUNT(*) as count
            FROM lab_sessions ls
            JOIN laboratories l ON ls.lab_id = l.lab_id
            WHERE ls.status = 'Completed'
            GROUP BY l.building, l.room_number
            ORDER BY count DESC
        """).fetchall()
        
        # Get total completed sessions
        total_sessions = conn.execute("""
            SELECT COUNT(*) as total
            FROM lab_sessions
            WHERE status = 'Completed'
        """).fetchone()['total']
        
        # Fetch all completed sessions with student details
        completed_sessions = conn.execute("""
            SELECT 
                ls.session_id,
                s.idno,
                s.lastname,
                s.firstname,
                s.midname,
                s.course,
                s.year_level,
                DATE(ls.check_in_time) as date,
                ls.check_in_time,
                ls.check_out_time,
                ls.notes as purpose,
                l.room_number,
                l.building,
                ls.behavior_points,
                ROUND(CAST((julianday(ls.check_out_time) - julianday(ls.check_in_time)) * 24 AS REAL), 2) as duration
            FROM lab_sessions ls
            JOIN students s ON ls.student_id = s.idno
            JOIN laboratories l ON ls.lab_id = l.lab_id
            WHERE ls.status = 'Completed'
            ORDER BY ls.check_in_time DESC
        """).fetchall()
        
        conn.close()
        
        return render_template('admin_sit_in_records.html', 
                             completed_sessions=completed_sessions,
                             purpose_stats=purpose_stats,
                             lab_stats=lab_stats,
                             total_sessions=total_sessions)
        
    except sqlite3.Error as e:
        flash(f"Database error: {str(e)}", "danger")
        return redirect(url_for('admin_dashboard'))

@app.route('/admin/reports')
def admin_reports():
    if 'user' not in session or not session.get('is_admin'):
        flash("Access denied. Admin privileges required.", "danger")
        return redirect(url_for('home'))
    
    conn = get_db_connection()
    try:
        # Get all students for the dropdown
        students = conn.execute("""
            SELECT idno, lastname, firstname, midname, course, year_level, email_address
            FROM students
            ORDER BY lastname, firstname
        """).fetchall()
        
        # Get selected student's data if student_id is provided
        selected_student = None
        student_sessions = []
        student_feedback = []
        
        student_id = request.args.get('student_id')
        if student_id:
            # Get student details
            selected_student = conn.execute("""
                SELECT idno, lastname, firstname, midname, course, year_level, email_address
                FROM students
                WHERE idno = ?
            """, (student_id,)).fetchone()
            
            # Get student's sit-in sessions
            student_sessions = conn.execute("""
                SELECT 
                    s.idno,
                    s.lastname,
                    s.firstname,
                    s.midname,
                    ls.notes as purpose,
                    l.room_number,
                    l.building,
                    ls.check_in_time,
                    ls.check_out_time,
                    DATE(ls.check_in_time) as date,
                    ROUND(CAST((julianday(ls.check_out_time) - julianday(ls.check_in_time)) * 24 AS REAL), 2) as duration
                FROM lab_sessions ls
                JOIN students s ON ls.student_id = s.idno
                JOIN laboratories l ON ls.lab_id = l.lab_id
                WHERE ls.student_id = ? AND ls.status = 'Completed'
                ORDER BY ls.check_in_time DESC
            """, (student_id,)).fetchall()
            
            # Get student's feedback
            student_feedback = conn.execute("""
                SELECT 
                    s.idno,
                    s.lastname,
                    s.firstname,
                    s.midname,
                    l.room_number,
                    l.building,
                    DATE(ls.check_in_time) as date,
                    sf.rating,
                    sf.comments
                FROM session_feedback sf
                JOIN lab_sessions ls ON sf.session_id = ls.session_id
                JOIN students s ON sf.student_id = s.idno
                JOIN laboratories l ON ls.lab_id = l.lab_id
                WHERE sf.student_id = ?
                ORDER BY ls.check_in_time DESC
            """, (student_id,)).fetchall()
        
        return render_template(
            "admin_reports.html",
            students=students,
            selected_student=selected_student,
            student_sessions=student_sessions,
            student_feedback=student_feedback
        )
    finally:
        conn.close()

@app.route('/submit_feedback/<int:session_id>', methods=['GET', 'POST'])
def submit_feedback(session_id):
    if 'user' not in session:
        flash("Please log in to submit feedback.", "warning")
        return redirect(url_for('home'))
    
    conn = get_db_connection()
    try:
        # Check if the session exists and belongs to the student
        session_data = conn.execute("""
            SELECT 
                ls.*, 
                l.room_number, 
                l.building,
                ROUND(CAST((julianday(ls.check_out_time) - julianday(ls.check_in_time)) * 24 AS REAL), 2) as duration
            FROM lab_sessions ls
            JOIN laboratories l ON ls.lab_id = l.lab_id
            WHERE ls.session_id = ? AND ls.student_id = ? AND ls.status = 'Completed'
        """, (session_id, session['user'])).fetchone()
        
        if not session_data:
            flash("Session not found or you don't have permission to submit feedback.", "danger")
            return redirect(url_for('sit_in_history'))
        
        # Check if feedback already exists
        existing_feedback = conn.execute("""
            SELECT * FROM session_feedback 
            WHERE session_id = ? AND student_id = ?
        """, (session_id, session['user'])).fetchone()
        
        if existing_feedback:
            flash("You have already submitted feedback for this session.", "warning")
            return redirect(url_for('sit_in_history'))
        
        if request.method == 'POST':
            rating = request.form.get('rating')
            comments = request.form.get('comments')
            
            if not rating:
                flash("Please provide a rating.", "danger")
                return redirect(url_for('submit_feedback', session_id=session_id))
            
            # Insert the feedback
            conn.execute("""
                INSERT INTO session_feedback (session_id, student_id, rating, comments)
                VALUES (?, ?, ?, ?)
            """, (session_id, session['user'], rating, comments))
            conn.commit()
            
            flash("Thank you for your feedback!", "success")
            return redirect(url_for('sit_in_history'))
        
        return render_template('submit_feedback.html', session=session_data)
        
    finally:
        conn.close()

@app.route('/admin/reports/export/sessions/<student_id>')
def export_sessions_csv(student_id):
    if 'user' not in session or not session.get('is_admin'):
        flash("Access denied. Admin privileges required.", "danger")
        return redirect(url_for('home'))
    
    conn = get_db_connection()
    try:
        # Get student info for filename
        student = conn.execute("""
            SELECT lastname, firstname 
            FROM students 
            WHERE idno = ?
        """, (student_id,)).fetchone()
        
        if not student:
            flash("Student not found.", "danger")
            return redirect(url_for('admin_reports'))
        
        # Get student's sit-in sessions
        sessions = conn.execute("""
            SELECT 
                s.idno as "ID Number",
                s.lastname || ', ' || s.firstname || ' ' || COALESCE(s.midname, '') as "Name",
                ls.notes as "Purpose",
                l.room_number || ' - ' || l.building as "Laboratory Room",
                ls.check_in_time as "Check-in Time",
                ls.check_out_time as "Check-out Time",
                DATE(ls.check_in_time) as "Date",
                ROUND(CAST((julianday(ls.check_out_time) - julianday(ls.check_in_time)) * 24 AS REAL), 2) as "Duration (Hours)"
            FROM lab_sessions ls
            JOIN students s ON ls.student_id = s.idno
            JOIN laboratories l ON ls.lab_id = l.lab_id
            WHERE ls.student_id = ? AND ls.status = 'Completed'
            ORDER BY ls.check_in_time DESC
        """, (student_id,)).fetchall()
        
        # Create CSV in memory
        si = StringIO()
        writer = csv.writer(si)
        
        # Write headers
        headers = [
            "ID Number",
            "Name",
            "Purpose",
            "Laboratory Room",
            "Check-in Time",
            "Check-out Time",
            "Date",
            "Duration (Hours)"
        ]
        writer.writerow(headers)
        
        # Write data
        for row in sessions:
            writer.writerow([row[col] for col in row.keys()])
        
        # Create the response
        output = si.getvalue()
        si.close()
        
        # Generate filename
        filename = f"sit_in_sessions_{student['lastname']}_{student['firstname']}_{datetime.now().strftime('%Y-%m-%d')}.csv"
        
        return output, 200, {
            'Content-Type': 'text/csv',
            'Content-Disposition': f'attachment; filename={filename}'
        }
        
    except sqlite3.Error as e:
        flash(f"Database error: {str(e)}", "danger")
        return redirect(url_for('admin_reports'))
    finally:
        conn.close()

@app.route('/admin/reports/export/feedback/<student_id>')
def export_feedback_csv(student_id):
    if 'user' not in session or not session.get('is_admin'):
        flash("Access denied. Admin privileges required.", "danger")
        return redirect(url_for('home'))
    
    conn = get_db_connection()
    try:
        # Get student info for filename
        student = conn.execute("""
            SELECT lastname, firstname 
            FROM students 
            WHERE idno = ?
        """, (student_id,)).fetchone()
        
        if not student:
            flash("Student not found.", "danger")
            return redirect(url_for('admin_reports'))
        
        # Get student's feedback
        feedback = conn.execute("""
            SELECT 
                s.idno as "ID Number",
                s.lastname || ', ' || s.firstname || ' ' || COALESCE(s.midname, '') as "Name",
                l.room_number || ' - ' || l.building as "Laboratory Room",
                DATE(ls.check_in_time) as "Date",
                sf.rating as "Rating",
                sf.comments as "Comments"
            FROM session_feedback sf
            JOIN lab_sessions ls ON sf.session_id = ls.session_id
            JOIN students s ON sf.student_id = s.idno
            JOIN laboratories l ON ls.lab_id = l.lab_id
            WHERE sf.student_id = ?
            ORDER BY ls.check_in_time DESC
        """, (student_id,)).fetchall()
        
        # Create CSV in memory
        si = StringIO()
        writer = csv.writer(si)
        
        # Write headers
        headers = [
            "ID Number",
            "Name",
            "Laboratory Room",
            "Date",
            "Rating",
            "Comments"
        ]
        writer.writerow(headers)
        
        # Write data
        for row in feedback:
            writer.writerow([row[col] for col in row.keys()])
        
        # Create the response
        output = si.getvalue()
        si.close()
        
        # Generate filename
        filename = f"feedback_{student['lastname']}_{student['firstname']}_{datetime.now().strftime('%Y-%m-%d')}.csv"
        
        return output, 200, {
            'Content-Type': 'text/csv',
            'Content-Disposition': f'attachment; filename={filename}'
        }
        
    except sqlite3.Error as e:
        flash(f"Database error: {str(e)}", "danger")
        return redirect(url_for('admin_reports'))
    finally:
        conn.close()

@app.route('/admin/reports/export/sessions/excel/<student_id>')
def export_sessions_excel(student_id):
    if 'user' not in session or not session.get('is_admin'):
        flash("Access denied. Admin privileges required.", "danger")
        return redirect(url_for('home'))
    
    conn = get_db_connection()
    try:
        # Get student info for filename
        student = conn.execute("""
            SELECT lastname, firstname 
            FROM students 
            WHERE idno = ?
        """, (student_id,)).fetchone()
        
        if not student:
            flash("Student not found.", "danger")
            return redirect(url_for('admin_reports'))
        
        # Get student's sit-in sessions
        sessions = conn.execute("""
            SELECT 
                s.idno as "ID Number",
                s.lastname || ', ' || s.firstname || ' ' || COALESCE(s.midname, '') as "Name",
                ls.notes as "Purpose",
                l.room_number || ' - ' || l.building as "Laboratory Room",
                ls.check_in_time as "Check-in Time",
                ls.check_out_time as "Check-out Time",
                DATE(ls.check_in_time) as "Date",
                ROUND(CAST((julianday(ls.check_out_time) - julianday(ls.check_in_time)) * 24 AS REAL), 2) as "Duration (Hours)"
            FROM lab_sessions ls
            JOIN students s ON ls.student_id = s.idno
            JOIN laboratories l ON ls.lab_id = l.lab_id
            WHERE ls.student_id = ? AND ls.status = 'Completed'
            ORDER BY ls.check_in_time DESC
        """, (student_id,)).fetchall()
        
        # Create Excel workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Sit-in Sessions"
        
        # Define headers
        headers = [
            "ID Number",
            "Name",
            "Purpose",
            "Laboratory Room",
            "Check-in Time",
            "Check-out Time",
            "Date",
            "Duration (Hours)"
        ]
        
        # Style for headers
        header_fill = PatternFill(start_color="4CAF50", end_color="4CAF50", fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF")
        
        # Write headers
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal='center')
        
        # Write data
        for row_idx, row in enumerate(sessions, 2):
            for col_idx, col in enumerate(headers):
                cell = ws.cell(row=row_idx, column=col_idx + 1, value=row[col])
                cell.alignment = Alignment(horizontal='center')
        
        # Adjust column widths
        for column in ws.columns:
            max_length = 0
            column = [cell for cell in column]
            for cell in column:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = (max_length + 2)
            ws.column_dimensions[column[0].column_letter].width = adjusted_width
        
        # Save to BytesIO
        excel_file = BytesIO()
        wb.save(excel_file)
        excel_file.seek(0)
        
        filename = f"sit_in_sessions_{student['lastname']}_{student['firstname']}_{datetime.now().strftime('%Y-%m-%d')}.xlsx"
        
        return send_file(
            excel_file,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )
        
    except sqlite3.Error as e:
        flash(f"Database error: {str(e)}", "danger")
        return redirect(url_for('admin_reports'))
    finally:
        conn.close()

@app.route('/admin/reports/export/feedback/excel/<student_id>')
def export_feedback_excel(student_id):
    if 'user' not in session or not session.get('is_admin'):
        flash("Access denied. Admin privileges required.", "danger")
        return redirect(url_for('home'))
    
    conn = get_db_connection()
    try:
        # Get student info for filename
        student = conn.execute("""
            SELECT lastname, firstname 
            FROM students 
            WHERE idno = ?
        """, (student_id,)).fetchone()
        
        if not student:
            flash("Student not found.", "danger")
            return redirect(url_for('admin_reports'))
        
        # Get student's feedback
        feedback = conn.execute("""
            SELECT 
                s.idno as "ID Number",
                s.lastname || ', ' || s.firstname || ' ' || COALESCE(s.midname, '') as "Name",
                l.room_number || ' - ' || l.building as "Laboratory Room",
                DATE(ls.check_in_time) as "Date",
                sf.rating as "Rating",
                sf.comments as "Comments"
            FROM session_feedback sf
            JOIN lab_sessions ls ON sf.session_id = ls.session_id
            JOIN students s ON sf.student_id = s.idno
            JOIN laboratories l ON ls.lab_id = l.lab_id
            WHERE sf.student_id = ?
            ORDER BY ls.check_in_time DESC
        """, (student_id,)).fetchall()
        
        # Create Excel workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Feedback"
        
        # Define headers
        headers = [
            "ID Number",
            "Name",
            "Laboratory Room",
            "Date",
            "Rating",
            "Comments"
        ]
        
        # Style for headers
        header_fill = PatternFill(start_color="4CAF50", end_color="4CAF50", fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF")
        
        # Write headers
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal='center')
        
        # Write data
        for row_idx, row in enumerate(feedback, 2):
            for col_idx, col in enumerate(headers):
                cell = ws.cell(row=row_idx, column=col_idx + 1, value=row[col])
                # Center align all columns except Comments
                if col != "Comments":
                    cell.alignment = Alignment(horizontal='center')
                # For Rating column, convert number to stars
                if col == "Rating":
                    cell.value = "★" * row[col] + "☆" * (5 - row[col])
        
        # Adjust column widths
        for column in ws.columns:
            max_length = 0
            column = [cell for cell in column]
            for cell in column:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = (max_length + 2)
            ws.column_dimensions[column[0].column_letter].width = adjusted_width
        
        # Save to BytesIO
        excel_file = BytesIO()
        wb.save(excel_file)
        excel_file.seek(0)
        
        filename = f"feedback_{student['lastname']}_{student['firstname']}_{datetime.now().strftime('%Y-%m-%d')}.xlsx"
        
        return send_file(
            excel_file,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )
        
    except sqlite3.Error as e:
        flash(f"Database error: {str(e)}", "danger")
        return redirect(url_for('admin_reports'))
    finally:
        conn.close()

@app.route('/admin/reports/export/sessions/pdf/<student_id>')
def export_sessions_pdf(student_id):
    if 'user' not in session or not session.get('is_admin'):
        flash("Access denied. Admin privileges required.", "danger")
        return redirect(url_for('home'))
    
    conn = get_db_connection()
    try:
        # Get student info for filename and header
        student = conn.execute("""
            SELECT lastname, firstname 
            FROM students 
            WHERE idno = ?
        """, (student_id,)).fetchone()
        
        if not student:
            flash("Student not found.", "danger")
            return redirect(url_for('admin_reports'))
        
        # Get student's sit-in sessions
        sessions = conn.execute("""
            SELECT 
                s.idno,
                s.lastname,
                s.firstname,
                s.midname,
                ls.notes as purpose,
                l.room_number,
                l.building,
                ls.check_in_time,
                ls.check_out_time,
                DATE(ls.check_in_time) as date,
                ROUND((JULIANDAY(ls.check_out_time) - JULIANDAY(ls.check_in_time)) * 24, 2) as duration
            FROM lab_sessions ls
            JOIN students s ON ls.student_id = s.idno
            JOIN laboratories l ON ls.lab_id = l.lab_id
            WHERE ls.student_id = ? AND ls.status = 'Completed'
            ORDER BY ls.check_in_time DESC
        """, (student_id,)).fetchall()

        # Create PDF buffer
        buffer = BytesIO()
        
        # Create the PDF document
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            rightMargin=72,
            leftMargin=72,
            topMargin=72,
            bottomMargin=72
        )
        
        # Container for the 'Flowable' objects
        elements = []
        
        # Define styles
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=14,
            textColor=colors.darkgreen,
            spaceBefore=10,
            spaceAfter=20,
            alignment=1  # center alignment
        )
        
        # Add title
        title = Paragraph(f"Sit-in Sessions Report - {student['lastname']}, {student['firstname']}", title_style)
        elements.append(title)
        elements.append(Spacer(1, 12))
        
        # Define table data
        data = []
        # Add header row
        headers = ['ID No.', 'Name', 'Purpose', 'Laboratory', 'Check-in', 'Check-out', 'Date', 'Duration']
        data.append(headers)
        
        # Add session data rows
        for session_row in sessions:
            session_dict = dict(session_row)
            name = f"{session_dict['lastname']}, {session_dict['firstname']} {session_dict['midname'] or ''}"
            laboratory = f"{session_dict['building']} - Room {session_dict['room_number']}"
            
            data.append([
                session_dict['idno'],
                name,
                session_dict['purpose'],
                laboratory,
                session_dict['check_in_time'],
                session_dict['check_out_time'],
                session_dict['date'],
                f"{session_dict['duration']} hrs"
            ])
        
        # Create the table with the data
        table = Table(data, colWidths=[1*inch, 2*inch, 1.5*inch, 1.5*inch, 1.2*inch, 1.2*inch, 1*inch, 1*inch])
        
        # Add style to the table
        style = TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.green),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('ALIGN', (0, 0), (0, -1), 'CENTER'),  # Center align the first column
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('TOPPADDING', (0, 1), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 6),
        ])
        table.setStyle(style)
        
        # Add the table to the elements
        elements.append(table)
        
        # Build the PDF
        doc.build(elements)
        
        # Get the value of the BytesIO buffer
        pdf_data = buffer.getvalue()
        buffer.close()
        
        # Create the response
        response = make_response(pdf_data)
        response.headers['Content-Disposition'] = f'attachment; filename=sit_in_sessions_{student['lastname']}_{student['firstname']}_{datetime.now().strftime("%Y-%m-%d")}.pdf'
        response.headers['Content-Type'] = 'application/pdf'
        
        return response
        
    except Exception as e:
        print(f"Error exporting sessions PDF: {str(e)}")
        flash('An error occurred while exporting the data.', 'error')
        return redirect(url_for('admin_reports'))

@app.route('/admin/reports/export/feedback/pdf/<student_id>')
def export_feedback_pdf(student_id):
    if 'user' not in session or not session.get('is_admin'):
        flash("Access denied. Admin privileges required.", "danger")
        return redirect(url_for('home'))
    
    try:
        conn = get_db_connection()
        
        # Get student info for filename and header
        student = conn.execute("""
            SELECT lastname, firstname 
            FROM students 
            WHERE idno = ?
        """, (student_id,)).fetchone()
        
        if not student:
            flash("Student not found.", "danger")
            return redirect(url_for('admin_reports'))
        
        # Get student's feedback
        feedback = conn.execute("""
            SELECT 
                s.idno,
                s.lastname,
                s.firstname,
                s.midname,
                l.building,
                l.room_number,
                DATE(ls.check_in_time) as date,
                sf.rating,
                sf.comments
            FROM session_feedback sf
            JOIN lab_sessions ls ON sf.session_id = ls.session_id
            JOIN students s ON sf.student_id = s.idno
            JOIN laboratories l ON ls.lab_id = l.lab_id
            WHERE sf.student_id = ?
            ORDER BY ls.check_in_time DESC
        """, (student_id,)).fetchall()

        # Create PDF buffer
        buffer = BytesIO()
        
        # Create the PDF document
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            rightMargin=72,
            leftMargin=72,
            topMargin=72,
            bottomMargin=72
        )
        
        # Container for the 'Flowable' objects
        elements = []
        
        # Define styles
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=14,
            textColor=colors.darkgreen,
            spaceBefore=10,
            spaceAfter=20,
            alignment=1  # center alignment
        )
        
        # Add title
        title = Paragraph(f"Feedback Report - {student['lastname']}, {student['firstname']}", title_style)
        elements.append(title)
        elements.append(Spacer(1, 12))
        
        # Define table data
        data = []
        # Add header row
        headers = ['ID No.', 'Name', 'Laboratory', 'Date', 'Rating', 'Comments']
        data.append(headers)
        
        # Add feedback data rows
        for feedback_row in feedback:
            feedback_dict = dict(feedback_row)
            name = f"{feedback_dict['lastname']}, {feedback_dict['firstname']} {feedback_dict['midname'] or ''}"
            laboratory = f"{feedback_dict['building']} - Room {feedback_dict['room_number']}"
            stars = "★" * feedback_dict['rating'] + "☆" * (5 - feedback_dict['rating'])
            
            data.append([
                feedback_dict['idno'],
                name,
                laboratory,
                feedback_dict['date'],
                stars,
                feedback_dict['comments'] or ""
            ])
        
        # Create the table with the data
        table = Table(data, colWidths=[1*inch, 1.8*inch, 1.5*inch, 1*inch, 0.8*inch, 2.5*inch])
        
        # Add style to the table
        style = TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.green),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('ALIGN', (0, 0), (0, -1), 'CENTER'),  # Center align the first column
            ('ALIGN', (4, 1), (4, -1), 'CENTER'),  # Center align the rating column
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('TOPPADDING', (0, 1), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 6),
        ])
        table.setStyle(style)
        
        # Add the table to the elements
        elements.append(table)
        
        # Build the PDF
        doc.build(elements)
        
        # Get the value of the BytesIO buffer
        pdf_data = buffer.getvalue()
        buffer.close()
        
        # Create the response
        response = make_response(pdf_data)
        response.headers['Content-Disposition'] = f'attachment; filename=feedback_{student['lastname']}_{student['firstname']}_{datetime.now().strftime("%Y-%m-%d")}.pdf'
        response.headers['Content-Type'] = 'application/pdf'
        
        return response
        
    except Exception as e:
        print(f"Error exporting feedback PDF: {str(e)}")
        flash('An error occurred while exporting the data.', 'error')
        return redirect(url_for('admin_reports'))

@app.route('/admin/reports/all')
def admin_all_reports():
    if 'user' not in session or not session.get('is_admin'):
        flash("Access denied. Admin privileges required.", "danger")
        return redirect(url_for('home'))
    
    conn = get_db_connection()
    try:
        # Fetch all students
        students = conn.execute("""
            SELECT idno, lastname, firstname, midname, course, year_level, email_address
            FROM students
            ORDER BY lastname, firstname
        """).fetchall()
        
        # Get aggregate data for all students
        # 1. Total completed sessions
        total_sessions = conn.execute("""
            SELECT COUNT(*) as count
            FROM lab_sessions
            WHERE status = 'Completed'
        """).fetchone()['count']
        
        # 2. Total feedback submissions
        total_feedback = conn.execute("""
            SELECT COUNT(*) as count
            FROM session_feedback
        """).fetchone()['count']
        
        # 3. Laboratory usage statistics
        lab_stats = conn.execute("""
            SELECT 
                l.building || ' - Room ' || l.room_number as lab,
                COUNT(*) as count
            FROM lab_sessions ls
            JOIN laboratories l ON ls.lab_id = l.lab_id
            WHERE ls.status = 'Completed'
            GROUP BY l.building, l.room_number
            ORDER BY count DESC
        """).fetchall()
        
        # 4. Purpose statistics
        purpose_stats = conn.execute("""
            SELECT 
                notes as purpose,
                COUNT(*) as count
            FROM lab_sessions
            WHERE status = 'Completed'
            GROUP BY notes
            ORDER BY count DESC
        """).fetchall()
        
        # 5. Recent sessions (limit to 20)
        recent_sessions = conn.execute("""
            SELECT 
                s.idno,
                s.lastname || ', ' || s.firstname || ' ' || COALESCE(s.midname, '') as name,
                ls.notes as purpose,
                l.room_number || ' - ' || l.building as lab,
                ls.check_in_time,
                ls.check_out_time,
                DATE(ls.check_in_time) as date,
                ROUND(CAST((julianday(ls.check_out_time) - julianday(ls.check_in_time)) * 24 AS REAL), 2) as duration
            FROM lab_sessions ls
            JOIN students s ON ls.student_id = s.idno
            JOIN laboratories l ON ls.lab_id = l.lab_id
            WHERE ls.status = 'Completed'
            ORDER BY ls.check_in_time DESC
            LIMIT 20
        """).fetchall()
        
        # 6. Recent feedback (limit to 20)
        recent_feedback = conn.execute("""
            SELECT 
                s.idno,
                s.lastname || ', ' || s.firstname || ' ' || COALESCE(s.midname, '') as name,
                l.room_number || ' - ' || l.building as lab,
                DATE(ls.check_in_time) as date,
                sf.rating,
                sf.comments
            FROM session_feedback sf
            JOIN lab_sessions ls ON sf.session_id = ls.session_id
            JOIN students s ON sf.student_id = s.idno
            JOIN laboratories l ON ls.lab_id = l.lab_id
            ORDER BY ls.check_in_time DESC
            LIMIT 20
        """).fetchall()
        
        return render_template(
            'admin_all_reports.html',
            students=students,
            total_sessions=total_sessions,
            total_feedback=total_feedback,
            lab_stats=lab_stats,
            purpose_stats=purpose_stats,
            recent_sessions=recent_sessions,
            recent_feedback=recent_feedback
        )
    
    except sqlite3.Error as e:
        flash(f"Database error: {str(e)}", "danger")
        return redirect(url_for('admin_reports'))
    finally:
        conn.close()

@app.route('/admin/reports/export/all/sessions/csv')
def export_all_sessions_csv():
    # Check if user is logged in and is admin
    if 'user' not in session or not session.get('is_admin'):
        flash("Access denied. Admin privileges required.", "danger")
        return redirect(url_for('home'))
    
    conn = None
    try:
        conn = get_db_connection()
        
        # Get all sessions
        sessions = conn.execute("""
            SELECT 
                s.idno,
                s.lastname,
                s.firstname,
                s.midname,
                ls.notes as purpose,
                l.building,
                l.room_number,
                ls.check_in_time,
                ls.check_out_time,
                DATE(ls.check_in_time) as date,
                ROUND((JULIANDAY(ls.check_out_time) - JULIANDAY(ls.check_in_time)) * 24, 2) as duration
            FROM lab_sessions ls
            JOIN students s ON ls.student_id = s.idno
            JOIN laboratories l ON ls.lab_id = l.lab_id
            WHERE ls.check_out_time IS NOT NULL
            ORDER BY ls.check_in_time DESC
        """).fetchall()
        
        # Create CSV
        csv_data = io.StringIO()
        csv_writer = csv.writer(csv_data)
        
        # Write headers
        headers = ['ID Number', 'Last Name', 'First Name', 'Middle Name', 'Purpose', 'Building', 'Room Number', 'Check-in Time', 'Check-out Time', 'Date', 'Duration (Hours)']
        csv_writer.writerow(headers)
        
        # Write data
        for session_row in sessions:
            session_dict = dict(session_row)
            csv_writer.writerow([
                session_dict['idno'],
                session_dict['lastname'],
                session_dict['firstname'],
                session_dict['midname'] or "",
                session_dict['purpose'] or "",
                session_dict['building'],
                session_dict['room_number'],
                session_dict['check_in_time'],
                session_dict['check_out_time'],
                session_dict['date'],
                session_dict['duration']
            ])
        
        # Prepare response
        response = make_response(csv_data.getvalue())
        response.headers['Content-Disposition'] = f'attachment; filename=all_sit_in_sessions_{datetime.now().strftime("%Y-%m-%d")}.csv'
        response.headers['Content-Type'] = 'text/csv'
        
        return response
    
    except Exception as e:
        print(f"Error exporting all sessions CSV: {str(e)}")
        flash(f'An error occurred while exporting the data: {str(e)}', 'error')
        return redirect(url_for('admin_all_reports'))
    finally:
        if conn:
            conn.close()

@app.route('/admin/reports/export/all/feedback/csv')
def export_all_feedback_csv():
    # Check if user is logged in and is admin
    if 'user' not in session or not session.get('is_admin'):
        flash("Access denied. Admin privileges required.", "danger")
        return redirect(url_for('home'))
    
    conn = None
    try:
        conn = get_db_connection()
        
        # Get all feedback
        feedback = conn.execute("""
            SELECT 
                s.idno,
                s.lastname,
                s.firstname,
                s.midname,
                l.building,
                l.room_number,
                DATE(ls.check_in_time) as date,
                sf.rating,
                sf.comments
            FROM session_feedback sf
            JOIN lab_sessions ls ON sf.session_id = ls.session_id
            JOIN students s ON sf.student_id = s.idno
            JOIN laboratories l ON ls.lab_id = l.lab_id
            ORDER BY ls.check_in_time DESC
        """).fetchall()
        
        # Create CSV
        csv_data = io.StringIO()
        csv_writer = csv.writer(csv_data)
        
        # Write headers
        headers = ['ID Number', 'Last Name', 'First Name', 'Middle Name', 'Building', 'Room Number', 'Date', 'Rating', 'Comments']
        csv_writer.writerow(headers)
        
        # Write data
        for feedback_row in feedback:
            feedback_dict = dict(feedback_row)
            csv_writer.writerow([
                feedback_dict['idno'],
                feedback_dict['lastname'],
                feedback_dict['firstname'],
                feedback_dict['midname'] or "",
                feedback_dict['building'],
                feedback_dict['room_number'],
                feedback_dict['date'],
                feedback_dict['rating'],
                feedback_dict['comments'] or ""
            ])
        
        # Prepare response
        response = make_response(csv_data.getvalue())
        response.headers['Content-Disposition'] = f'attachment; filename=all_feedback_{datetime.now().strftime("%Y-%m-%d")}.csv'
        response.headers['Content-Type'] = 'text/csv'
        
        return response
    
    except Exception as e:
        print(f"Error exporting all feedback CSV: {str(e)}")
        flash(f'An error occurred while exporting the data: {str(e)}', 'error')
        return redirect(url_for('admin_all_reports'))
    finally:
        if conn:
            conn.close()

@app.route('/admin/reports/export/all/sessions/excel')
def export_all_sessions_excel():
    # Check if user is logged in and is admin
    if 'user' not in session or not session.get('is_admin'):
        flash("Access denied. Admin privileges required.", "danger")
        return redirect(url_for('home'))
    
    conn = None
    try:
        conn = get_db_connection()
        
        # Get all sessions
        sessions = conn.execute("""
            SELECT 
                s.idno,
                s.lastname,
                s.firstname,
                s.midname,
                ls.notes as purpose,
                l.building,
                l.room_number,
                ls.check_in_time,
                ls.check_out_time,
                DATE(ls.check_in_time) as date,
                ROUND((JULIANDAY(ls.check_out_time) - JULIANDAY(ls.check_in_time)) * 24, 2) as duration
            FROM lab_sessions ls
            JOIN students s ON ls.student_id = s.idno
            JOIN laboratories l ON ls.lab_id = l.lab_id
            WHERE ls.check_out_time IS NOT NULL
            ORDER BY ls.check_in_time DESC
        """).fetchall()
        
        # Create a BytesIO object
        output = BytesIO()
        
        # Use pandas to create an Excel file
        import pandas as pd
        
        # Convert data to pandas DataFrame
        data = []
        for session_row in sessions:
            session_dict = dict(session_row)
            data.append({
                'ID Number': session_dict['idno'],
                'Last Name': session_dict['lastname'],
                'First Name': session_dict['firstname'],
                'Middle Name': session_dict['midname'] or "",
                'Purpose': session_dict['purpose'] or "",
                'Building': session_dict['building'],
                'Room Number': session_dict['room_number'],
                'Check-in Time': session_dict['check_in_time'],
                'Check-out Time': session_dict['check_out_time'],
                'Date': session_dict['date'],
                'Duration (Hours)': session_dict['duration']
            })
        
        df = pd.DataFrame(data)
        
        # Write DataFrame to Excel
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, sheet_name='All Sessions', index=False)
            
            # Access the workbook and the worksheet
            workbook = writer.book
            worksheet = writer.sheets['All Sessions']
            
            # Add a header format
            header_format = workbook.add_format({
                'bold': True,
                'fg_color': '#4CAF50',
                'font_color': 'white',
                'border': 1
            })
            
            # Apply header format to the header row
            for col_num, value in enumerate(df.columns.values):
                worksheet.write(0, col_num, value, header_format)
                
            # Auto-adjust column widths
            for i, col in enumerate(df.columns):
                max_len = max(df[col].astype(str).str.len().max(), len(col)) + 2
                worksheet.set_column(i, i, max_len)
        
        # Prepare response
        output.seek(0)
        
        response = make_response(output.getvalue())
        response.headers['Content-Disposition'] = f'attachment; filename=all_sit_in_sessions_{datetime.now().strftime("%Y-%m-%d")}.xlsx'
        response.headers['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        
        return response
        
    except Exception as e:
        print(f"Error exporting all sessions Excel: {str(e)}")
        flash(f'An error occurred while exporting the data: {str(e)}', 'error')
        return redirect(url_for('admin_all_reports'))
    finally:
        if conn:
            conn.close()

@app.route('/admin/reports/export/all/feedback/excel')
def export_all_feedback_excel():
    # Check if user is logged in and is admin
    if 'user' not in session or not session.get('is_admin'):
        flash("Access denied. Admin privileges required.", "danger")
        return redirect(url_for('home'))
    
    conn = None
    try:
        conn = get_db_connection()
        
        # Get all feedback
        feedback = conn.execute("""
            SELECT 
                s.idno,
                s.lastname,
                s.firstname,
                s.midname,
                l.building,
                l.room_number,
                DATE(ls.check_in_time) as date,
                sf.rating,
                sf.comments
            FROM session_feedback sf
            JOIN lab_sessions ls ON sf.session_id = ls.session_id
            JOIN students s ON sf.student_id = s.idno
            JOIN laboratories l ON ls.lab_id = l.lab_id
            ORDER BY ls.check_in_time DESC
        """).fetchall()
        
        # Create a BytesIO object
        output = BytesIO()
        
        # Use pandas to create an Excel file
        import pandas as pd
        
        # Convert data to pandas DataFrame
        data = []
        for feedback_row in feedback:
            feedback_dict = dict(feedback_row)
            # Convert rating to stars
            stars = "★" * feedback_dict['rating'] + "☆" * (5 - feedback_dict['rating'])
            data.append({
                'ID Number': feedback_dict['idno'],
                'Last Name': feedback_dict['lastname'],
                'First Name': feedback_dict['firstname'],
                'Middle Name': feedback_dict['midname'] or "",
                'Building': feedback_dict['building'],
                'Room Number': feedback_dict['room_number'],
                'Date': feedback_dict['date'],
                'Rating': stars,
                'Comments': feedback_dict['comments'] or ""
            })
        
        df = pd.DataFrame(data)
        
        # Write DataFrame to Excel
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, sheet_name='All Feedback', index=False)
            
            # Access the workbook and the worksheet
            workbook = writer.book
            worksheet = writer.sheets['All Feedback']
            
            # Add a header format
            header_format = workbook.add_format({
                'bold': True,
                'fg_color': '#4CAF50',
                'font_color': 'white',
                'border': 1
            })
            
            # Apply header format to the header row
            for col_num, value in enumerate(df.columns.values):
                worksheet.write(0, col_num, value, header_format)
                
            # Auto-adjust column widths
            for i, col in enumerate(df.columns):
                if col == 'Comments':
                    worksheet.set_column(i, i, 40)  # Make comments column wider
                else:
                    max_len = max(df[col].astype(str).str.len().max(), len(col)) + 2
                    worksheet.set_column(i, i, max_len)
        
        # Prepare response
        output.seek(0)
        
        response = make_response(output.getvalue())
        response.headers['Content-Disposition'] = f'attachment; filename=all_feedback_{datetime.now().strftime("%Y-%m-%d")}.xlsx'
        response.headers['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        
        return response
        
    except Exception as e:
        print(f"Error exporting all feedback Excel: {str(e)}")
        flash(f'An error occurred while exporting the data: {str(e)}', 'error')
        return redirect(url_for('admin_all_reports'))
    finally:
        if conn:
            conn.close()

@app.route('/admin/reports/export/all/sessions/pdf')
def export_all_sessions_pdf():
    # Check if user is logged in and is admin
    if 'user' not in session or not session.get('is_admin'):
        flash("Access denied. Admin privileges required.", "danger")
        return redirect(url_for('home'))
    
    conn = None
    try:
        conn = get_db_connection()
        
        # Get all sessions
        sessions = conn.execute("""
            SELECT 
                s.idno,
                s.lastname,
                s.firstname,
                s.midname,
                ls.notes as purpose,
                l.building,
                l.room_number,
                ls.check_in_time,
                ls.check_out_time,
                DATE(ls.check_in_time) as date,
                ROUND((JULIANDAY(ls.check_out_time) - JULIANDAY(ls.check_in_time)) * 24, 2) as duration
            FROM lab_sessions ls
            JOIN students s ON ls.student_id = s.idno
            JOIN laboratories l ON ls.lab_id = l.lab_id
            WHERE ls.check_out_time IS NOT NULL
            ORDER BY ls.check_in_time DESC
        """).fetchall()
        
        # Create PDF buffer
        buffer = BytesIO()
        
        # Create the PDF document
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            rightMargin=72,
            leftMargin=72,
            topMargin=72,
            bottomMargin=72
        )
        
        # Container for the 'Flowable' objects
        elements = []
        
        # Define styles
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=14,
            textColor=colors.darkgreen,
            spaceBefore=10,
            spaceAfter=20,
            alignment=1  # center alignment
        )
        
        # Add title
        title = Paragraph(f"All Sit-in Sessions - {datetime.now().strftime('%Y-%m-%d')}", title_style)
        elements.append(title)
        elements.append(Spacer(1, 12))
        
        # Define table data
        data = []
        # Add header row
        headers = ['ID No.', 'Name', 'Purpose', 'Laboratory', 'Check-in', 'Check-out', 'Date', 'Duration']
        data.append(headers)
        
        # Add session data rows
        for session_row in sessions:
            session_dict = dict(session_row)
            name = f"{session_dict['lastname']}, {session_dict['firstname']} {session_dict['midname'] or ''}"
            laboratory = f"{session_dict['building']} - Room {session_dict['room_number']}"
            
            data.append([
                session_dict['idno'],
                name,
                session_dict['purpose'] or "",
                laboratory,
                session_dict['check_in_time'],
                session_dict['check_out_time'],
                session_dict['date'],
                f"{session_dict['duration']} hrs"
            ])
        
        # Create the table with the data
        table = Table(data, colWidths=[1*inch, 2*inch, 1.5*inch, 1.5*inch, 1.2*inch, 1.2*inch, 1*inch, 1*inch])
        
        # Add style to the table
        style = TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.green),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('ALIGN', (0, 0), (0, -1), 'CENTER'),  # Center align the first column
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('TOPPADDING', (0, 1), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 6),
        ])
        table.setStyle(style)
        
        # Add the table to the elements
        elements.append(table)
        
        # Build the PDF
        doc.build(elements)
        
        # Get the value of the BytesIO buffer
        pdf_data = buffer.getvalue()
        buffer.close()
        
        # Create the response
        response = make_response(pdf_data)
        response.headers['Content-Disposition'] = f'attachment; filename=all_sit_in_sessions_{datetime.now().strftime("%Y-%m-%d")}.pdf'
        response.headers['Content-Type'] = 'application/pdf'
        
        return response
        
    except Exception as e:
        print(f"Error exporting all sessions PDF: {str(e)}")
        flash(f'An error occurred while exporting the data: {str(e)}', 'error')
        return redirect(url_for('admin_all_reports'))
    finally:
        if conn:
            conn.close()

@app.route('/admin/reports/export/all/feedback/pdf')
def export_all_feedback_pdf():
    # Check if user is logged in and is admin
    if 'user' not in session or not session.get('is_admin'):
        flash("Access denied. Admin privileges required.", "danger")
        return redirect(url_for('home'))
    
    conn = None
    try:
        conn = get_db_connection()
        
        # Get all feedback
        feedback = conn.execute("""
            SELECT 
                s.idno,
                s.lastname,
                s.firstname,
                s.midname,
                l.building,
                l.room_number,
                DATE(ls.check_in_time) as date,
                sf.rating,
                sf.comments
            FROM session_feedback sf
            JOIN lab_sessions ls ON sf.session_id = ls.session_id
            JOIN students s ON sf.student_id = s.idno
            JOIN laboratories l ON ls.lab_id = l.lab_id
            ORDER BY ls.check_in_time DESC
        """).fetchall()
        
        # Create PDF buffer
        buffer = BytesIO()
        
        # Create the PDF document
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            rightMargin=72,
            leftMargin=72,
            topMargin=72,
            bottomMargin=72
        )
        
        # Container for the 'Flowable' objects
        elements = []
        
        # Define styles
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=14,
            textColor=colors.darkgreen,
            spaceBefore=10,
            spaceAfter=20,
            alignment=1  # center alignment
        )
        
        # Add title
        title = Paragraph(f"All Student Feedback - {datetime.now().strftime('%Y-%m-%d')}", title_style)
        elements.append(title)
        elements.append(Spacer(1, 12))
        
        # Define table data
        data = []
        # Add header row
        headers = ['ID No.', 'Name', 'Laboratory', 'Date', 'Rating', 'Comments']
        data.append(headers)
        
        # Add feedback data rows
        for feedback_row in feedback:
            feedback_dict = dict(feedback_row)
            name = f"{feedback_dict['lastname']}, {feedback_dict['firstname']} {feedback_dict['midname'] or ''}"
            laboratory = f"{feedback_dict['building']} - Room {feedback_dict['room_number']}"
            stars = "★" * feedback_dict['rating'] + "☆" * (5 - feedback_dict['rating'])
            
            data.append([
                feedback_dict['idno'],
                name,
                laboratory,
                feedback_dict['date'],
                stars,
                feedback_dict['comments'] or ""
            ])
        
        # Create the table with the data
        table = Table(data, colWidths=[1*inch, 1.8*inch, 1.5*inch, 1*inch, 0.8*inch, 2.5*inch])
        
        # Add style to the table
        style = TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.green),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('ALIGN', (0, 0), (0, -1), 'CENTER'),  # Center align the first column
            ('ALIGN', (4, 1), (4, -1), 'CENTER'),  # Center align the rating column
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('TOPPADDING', (0, 1), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 6),
        ])
        table.setStyle(style)
        
        # Add the table to the elements
        elements.append(table)
        
        # Build the PDF
        doc.build(elements)
        
        # Get the value of the BytesIO buffer
        pdf_data = buffer.getvalue()
        buffer.close()
        
        # Create the response
        response = make_response(pdf_data)
        response.headers['Content-Disposition'] = f'attachment; filename=all_feedback_{datetime.now().strftime("%Y-%m-%d")}.pdf'
        response.headers['Content-Type'] = 'application/pdf'
        
        return response
        
    except Exception as e:
        print(f"Error exporting all feedback PDF: {str(e)}")
        flash(f'An error occurred while exporting the data: {str(e)}', 'error')
        return redirect(url_for('admin_all_reports'))
    finally:
        if conn:
            conn.close()

@app.route('/admin/edit-student/<student_id>', methods=['GET', 'POST'])
def admin_edit_student(student_id):
    # Check if user is logged in and is admin
    if 'user' not in session or not session.get('is_admin'):
        flash("Access denied. Admin privileges required.", "danger")
        return redirect(url_for('home'))
    
    conn = get_db_connection()
    
    if request.method == 'POST':
        # Retrieve form data
        idno = request.form.get('idno')
        lastname = request.form.get('lastname')
        firstname = request.form.get('firstname')
        midname = request.form.get('midname')
        course = request.form.get('course')
        year_level = request.form.get('year_level')
        email_address = request.form.get('email_address')
        delete_image = request.form.get('delete_image') == 'yes'  # Check if delete checkbox is checked

        # Verify that the student ID matches the URL parameter
        if idno != student_id:
            flash("Student ID mismatch. Operation aborted.", "danger")
            return redirect(url_for('admin_dashboard'))

        # Check for email conflicts
        existing_email = conn.execute(
            "SELECT * FROM students WHERE email_address = ? AND idno != ?",
            (email_address, idno)
        ).fetchone()
        if existing_email:
            flash("Email is already used by another account.", "danger")
            conn.close()
            return redirect(url_for('admin_edit_student', student_id=student_id))
        
        # Get current student info to access current image path
        current_student = conn.execute("SELECT image_path FROM students WHERE idno = ?", (idno,)).fetchone()
        image_path = current_student['image_path']  # Keep current path by default
        
        # Handle profile image upload or deletion
        profile_image = request.files.get('profile_image')
        
        if delete_image and image_path:
            # Delete the physical file if it exists
            if image_path:
                file_path = os.path.join('static', image_path)
                if os.path.exists(file_path):
                    os.remove(file_path)
            # Set image_path to None in the database
            image_path = None
            
        elif profile_image and profile_image.filename:
            # Delete old image file if exists
            if image_path:
                old_file_path = os.path.join('static', image_path)
                if os.path.exists(old_file_path):
                    os.remove(old_file_path)
                    
            # Process new image
            filename = secure_filename(profile_image.filename)
            upload_folder = os.path.join('static', 'uploads')
            if not os.path.exists(upload_folder):
                os.makedirs(upload_folder)
            save_path = os.path.join(upload_folder, filename)
            profile_image.save(save_path)
            image_path = os.path.join('uploads', filename).replace(os.sep, '/')

        # Update student record including image path
        conn.execute("""
            UPDATE students
            SET lastname = ?,
                firstname = ?,
                midname = ?,
                course = ?,
                year_level = ?,
                email_address = ?,
                image_path = ?
            WHERE idno = ?
        """, (lastname, firstname, midname, course, year_level, email_address, image_path, idno))
        
        conn.commit()
        conn.close()
        flash("Student updated successfully!", "success")
        return redirect(url_for('admin_dashboard'))
    
    else:
        # For GET requests, retrieve the specified student info
        student = conn.execute("SELECT * FROM students WHERE idno = ?", (student_id,)).fetchone()
        conn.close()
        if not student:
            flash("Student not found.", "danger")
            return redirect(url_for('admin_dashboard'))
        return render_template('admin_edit_student.html', student=student)

@app.route('/admin/feedback')
def admin_feedback():
    # Check if user is logged in and is admin
    if 'user' not in session or session.get('is_admin') != True:
        flash("Please log in as an administrator.", "warning")
        return redirect(url_for('home'))
    
    conn = get_db_connection()
    
    # Get all feedback with student and lab details
    feedback = conn.execute("""
        SELECT sf.*, s.firstname, s.lastname, s.course, s.year_level, 
               l.building, l.room_number, ls.check_in_time,
               DATE(ls.check_in_time) as date
        FROM session_feedback sf
        JOIN students s ON sf.student_id = s.idno
        JOIN lab_sessions ls ON sf.session_id = ls.session_id
        JOIN laboratories l ON ls.lab_id = l.lab_id
        ORDER BY ls.check_in_time DESC
    """).fetchall()
    
    # Calculate statistics
    total_feedback = len(feedback)
    
    # Calculate average rating
    if total_feedback > 0:
        avg_rating = conn.execute("""
            SELECT AVG(rating) as avg_rating FROM session_feedback
        """).fetchone()['avg_rating']
    else:
        avg_rating = 0
    
    # Get rating distribution
    rating_distribution = []
    for rating in range(1, 6):
        count = conn.execute("""
            SELECT COUNT(*) as count FROM session_feedback
            WHERE rating = ?
        """, (rating,)).fetchone()['count']
        rating_distribution.append({'rating': rating, 'count': count})
    
    conn.close()
    
    return render_template(
        'admin_feedback.html',
        feedback=feedback,
        total_feedback=total_feedback,
        avg_rating=avg_rating,
        rating_distribution=rating_distribution
    )

@app.route('/admin/leaderboard')
def admin_leaderboard():
    # Check if user is logged in and is admin
    if 'user' not in session or session.get('is_admin') != True:
        flash("Please log in as an administrator.", "warning")
        return redirect(url_for('home'))
    
    conn = get_db_connection()
    
    # Get top 10 most active students (by completed session count)
    most_active_students = conn.execute("""
        SELECT 
            s.idno, s.firstname, s.lastname, s.course, s.year_level, s.image_path,
            COUNT(ls.session_id) as session_count
        FROM students s
        JOIN lab_sessions ls ON s.idno = ls.student_id
        WHERE ls.status = 'Completed'
        GROUP BY s.idno
        ORDER BY session_count DESC
        LIMIT 10
    """).fetchall()
    
    # Get top 10 students with the most behavior points
    top_performers = conn.execute("""
        SELECT 
            s.idno, s.firstname, s.lastname, s.course, s.year_level, s.image_path,
            COALESCE(SUM(ls.behavior_points), 0) as behavior_points
        FROM students s
        LEFT JOIN lab_sessions ls ON s.idno = ls.student_id AND ls.behavior_points > 0
        GROUP BY s.idno
        HAVING behavior_points > 0
        ORDER BY behavior_points DESC
        LIMIT 10
    """).fetchall()
    
    # Calculate free sessions for each top performer
    top_performers_list = []
    for student in top_performers:
        student_dict = dict(student)
        student_dict['free_sessions'] = student_dict['behavior_points'] // 3
        top_performers_list.append(student_dict)
    
    conn.close()
    
    return render_template(
        'admin_leaderboard.html',
        most_active_students=most_active_students,
        top_performers=top_performers_list
    )

@app.route('/admin/award_behavior_point/<int:session_id>')
def award_behavior_point(session_id):
    if 'user' not in session or not session.get('is_admin'):
        flash("Access denied. Admin privileges required.", "danger")
        return redirect(url_for('home'))
    
    conn = None
    try:
        conn = get_db_connection()
        
        # Check if the session exists
        session_data = conn.execute("SELECT * FROM lab_sessions WHERE session_id = ?", (session_id,)).fetchone()
        
        if not session_data:
            flash("Session not found.", "danger")
            return redirect(url_for('admin_sit_in_records'))
        
        # Check if the student already has a behavior point for this session
        existing_point = conn.execute(
            "SELECT behavior_points FROM lab_sessions WHERE session_id = ?", 
            (session_id,)
        ).fetchone()
        
        if existing_point and existing_point['behavior_points']:
            flash("This student has already been awarded a behavior point for this session.", "warning")
        else:
            # Award the behavior point
            conn.execute(
                "UPDATE lab_sessions SET behavior_points = 1 WHERE session_id = ?",
                (session_id,)
            )
            conn.commit()
            
            # Get student info and calculate total points
            student_info = conn.execute("""
                SELECT s.idno, s.firstname, s.lastname
                FROM lab_sessions ls
                JOIN students s ON ls.student_id = s.idno
                WHERE ls.session_id = ?
            """, (session_id,)).fetchone()
            
            student_name = f"{student_info['firstname']} {student_info['lastname']}" if student_info else "Student"
            
            # Calculate total behavior points for the student
            if student_info:
                total_points = conn.execute("""
                    SELECT COALESCE(SUM(behavior_points), 0) as total
                    FROM lab_sessions
                    WHERE student_id = ? AND behavior_points > 0
                """, (student_info['idno'],)).fetchone()['total']
                
                # Check if student has reached a multiple of 3 points
                if total_points > 0 and total_points % 3 == 0:
                    free_sessions = total_points // 3
                    flash(f"Behavior point awarded to {student_name} successfully! They now have {total_points} points, earning them {free_sessions} free session(s)!", "success")
                else:
                    points_to_next_free = 3 - (total_points % 3)
                    flash(f"Behavior point awarded to {student_name} successfully! They now have {total_points} points. {points_to_next_free} more points needed for another free session.", "success")
            else:
                flash(f"Behavior point awarded successfully!", "success")
        
        # Determine which page to redirect to based on the session status
        if session_data['status'] == 'Active':
            return redirect(url_for('admin_current_sit_in'))
        else:
            return redirect(url_for('admin_sit_in_records'))
            
    except sqlite3.Error as e:
        flash(f"Database error: {str(e)}", "danger")
        return redirect(url_for('admin_dashboard'))
    finally:
        if conn:
            conn.close()

@app.route('/admin/reset_sessions')
def admin_reset_sessions():
    """Reset all students' remaining sessions to default values based on their course."""
    if 'user' not in session or not session.get('is_admin'):
        flash("Access denied. Admin privileges required.", "danger")
        return redirect(url_for('home'))
    
    try:
        conn = get_db_connection()
        
        # Delete all completed lab sessions
        conn.execute("DELETE FROM lab_sessions WHERE status = 'Completed'")
        
        # Log the action
        conn.execute("""
            INSERT INTO admin_logs (admin_id, action, timestamp)
            VALUES (?, 'Reset all student sessions', datetime('now'))
        """, (session['user'],))
        
        conn.commit()
        conn.close()
        
        flash("All student sessions have been reset to their default values.", "success")
    except sqlite3.Error as e:
        flash(f"Database error: {str(e)}", "danger")
    
    return redirect(url_for('admin_system_management'))

@app.route('/admin/reset_points')
def admin_reset_points():
    """Reset all behavior points for all students."""
    if 'user' not in session or not session.get('is_admin'):
        flash("Access denied. Admin privileges required.", "danger")
        return redirect(url_for('home'))
    
    try:
        conn = get_db_connection()
        
        # Set all behavior points to 0
        conn.execute("UPDATE lab_sessions SET behavior_points = 0 WHERE behavior_points > 0")
        
        # Log the action
        conn.execute("""
            INSERT INTO admin_logs (admin_id, action, timestamp)
            VALUES (?, 'Reset all behavior points', datetime('now'))
        """, (session['user'],))
        
        conn.commit()
        conn.close()
        
        flash("All student behavior points have been reset.", "success")
    except sqlite3.Error as e:
        flash(f"Database error: {str(e)}", "danger")
    
    return redirect(url_for('admin_system_management'))

@app.route('/admin/reset_all')
def admin_reset_all():
    """Reset both sessions and behavior points for all students."""
    if 'user' not in session or not session.get('is_admin'):
        flash("Access denied. Admin privileges required.", "danger")
        return redirect(url_for('home'))
    
    try:
        conn = get_db_connection()
        
        # Delete all completed lab sessions (automatically removes their behavior points)
        conn.execute("DELETE FROM lab_sessions WHERE status = 'Completed'")
        
        # Also reset behavior points for any active sessions
        conn.execute("UPDATE lab_sessions SET behavior_points = 0 WHERE behavior_points > 0")
        
        # Log the action
        conn.execute("""
            INSERT INTO admin_logs (admin_id, action, timestamp)
            VALUES (?, 'Reset all student data (sessions and points)', datetime('now'))
        """, (session['user'],))
        
        conn.commit()
        conn.close()
        
        flash("All student data (sessions and behavior points) has been reset.", "success")
    except sqlite3.Error as e:
        flash(f"Database error: {str(e)}", "danger")
    
    return redirect(url_for('admin_system_management'))

@app.route('/admin/system')
def admin_system_management():
    """Admin system management page."""
    if 'user' not in session or not session.get('is_admin'):
        flash("Access denied. Admin privileges required.", "danger")
        return redirect(url_for('home'))
    
    return render_template('admin_system_management.html')

@app.route('/admin/backup_database')
def admin_backup_database():
    """Download a backup of the database."""
    if 'user' not in session or not session.get('is_admin'):
        flash("Access denied. Admin privileges required.", "danger")
        return redirect(url_for('home'))
    
    try:
        # Create a copy of the database file
        import os
        import shutil
        from datetime import datetime
        import io
        
        # Get the database file path
        db_path = 'users.db'
        
        # Create a backup file name with timestamp
        timestamp = datetime.now().strftime('%Y-%m-%d_%H%M%S')
        backup_filename = f"backup_{timestamp}.db"
        
        # Read the database file into memory
        with open(db_path, 'rb') as f:
            db_data = f.read()
        
        # Create a memory file-like object
        memory_file = io.BytesIO(db_data)
        memory_file.seek(0)
        
        # Log the action
        conn = get_db_connection()
        conn.execute("""
            INSERT INTO admin_logs (admin_id, action, timestamp)
            VALUES (?, 'Database backup created', datetime('now'))
        """, (session['user'],))
        conn.commit()
        conn.close()
        
        # Send the file as download attachment
        return send_file(
            memory_file,
            mimetype='application/octet-stream',
            as_attachment=True,
            download_name=backup_filename
        )
    except Exception as e:
        flash(f"Error creating database backup: {str(e)}", "danger")
        return redirect(url_for('admin_system_management'))

@app.route('/admin/restore_database', methods=['POST'])
def admin_restore_database():
    """Restore the database from a backup file."""
    if 'user' not in session or not session.get('is_admin'):
        flash("Access denied. Admin privileges required.", "danger")
        return redirect(url_for('home'))
    
    if 'database_file' not in request.files:
        flash("No file selected for upload.", "warning")
        return redirect(url_for('admin_system_management'))
    
    file = request.files['database_file']
    
    if file.filename == '':
        flash("No file selected for upload.", "warning")
        return redirect(url_for('admin_system_management'))
    
    try:
        import os
        import shutil
        from datetime import datetime
        
        # Get the database file path
        db_path = 'users.db'
        
        # Create a backup of the current database before replacing it
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_path = f"auto_backup_before_restore_{timestamp}.db"
        shutil.copy2(db_path, backup_path)
        
        # Save the uploaded file as the new database
        file.save(db_path)
        
        # No need to log in the restored database as it might not have the same schema
        # but we can try
        try:
            conn = get_db_connection()
            conn.execute("""
                INSERT INTO admin_logs (admin_id, action, timestamp)
                VALUES (?, 'Database restored from backup', datetime('now'))
            """, (session['user'],))
            conn.commit()
            conn.close()
        except:
            # If this fails, it's not critical - the restore already happened
            pass
        
        flash("Database successfully restored from backup file.", "success")
    except Exception as e:
        flash(f"Error restoring database: {str(e)}", "danger")
    
    return redirect(url_for('admin_system_management'))

if __name__ == '__main__':
    app.run(debug=True)