"""
SMS Dashboard - Flask Backend
Reads SMS from a GSM modem via serial port, stores them in SQLite,
and exposes a REST API for the web frontend.
"""

import os
import re
import time
import logging
import sqlite3
import threading
from datetime import datetime

import serial
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SERIAL_PORT = os.environ.get("SERIAL_PORT", "/dev/ttyUSB0")
SERIAL_BAUD = int(os.environ.get("SERIAL_BAUD", "9600"))
DB_PATH = os.environ.get("DB_PATH", "/data/sms.db")
STATIC_DIR = os.environ.get("STATIC_DIR", "/app/static")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("sms-dashboard")

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(__name__, static_folder=STATIC_DIR)
CORS(app)

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def get_db():
    """Return a new SQLite connection (caller must close it)."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they do not exist."""
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                sender    TEXT    NOT NULL,
                content   TEXT    NOT NULL,
                received  TEXT    NOT NULL,
                read      INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.commit()
    logger.info("Database initialised at %s", DB_PATH)


def save_message(sender: str, content: str, received: str) -> int:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO messages (sender, content, received) VALUES (?, ?, ?)",
            (sender, content, received),
        )
        conn.commit()
        return cur.lastrowid


# ---------------------------------------------------------------------------
# SMS / Serial helpers
# ---------------------------------------------------------------------------
def send_at(ser: serial.Serial, command: str, timeout: float = 3.0) -> str:
    """Send an AT command and return the response string."""
    ser.write((command + "\r\n").encode())
    time.sleep(0.2)
    deadline = time.time() + timeout
    response = ""
    while time.time() < deadline:
        if ser.in_waiting:
            chunk = ser.read(ser.in_waiting).decode(errors="replace")
            response += chunk
            if "OK" in response or "ERROR" in response:
                break
        time.sleep(0.05)
    return response.strip()


def parse_sms_list(raw: str):
    """
    Parse the output of AT+CMGL="ALL" into a list of dicts.
    Returns list of {index, status, sender, timestamp, content}.
    """
    messages = []
    lines = raw.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        # Header line: +CMGL: <index>,<status>,<sender>,<alpha>,<timestamp>
        match = re.match(
            r'\+CMGL:\s*(\d+),"([^"]+)","([^"]*)"(?:,"([^"]*)")*,\s*"([^"]+)"',
            line,
        )
        if match:
            idx = match.group(1)
            status = match.group(2)
            sender = match.group(3)
            timestamp = match.group(5)
            content = lines[i + 1].strip() if i + 1 < len(lines) else ""
            messages.append(
                {
                    "index": idx,
                    "status": status,
                    "sender": sender,
                    "timestamp": timestamp,
                    "content": content,
                }
            )
            i += 2
        else:
            i += 1
    return messages


def open_serial() -> serial.Serial | None:
    """Try to open the serial port; return None if unavailable."""
    try:
        ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=1)
        logger.info("Opened serial port %s at %d baud", SERIAL_PORT, SERIAL_BAUD)
        return ser
    except serial.SerialException as exc:
        logger.warning("Cannot open serial port %s: %s", SERIAL_PORT, exc)
        return None


def modem_setup(ser: serial.Serial):
    """Initialise the modem for SMS reading."""
    send_at(ser, "AT")           # basic check
    send_at(ser, "AT+CMGF=1")   # text mode
    send_at(ser, 'AT+CSCS="GSM"')  # GSM character set
    logger.info("Modem initialised")


def poll_messages(ser: serial.Serial):
    """Read all messages from modem and persist new ones."""
    raw = send_at(ser, 'AT+CMGL="ALL"', timeout=5)
    parsed = parse_sms_list(raw)
    for msg in parsed:
        # Use modem timestamp or current time
        try:
            received = datetime.strptime(msg["timestamp"][:17], "%y/%m/%d,%H:%M:%S").isoformat()
        except (ValueError, IndexError):
            received = datetime.utcnow().isoformat()
        save_message(msg["sender"], msg["content"], received)
        # Delete from modem storage after saving
        send_at(ser, f'AT+CMGD={msg["index"]}')
    if parsed:
        logger.info("Stored %d new message(s)", len(parsed))


# ---------------------------------------------------------------------------
# Background SMS polling thread
# ---------------------------------------------------------------------------
def sms_reader_thread():
    """Background thread that polls the modem every 30 seconds."""
    retry_delay = 10
    ser = None
    while True:
        if ser is None:
            ser = open_serial()
            if ser is None:
                logger.debug("Serial unavailable, retrying in %ds", retry_delay)
                time.sleep(retry_delay)
                continue
            try:
                modem_setup(ser)
            except Exception as exc:
                logger.error("Modem setup failed: %s", exc)
                ser.close()
                ser = None
                time.sleep(retry_delay)
                continue

        try:
            poll_messages(ser)
            time.sleep(30)
        except serial.SerialException as exc:
            logger.error("Serial error: %s – will reconnect", exc)
            ser.close()
            ser = None
            time.sleep(retry_delay)
        except Exception as exc:
            logger.error("Unexpected error in SMS reader: %s", exc, exc_info=True)
            time.sleep(retry_delay)


# ---------------------------------------------------------------------------
# REST API endpoints
# ---------------------------------------------------------------------------
@app.route("/api/messages", methods=["GET"])
def get_messages():
    """
    Return SMS messages.
    Query params:
      - limit  (int, default 50)
      - offset (int, default 0)
      - unread (bool, return only unread messages)
    """
    limit = min(int(request.args.get("limit", 50)), 200)
    offset = int(request.args.get("offset", 0))
    unread_only = request.args.get("unread", "").lower() in ("1", "true")

    with get_db() as conn:
        if unread_only:
            total = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE read=0"
            ).fetchone()[0]
            rows = conn.execute(
                """
                SELECT id, sender, content, received, read
                FROM messages WHERE read=0
                ORDER BY received DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        else:
            total = conn.execute(
                "SELECT COUNT(*) FROM messages"
            ).fetchone()[0]
            rows = conn.execute(
                """
                SELECT id, sender, content, received, read
                FROM messages
                ORDER BY received DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()

    return jsonify(
        {
            "total": total,
            "limit": limit,
            "offset": offset,
            "messages": [dict(r) for r in rows],
        }
    )


@app.route("/api/messages/<int:msg_id>", methods=["GET"])
def get_message(msg_id: int):
    """Return a single message by id."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, sender, content, received, read FROM messages WHERE id=?",
            (msg_id,),
        ).fetchone()
    if row is None:
        return jsonify({"error": "Message not found"}), 404
    return jsonify(dict(row))


@app.route("/api/messages/<int:msg_id>/read", methods=["POST"])
def mark_read(msg_id: int):
    """Mark a message as read."""
    with get_db() as conn:
        result = conn.execute(
            "UPDATE messages SET read=1 WHERE id=?", (msg_id,)
        )
        conn.commit()
    if result.rowcount == 0:
        return jsonify({"error": "Message not found"}), 404
    return jsonify({"success": True})


@app.route("/api/messages/<int:msg_id>", methods=["DELETE"])
def delete_message(msg_id: int):
    """Delete a message."""
    with get_db() as conn:
        result = conn.execute("DELETE FROM messages WHERE id=?", (msg_id,))
        conn.commit()
    if result.rowcount == 0:
        return jsonify({"error": "Message not found"}), 404
    return jsonify({"success": True})


@app.route("/api/stats", methods=["GET"])
def get_stats():
    """Return summary statistics."""
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        unread = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE read=0"
        ).fetchone()[0]
        latest = conn.execute(
            "SELECT received FROM messages ORDER BY received DESC LIMIT 1"
        ).fetchone()
    return jsonify(
        {
            "total": total,
            "unread": unread,
            "read": total - unread,
            "latest": latest[0] if latest else None,
            "serial_port": SERIAL_PORT,
        }
    )


@app.route("/api/health", methods=["GET"])
def health():
    """Health-check endpoint."""
    return jsonify({"status": "ok", "timestamp": datetime.utcnow().isoformat()})


# ---------------------------------------------------------------------------
# Serve static frontend
# ---------------------------------------------------------------------------
@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_static(path):
    if path and os.path.exists(os.path.join(STATIC_DIR, path)):
        return send_from_directory(STATIC_DIR, path)
    return send_from_directory(STATIC_DIR, "index.html")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    init_db()
    t = threading.Thread(target=sms_reader_thread, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=5000, debug=False)
