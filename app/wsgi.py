"""WSGI entry-point used by gunicorn."""
import threading
from app import app, init_db, sms_reader_thread

init_db()
t = threading.Thread(target=sms_reader_thread, daemon=True)
t.start()
