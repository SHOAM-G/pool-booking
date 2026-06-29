"""
The Snooker Villa — shared pool/snooker table booking backend.
Powered by SHOAM: The Local Marketplace.

Stack: Flask + Google Sheets (persistence) + SMTP (email on interest).
One deploy serves both the API and the page (static/index.html).

Required environment variables (set these on Railway):
  GOOGLE_CREDENTIALS  -> the full service-account JSON (paste as one string)
  SHEET_ID            -> the Google Sheet ID (from its URL)
  ADMIN_PASSWORD      -> password for removing bookings / match-ups (CHANGE THIS)

Optional (enables real emails; without them, interest is still logged to the sheet):
  SMTP_HOST  SMTP_PORT  SMTP_USER  SMTP_PASS  FROM_EMAIL  FROM_NAME
"""

import os
import json
import time
import threading
import smtplib
import re
import uuid
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import gspread
from google.oauth2.service_account import Credentials

app = Flask(__name__, static_folder="static")
CORS(app)

# ---------------------------------------------------------------- config
SHEET_ID = os.environ.get("SHEET_ID", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "snooker123")  # <-- CHANGE in Railway
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")  # where new-booking alerts go
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
IST = timezone(timedelta(hours=5, minutes=30))  # hall's local time

BOOK_HEADERS     = ["Date", "Table", "Hour", "Name", "Phone", "Timestamp"]
PLAYER_HEADERS   = ["Id", "Name", "Level", "Phone", "Email", "Time", "Note", "Timestamp"]
INTEREST_HEADERS = ["PlayerId", "PlayerName", "FromName", "FromContact", "Message", "Timestamp"]
VALID_LEVELS = {"beginner", "intermediate", "pro"}

# ---------------------------------------------------------------- sheets
_sh = None
_lock = threading.Lock()
_cache = {"data": None, "ts": 0}
_last_purge = 0
CACHE_TTL = 8
PURGE_EVERY = 3600  # seconds


def today_ist():
    return datetime.now(IST).strftime("%Y-%m-%d")


def get_sheet():
    global _sh
    if _sh:
        return _sh
    raw = os.environ.get("GOOGLE_CREDENTIALS", "")
    if not raw:
        raise RuntimeError("GOOGLE_CREDENTIALS env var is not set")
    creds = Credentials.from_service_account_info(json.loads(raw), scopes=SCOPES)
    _sh = gspread.authorize(creds).open_by_key(SHEET_ID)
    return _sh


def ws(name, headers):
    sh = get_sheet()
    try:
        return sh.worksheet(name)
    except gspread.WorksheetNotFound:
        w = sh.add_worksheet(title=name, rows=2000, cols=len(headers))
        w.append_row(headers)
        return w


def purge_past():
    """Drop bookings for dates before today (keeps the rolling week tidy)."""
    global _last_purge
    now = time.time()
    if now - _last_purge < PURGE_EVERY:
        return
    _last_purge = now
    today = today_ist()
    with _lock:
        book_w = ws("Bookings", BOOK_HEADERS)
        records = book_w.get_all_records()
        stale = [i + 2 for i, r in enumerate(records)
                 if str(r.get("Date", "")).strip() and str(r.get("Date", "")).strip() < today]
        for idx in sorted(stale, reverse=True):  # bottom-up so indices stay valid
            book_w.delete_rows(idx)
        if stale:
            bust_cache()


def read_state(fresh=False):
    now = time.time()
    if not fresh and _cache["data"] is not None and now - _cache["ts"] < CACHE_TTL:
        return _cache["data"]

    today = today_ist()
    book_w = ws("Bookings", BOOK_HEADERS)
    play_w = ws("Players", PLAYER_HEADERS)

    bookings = []
    for r in book_w.get_all_records():
        d = str(r.get("Date", "")).strip()
        if not d or d < today:           # hide past days from the window
            continue
        bookings.append({
            "date": d,
            "table": int(r.get("Table", 0) or 0),
            "hour": int(r.get("Hour", 0) or 0),
            "name": str(r.get("Name", "")).strip(),
            "phone": str(r.get("Phone", "")).strip(),
        })

    players = []
    for r in play_w.get_all_records():
        pid = str(r.get("Id", "")).strip()
        if not pid:
            continue
        # contact (phone/email) is NOT sent to the client — only released on interest
        players.append({
            "id": pid,
            "name": str(r.get("Name", "")).strip(),
            "level": str(r.get("Level", "")).strip(),
            "time": str(r.get("Time", "")).strip(),
            "note": str(r.get("Note", "")).strip(),
            "ts": r.get("Timestamp", ""),
        })
    players.reverse()

    data = {"bookings": bookings, "players": players}
    _cache["data"] = data
    _cache["ts"] = now
    return data


def bust_cache():
    _cache["data"] = None
    _cache["ts"] = 0


# ---------------------------------------------------------------- email / auth
def send_email(to_addr, subject, body):
    host = os.environ.get("SMTP_HOST")
    if not host or not to_addr:
        return False
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    pwd = os.environ.get("SMTP_PASS", "")
    from_email = os.environ.get("FROM_EMAIL", user)
    from_name = os.environ.get("FROM_NAME", "The Snooker Villa")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = to_addr
    msg.set_content(body)
    with smtplib.SMTP(host, port, timeout=20) as s:
        s.starttls()
        if user:
            s.login(user, pwd)
        s.send_message(msg)
    return True


def is_admin():
    return bool(ADMIN_PASSWORD) and request.headers.get("X-Admin-Password", "") == ADMIN_PASSWORD


def clean(s, n=200):
    return str(s or "").strip()[:n]


EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PHONE_RE = re.compile(r"^[0-9+\-\s]{7,20}$")


# ---------------------------------------------------------------- routes
@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/state")
def api_state():
    try:
        purge_past()
        return jsonify(read_state())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/login", methods=["POST"])
def api_admin_login():
    d = request.get_json(force=True, silent=True) or {}
    ok = bool(ADMIN_PASSWORD) and clean(d.get("password"), 100) == ADMIN_PASSWORD
    return (jsonify({"ok": True}), 200) if ok else (jsonify({"error": "Wrong password"}), 401)


@app.route("/api/bookings", methods=["POST"])
def api_book():
    d = request.get_json(force=True, silent=True) or {}
    date = clean(d.get("date"), 10)
    name = clean(d.get("name"), 40)
    phone = clean(d.get("phone"), 20)
    try:
        table, hour = int(d.get("table")), int(d.get("hour"))
    except (TypeError, ValueError):
        return jsonify({"error": "table and hour must be numbers"}), 400
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        return jsonify({"error": "valid date is required"}), 400
    if not name:
        return jsonify({"error": "name is required"}), 400
    if not PHONE_RE.match(phone):
        return jsonify({"error": "a valid mobile number is required"}), 400

    with _lock:
        book_w = ws("Bookings", BOOK_HEADERS)
        for r in book_w.get_all_records():
            if (str(r.get("Date", "")).strip() == date
                    and int(r.get("Table", 0) or 0) == table
                    and int(r.get("Hour", 0) or 0) == hour):
                return jsonify({"error": "That slot was just taken."}), 409
        book_w.append_row([date, table, hour, name, phone,
                           datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")])
        bust_cache()
    # notify the admin (works even when no one has the page open)
    label = clean(d.get("label"), 120) or (date + " · table " + str(table) + " · " + str(hour) + ":00")
    try:
        if ADMIN_EMAIL:
            send_email(ADMIN_EMAIL, "New booking — " + label,
                       "New booking at The Snooker Villa\n\n" + label +
                       "\nName: " + name + "\nPhone: " + phone +
                       "\n\n— Powered by SHOAM: The Local Marketplace")
    except Exception as e:
        app.logger.warning("admin notify failed: %s", e)
    return jsonify({"ok": True})


@app.route("/api/bookings/cancel", methods=["POST"])
def api_cancel():
    if not is_admin():
        return jsonify({"error": "Admin only"}), 401
    d = request.get_json(force=True, silent=True) or {}
    date = clean(d.get("date"), 10)
    try:
        table, hour = int(d.get("table")), int(d.get("hour"))
    except (TypeError, ValueError):
        return jsonify({"error": "table and hour must be numbers"}), 400
    with _lock:
        book_w = ws("Bookings", BOOK_HEADERS)
        for i, r in enumerate(book_w.get_all_records()):
            if (str(r.get("Date", "")).strip() == date
                    and int(r.get("Table", 0) or 0) == table
                    and int(r.get("Hour", 0) or 0) == hour):
                book_w.delete_rows(i + 2)
                bust_cache()
                return jsonify({"ok": True})
    return jsonify({"error": "booking not found"}), 404


@app.route("/api/players", methods=["POST"])
def api_add_player():
    d = request.get_json(force=True, silent=True) or {}
    name = clean(d.get("name"), 40)
    level = clean(d.get("level"), 20).lower()
    phone = clean(d.get("phone"), 20)
    email = clean(d.get("email"), 80)
    when = clean(d.get("time"), 80)
    note = clean(d.get("note"), 140)
    if not name:
        return jsonify({"error": "name is required"}), 400
    if level not in VALID_LEVELS:
        return jsonify({"error": "invalid level"}), 400
    if not PHONE_RE.match(phone):
        return jsonify({"error": "a valid mobile number is required"}), 400
    if email and not EMAIL_RE.match(email):
        return jsonify({"error": "that email looks invalid"}), 400

    pid = "p" + uuid.uuid4().hex[:12]
    with _lock:
        play_w = ws("Players", PLAYER_HEADERS)
        play_w.append_row([pid, name, level, phone, email, when, note,
                           datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")])
        bust_cache()
    return jsonify({"ok": True, "id": pid})


@app.route("/api/players/remove", methods=["POST"])
def api_remove_player():
    if not is_admin():
        return jsonify({"error": "Admin only"}), 401
    d = request.get_json(force=True, silent=True) or {}
    pid = clean(d.get("id"), 40)
    with _lock:
        play_w = ws("Players", PLAYER_HEADERS)
        for i, r in enumerate(play_w.get_all_records()):
            if str(r.get("Id", "")).strip() == pid:
                play_w.delete_rows(i + 2)
                bust_cache()
                return jsonify({"ok": True})
    return jsonify({"error": "player not found"}), 404


@app.route("/api/interest", methods=["POST"])
def api_interest():
    d = request.get_json(force=True, silent=True) or {}
    pid = clean(d.get("playerId"), 40)
    from_name = clean(d.get("fromName"), 40)
    from_contact = clean(d.get("fromContact"), 80)
    message = clean(d.get("message"), 300)
    if not from_name or not from_contact:
        return jsonify({"error": "Please add your name and a contact."}), 400

    play_w = ws("Players", PLAYER_HEADERS)
    target = None
    for r in play_w.get_all_records():
        if str(r.get("Id", "")).strip() == pid:
            target = r
            break
    if not target:
        return jsonify({"error": "player no longer on the rail"}), 404

    to_email = str(target.get("Email", "")).strip()
    to_phone = str(target.get("Phone", "")).strip()
    to_name = str(target.get("Name", "")).strip()

    with _lock:
        ws("Interests", INTEREST_HEADERS).append_row(
            [pid, to_name, from_name, from_contact, message,
             datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")])

    body = (f"Hi {to_name},\n\n{from_name} saw your match-up on The Snooker Villa "
            f"and wants a game.\n\nReach them at: {from_contact}\n"
            f"Their message: {message or '(none)'}\n\n"
            f"— The Snooker Villa, Powered by SHOAM: The Local Marketplace")
    emailed = False
    try:
        emailed = send_email(to_email, "Someone wants a match!", body)
    except Exception as e:
        app.logger.warning("email failed: %s", e)

    # release the poster's contact so the interested player can connect now
    return jsonify({"ok": True, "emailed": emailed,
                    "contact": {"name": to_name, "phone": to_phone, "email": to_email}})


@app.route("/health")
def health():
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
