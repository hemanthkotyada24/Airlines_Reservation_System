from flask import Flask, request, jsonify, render_template, redirect, url_for, session, send_file
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
import mysql.connector
import sqlite3
import random
import string
import os
import tempfile
from datetime import datetime

from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

app = Flask(__name__)

# ---------------- SECRET KEY ----------------
app.secret_key = os.getenv("SECRET_KEY", "supersecretkey123")
app.config["SESSION_PERMANENT"] = False

# ---------------- DATABASE CONNECTION ----------------

_sqlite_path = None

def get_sqlite_path():
    global _sqlite_path
    if _sqlite_path is None:
        _sqlite_path = os.path.join(
            os.path.dirname(__file__),
            "flight_system.sqlite"
        )
    return _sqlite_path

def initialize_sqlite():
    """Initialize SQLite database on app startup."""
    try:
        conn = sqlite3.connect(get_sqlite_path(), check_same_thread=False, timeout=10)
        cursor = conn.cursor()

        # USERS TABLE
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password TEXT
            )
        """)

        # FLIGHTS TABLE
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS flights (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                flight_name TEXT UNIQUE,
                source TEXT,
                destination TEXT,
                seats INTEGER
            )
        """)

        # BOOKINGS TABLE
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                pnr TEXT PRIMARY KEY,
                username TEXT,
                passenger_name TEXT,
                flight_no TEXT,
                booking_date TEXT
            )
        """)

        conn.commit()

        # DEFAULT USERS
        users = [
            ("admin", generate_password_hash("admin123")),
            ("user", generate_password_hash("user123"))
        ]

        for user in users:
            cursor.execute(
                "INSERT OR IGNORE INTO users (username, password) VALUES (?, ?)",
                user
            )

        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Warning: SQLite initialization failed: {e}")

def get_db_connection():
    host = os.getenv("MYSQL_HOST", "localhost")
    port = int(os.getenv("MYSQL_PORT", 3306))
    user = os.getenv("MYSQL_USER", "root")
    password = os.getenv("MYSQL_PASSWORD")
    database = os.getenv("MYSQL_DATABASE", "flight_system")

    mysql_kwargs = {
        "host": host,
        "port": port,
        "user": user,
        "database": database,
    }

    if password:
        mysql_kwargs["password"] = password

    try:
        conn = mysql.connector.connect(**mysql_kwargs)
        return conn

    except mysql.connector.Error:
        conn = sqlite3.connect(get_sqlite_path(), check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn


def db_execute(cursor, query, params=None):

    if isinstance(cursor.connection, sqlite3.Connection):
        query = query.replace("%s", "?")

    if params:
        return cursor.execute(query, params)

    return cursor.execute(query)

# ---------------- LOGIN REQUIRED ----------------
def login_required(f):

    @wraps(f)
    def decorated_function(*args, **kwargs):

        if "username" not in session:
            return redirect(url_for("login"))

        return f(*args, **kwargs)

    return decorated_function

# ---------------- GENERATE PNR ----------------
def generate_pnr():

    conn = get_db_connection()
    cursor = conn.cursor()

    try:

        while True:

            pnr = ''.join(
                random.choices(
                    string.ascii_uppercase + string.digits,
                    k=6
                )
            )

            db_execute(
                cursor,
                "SELECT pnr FROM bookings WHERE pnr=%s",
                (pnr,)
            )

            if not cursor.fetchone():
                return pnr

    finally:
        conn.close()

# Initialize database once before first request
_db_initialized = False

@app.before_request
def before_request():
    global _db_initialized
    if not _db_initialized:
        initialize_sqlite()
        _db_initialized = True

# ---------------- HOME ----------------
@app.route("/")
def home():

    if "username" in session:
        return redirect(url_for("dashboard"))

    return redirect(url_for("login"))

# ---------------- DASHBOARD ----------------
@app.route("/dashboard")
@login_required
def dashboard():
    conn = get_db_connection()
    cursor = conn.cursor()

    db_execute(
        cursor,
        "SELECT COUNT(*) FROM bookings WHERE username=%s",
        (session.get("username"),)
    )

    booking_count_row = cursor.fetchone()
    booking_count = booking_count_row[0] if booking_count_row else 0

    db_execute(
        cursor,
        "SELECT * FROM bookings WHERE username=%s ORDER BY booking_date DESC LIMIT 5",
        (session.get("username"),)
    )

    recent_bookings = cursor.fetchall()
    conn.close()

    bookings = []
    for row in recent_bookings:
        bookings.append({
            "pnr": row[0],
            "passenger_name": row[2],
            "flight_no": row[3],
            "booking_date": row[4]
        })

    return render_template(
        "dashboard.html",
        username=session.get("username"),
        booking_count=booking_count,
        recent_bookings=bookings
    )

# ---------------- REGISTER ----------------
@app.route("/register", methods=["GET", "POST"])
def register():

    if "username" in session:
        return redirect(url_for("book"))

    message = ""

    if request.method == "POST":

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:
            message = "Please enter both username and password"
        elif len(username) < 3:
            message = "Username must be at least 3 characters"
        elif len(password) < 6:
            message = "Password must be at least 6 characters"
        else:

            conn = get_db_connection()
            cursor = conn.cursor()

            db_execute(
                cursor,
                "SELECT * FROM users WHERE username=%s",
                (username,)
            )

            existing = cursor.fetchone()

            if existing:
                message = "Username already exists"
                conn.close()
            else:
                hashed_password = generate_password_hash(password)

                db_execute(
                    cursor,
                    "INSERT INTO users (username, password) VALUES (%s, %s)",
                    (username, hashed_password)
                )

                conn.commit()
                conn.close()

                return render_template(
                    "register_success.html",
                    username=username
                )

    return render_template("register.html", message=message)

# ---------------- LOGIN ----------------
@app.route("/login", methods=["GET", "POST"])
def login():

    if "username" in session:
        return redirect(url_for("book"))

    message = ""

    if request.method == "POST":

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        conn = get_db_connection()
        cursor = conn.cursor()

        db_execute(
            cursor,
            "SELECT password FROM users WHERE username=%s",
            (username,)
        )

        user = cursor.fetchone()

        conn.close()

        if user:

            stored_password = user[0]

            if check_password_hash(stored_password, password):

                session["username"] = username

                return redirect(url_for("dashboard"))

        message = "Invalid username or password"

    return render_template("index.html", message=message)

# ---------------- LOGOUT ----------------
@app.route("/logout")
def logout():

    session.pop("username", None)

    return redirect(url_for("login"))

# ---------------- ADD SAMPLE FLIGHTS ----------------
@app.route("/add_flights")
def add_flights():

    conn = get_db_connection()
    cursor = conn.cursor()

    flights = [
        ("AI101", "Bengaluru", "Dubai", 12),
        ("SG502", "Mumbai", "Singapore", 7),
        ("6E712", "Chennai", "Goa", 14),
        ("UK215", "Delhi", "London", 8),
        ("AI803", "Hyderabad", "Kolkata", 10),
        ("SG430", "Pune", "Bangkok", 5)
    ]

    for flight in flights:

        db_execute(
            cursor,
            "SELECT * FROM flights WHERE flight_name=%s",
            (flight[0],)
        )

        existing = cursor.fetchone()

        if not existing:

            db_execute(
                cursor,
                """
                INSERT INTO flights
                (flight_name, source, destination, seats)
                VALUES (%s, %s, %s, %s)
                """,
                flight
            )

    conn.commit()
    conn.close()

    return jsonify({"message": "Flights added successfully"})

# ---------------- GET AVAILABLE FLIGHTS ----------------
def get_available_flights():

    conn = get_db_connection()
    cursor = conn.cursor()

    db_execute(cursor, "SELECT * FROM flights")

    data = cursor.fetchall()

    conn.close()

    flights = []

    for row in data:

        flights.append({
            "flight_name": row[1],
            "source": row[2],
            "destination": row[3],
            "seats": row[4]
        })

    return flights

# ---------------- SEARCH FLIGHTS ----------------
@app.route("/search")
@login_required
def search_flights():

    source = request.args.get("source")
    destination = request.args.get("destination")

    conn = get_db_connection()
    cursor = conn.cursor()

    db_execute(
        cursor,
        """
        SELECT * FROM flights
        WHERE source=%s AND destination=%s
        """,
        (source, destination)
    )

    data = cursor.fetchall()

    conn.close()

    flights = []

    for row in data:

        flights.append({
            "flight_name": row[1],
            "source": row[2],
            "destination": row[3],
            "seats": row[4]
        })

    return jsonify(flights)

# ---------------- BOOK TICKET ----------------
@app.route("/book", methods=["GET", "POST"])
@login_required
def book():

    flights = get_available_flights()

    if request.method == "GET":

        return render_template(
            "book.html",
            username=session.get("username"),
            flights=flights,
            error=None,
            selected_flight=None
        )

    passenger_name = request.form.get("name", "").strip()
    flight_no = request.form.get("flight_name")

    if not passenger_name:

        return render_template(
            "book.html",
            username=session.get("username"),
            flights=flights,
            error="Please enter passenger name",
            selected_flight=flight_no
        )

    conn = get_db_connection()
    cursor = conn.cursor()

    # SAFE SEAT UPDATE
    db_execute(
        cursor,
        """
        UPDATE flights
        SET seats = seats - 1
        WHERE flight_name=%s AND seats > 0
        """,
        (flight_no,)
    )

    if cursor.rowcount == 0:

        conn.close()

        return render_template(
            "book.html",
            username=session.get("username"),
            flights=flights,
            error="No seats available",
            selected_flight=flight_no
        )

    pnr = generate_pnr()

    booking_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    db_execute(
        cursor,
        """
        INSERT INTO bookings
        (pnr, username, passenger_name, flight_no, booking_date)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (
            pnr,
            session.get("username"),
            passenger_name,
            flight_no,
            booking_date
        )
    )

    conn.commit()
    conn.close()

    return render_template(
        "success.html",
        message="Booking Successful",
        name=passenger_name,
        flight_no=flight_no,
        pnr=pnr,
        username=session.get("username")
    )

# ---------------- MY BOOKINGS ----------------
@app.route("/my_bookings")
@login_required
def my_bookings():

    conn = get_db_connection()
    cursor = conn.cursor()

    db_execute(
        cursor,
        """
        SELECT * FROM bookings
        WHERE username=%s
        """,
        (session.get("username"),)
    )

    data = cursor.fetchall()

    conn.close()

    bookings = []

    for row in data:

        bookings.append({
            "pnr": row[0],
            "username": row[1],
            "passenger_name": row[2],
            "flight_no": row[3],
            "booking_date": row[4]
        })

    return jsonify(bookings)

# ---------------- CANCEL BOOKING ----------------
@app.route("/cancel/<pnr>")
@login_required
def cancel_booking(pnr):

    conn = get_db_connection()
    cursor = conn.cursor()

    db_execute(
        cursor,
        "SELECT flight_no FROM bookings WHERE pnr=%s",
        (pnr,)
    )

    booking = cursor.fetchone()

    if not booking:
        conn.close()
        return "Booking not found"

    flight_no = booking[0]

    db_execute(
        cursor,
        "DELETE FROM bookings WHERE pnr=%s",
        (pnr,)
    )

    db_execute(
        cursor,
        """
        UPDATE flights
        SET seats = seats + 1
        WHERE flight_name=%s
        """,
        (flight_no,)
    )

    conn.commit()
    conn.close()

    return f"Booking {pnr} cancelled successfully"

# ---------------- DOWNLOAD TICKET ----------------
@app.route("/download_ticket/<pnr>")
@login_required
def download_ticket(pnr):

    conn = get_db_connection()
    cursor = conn.cursor()

    db_execute(
        cursor,
        "SELECT * FROM bookings WHERE pnr=%s",
        (pnr,)
    )

    data = cursor.fetchone()

    conn.close()

    if not data:
        return "Ticket not found"

    temp = tempfile.NamedTemporaryFile(
        delete=False,
        suffix=".pdf"
    )

    file_name = temp.name

    doc = SimpleDocTemplate(file_name)

    styles = getSampleStyleSheet()

    content = []

    content.append(
        Paragraph("✈ AIRLINE TICKET", styles['Title'])
    )

    content.append(Spacer(1, 12))

    content.append(
        Paragraph(f"PNR: {data[0]}", styles['Normal'])
    )

    content.append(
        Paragraph(f"Username: {data[1]}", styles['Normal'])
    )

    content.append(
        Paragraph(f"Passenger Name: {data[2]}", styles['Normal'])
    )

    content.append(
        Paragraph(f"Flight No: {data[3]}", styles['Normal'])
    )

    content.append(
        Paragraph(f"Booking Date: {data[4]}", styles['Normal'])
    )

    doc.build(content)

    return send_file(
        file_name,
        as_attachment=True,
        download_name=f"ticket_{pnr}.pdf"
    )

# ---------------- ADMIN VIEW ALL BOOKINGS ----------------
@app.route("/all_bookings")
@login_required
def all_bookings():

    if session.get("username") != "admin":
        return "Access Denied"

    conn = get_db_connection()
    cursor = conn.cursor()

    db_execute(cursor, "SELECT * FROM bookings")

    data = cursor.fetchall()

    conn.close()

    bookings = []

    for row in data:

        bookings.append({
            "pnr": row[0],
            "username": row[1],
            "passenger_name": row[2],
            "flight_no": row[3],
            "booking_date": row[4]
        })

    return jsonify(bookings)

# ---------------- RUN APP ----------------
if __name__ == "__main__":
    initialize_sqlite()
    app.run(debug=True)