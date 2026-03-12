from flask import Flask, render_template, request, jsonify, send_from_directory
import os
import json
import serial
import time
from datetime import datetime
import sys
import threading
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import ssl
import requests
from requests.auth import HTTPBasicAuth

# Serve templates from repo root (as you already do)
app = Flask(__name__, template_folder='.')

# Setup logging
log_file = '/app/data/modem.log'
os.makedirs('/app/data', exist_ok=True)

# Forwarding config files
forwarding_config_file = '/app/data/forwarding_config.json'
gatewayapi_config_file = '/app/data/gatewayapi_config.json'

def log_message(message):
    """Log to both console and file"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_line = f"[{timestamp}] {message}"
    print(log_line, flush=True)
    sys.stdout.flush()
    try:
        with open(log_file, 'a') as f:
            f.write(log_line + '\n')
    except:
        pass

# ---- NEW: favicon routes ----
@app.route('/favicon.svg')
def favicon_svg():
    # index.html and app.py are at repo root, so serve from same folder as this file
    root_dir = os.path.dirname(os.path.abspath(__file__))
    return send_from_directory(root_dir, 'favicon.svg', mimetype='image/svg+xml')

@app.route('/favicon.ico')
def favicon_ico():
    # Optional: only works if you add favicon.ico
    root_dir = os.path.dirname(os.path.abspath(__file__))
    return send_from_directory(root_dir, 'favicon.ico', mimetype='image/x-icon')


def try_decode_hex(text):
    """Try to decode hex-encoded text"""
    try:
        if not all(c in '0123456789ABCDEFabcdef' for c in text.strip()):
            return text

        hex_str = text.strip()

        try:
            decoded = bytes.fromhex(hex_str).decode('utf-16-be', errors='ignore')
            if decoded and len(decoded) > 0:
                return decoded
        except:
            pass

        try:
            decoded = bytes.fromhex(hex_str).decode('utf-8', errors='ignore')
            if decoded and len(decoded) > 0:
                return decoded
        except:
            pass

        try:
            decoded = bytes.fromhex(hex_str).decode('latin-1', errors='ignore')
            if decoded and len(decoded) > 0:
                return decoded
        except:
            pass

    except Exception as e:
        log_message(f"[DECODE ERROR] {str(e)}")
        pass

    return text

# In-memory message storage
messages = {
    'sent': [],
    'received': []
}

# Messages storage file
messages_file = '/app/data/messages.json'

def load_messages_from_file():
    """Load messages from persistent storage"""
    global messages
    try:
        if os.path.exists(messages_file):
            with open(messages_file, 'r') as f:
                data = json.load(f)
                messages = data
                log_message(f"[STORAGE] Loaded {len(messages['sent'])} sent and {len(messages['received'])} received messages from disk")
        else:
            log_message("[STORAGE] No message file found, starting fresh")
    except Exception as e:
        log_message(f"[STORAGE ERROR] Failed to load messages: {str(e)}")

def save_messages_to_file():
    """Save messages to persistent storage"""
    global messages
    try:
        with open(messages_file, 'w') as f:
            json.dump(messages, f, indent=2)
    except Exception as e:
        log_message(f"[STORAGE ERROR] Failed to save messages: {str(e)}")

def load_forwarding_config():
    """Load forwarding configuration"""
    try:
        if os.path.exists(forwarding_config_file):
            with open(forwarding_config_file, 'r') as f:
                config = json.load(f)
                log_message(f"[FORWARDING] Loaded config: {config}")
                return config
    except Exception as e:
        log_message(f"[FORWARDING ERROR] Failed to load config: {str(e)}")

    return {
        'enabled': False,
        'sender_address': '',
        'sender_name': 'SMS Copilot',
        'subject': 'New SMS Received: {phone}',
        'destination_address': '',
        'smtp_server': '',
        'smtp_port': 587,
        'encryption': 'TLS',
        'encryption_protocol': 'TLSv1.2',
        'smtp_username': '',
        'smtp_password': ''
    }

def save_forwarding_config(config):
    """Save forwarding configuration"""
    try:
        log_message(f"[FORWARDING] Saving config: {config}")
        with open(forwarding_config_file, 'w') as f:
            json.dump(config, f, indent=2)
        log_message("[FORWARDING] Configuration saved successfully")
        return True
    except Exception as e:
        log_message(f"[FORWARDING ERROR] Failed to save config: {str(e)}")
        return False

def load_gatewayapi_config():
    """Load Gatewayapi configuration"""
    try:
        if os.path.exists(gatewayapi_config_file):
            with open(gatewayapi_config_file, 'r') as f:
                config = json.load(f)
                log_message(f"[GATEWAYAPI] Loaded config: {config}")
                return config
    except Exception as e:
        log_message(f"[GATEWAYAPI ERROR] Failed to load config: {str(e)}")

    return {
        'enabled': False,
        'api_token': '',
        'sender_id': '',
        'destination_phone': ''
    }

def save_gatewayapi_config(config):
    """Save Gatewayapi configuration"""
    try:
        log_message(f"[GATEWAYAPI] Saving config: {config}")
        with open(gatewayapi_config_file, 'w') as f:
            json.dump(config, f, indent=2)
        log_message("[GATEWAYAPI] Configuration saved successfully")
        return True
    except Exception as e:
        log_message(f"[GATEWAYAPI ERROR] Failed to save config: {str(e)}")
        return False

def send_forwarding_email_async(phone, message):
    """Send email via SMTP with received SMS (async - non-blocking)"""
    def send_email():
        try:
            config = load_forwarding_config()

            if not config['enabled']:
                log_message("[FORWARDING] Email forwarding is disabled, skipping")
                return False

            log_message(f"[FORWARDING] Starting async email send for SMS from {phone}")

            subject = config['subject'].replace('{phone}', phone).replace('{timestamp}', datetime.now().isoformat())
            body = f"""
New SMS Received

From: {phone}
Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

Message:
{message}

---
Sent by SMS Dashboard Copilot
"""

            msg = MIMEMultipart()
            msg['From'] = f"{config['sender_name']} <{config['sender_address']}>"
            msg['To'] = config['destination_address']
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'plain'))

            smtp_server = config['smtp_server']
            smtp_port = config['smtp_port']
            encryption = config['encryption']
            protocol = config['encryption_protocol']
            username = config['smtp_username']
            password = config['smtp_password']

            log_message(f"[FORWARDING] Connecting to {smtp_server}:{smtp_port} with {encryption}")

            if encryption == 'SSL':
                context = ssl.create_default_context()
                if protocol == 'TLSv1.2':
                    context.minimum_version = ssl.TLSVersion.TLSv1_2
                    context.maximum_version = ssl.TLSVersion.TLSv1_2
                elif protocol == 'TLSv1.3':
                    context.minimum_version = ssl.TLSVersion.TLSv1_3

                server = smtplib.SMTP_SSL(smtp_server, smtp_port, context=context, timeout=10)
            else:
                server = smtplib.SMTP(smtp_server, smtp_port, timeout=10)
                if encryption == 'TLS':
                    context = ssl.create_default_context()
                    if protocol == 'TLSv1.2':
                        context.minimum_version = ssl.TLSVersion.TLSv1_2
                        context.maximum_version = ssl.TLSVersion.TLSv1_2
                    elif protocol == 'TLSv1.3':
                        context.minimum_version = ssl.TLSVersion.TLSv1_3
                    server.starttls(context=context)

            log_message(f"[FORWARDING] Logging in as {username}")
            server.login(username, password)
            server.send_message(msg)
            server.quit()

            log_message(f"[FORWARDING] ✓ Email sent successfully to {config['destination_address']}")
            return True

        except Exception as e:
            log_message(f"[FORWARDING ERROR] Failed to send email: {str(e)}")
            import traceback
            log_message(f"[FORWARDING ERROR] Traceback: {traceback.format_exc()}")
            return False

    email_thread = threading.Thread(target=send_email, daemon=True)
    email_thread.start()
    log_message("[FORWARDING] Email send thread started")

def send_gatewayapi_sms_async(phone, message):
    """Send SMS via Gatewayapi (async - non-blocking)"""
    def send_sms():
        try:
            config = load_gatewayapi_config()

            if not config['enabled']:
                log_message("[GATEWAYAPI] SMS forwarding is disabled, skipping")
                return False

            log_message(f"[GATEWAYAPI] Starting async SMS send for message from {phone}")

            api_token = config['api_token']
            sender_id = config['sender_id']
            destination_phone = config['destination_phone']

            sms_message = f"SMS from {phone}:\n{message}"

            url = "https://gatewayapi.com/rest/mtsms"
            auth = HTTPBasicAuth(api_token, '')

            headers = {'Content-Type': 'application/json'}

            payload = {
                'sender': sender_id,
                'recipients': [{'msisdn': destination_phone}],
                'message': sms_message
            }

            log_message(f"[GATEWAYAPI] Sending SMS to {destination_phone} via Gatewayapi")
            response = requests.post(url, json=payload, headers=headers, auth=auth, timeout=10)

            log_message(f"[GATEWAYAPI] Response status: {response.status_code}")
            log_message(f"[GATEWAYAPI] Response: {response.text}")

            if response.status_code in [200, 201]:
                log_message(f"[GATEWAYAPI] ✓ SMS sent successfully to {destination_phone}")
                return True
            else:
                log_message(f"[GATEWAYAPI] ✗ Failed to send SMS: {response.text}")
                return False

        except Exception as e:
            log_message(f"[GATEWAYAPI ERROR] Failed to send SMS: {str(e)}")
            import traceback
            log_message(f"[GATEWAYAPI ERROR] Traceback: {traceback.format_exc()}")
            return False

    sms_thread = threading.Thread(target=send_sms, daemon=True)
    sms_thread.start()
    log_message("[GATEWAYAPI] SMS send thread started")

# ... keep the rest of your existing file unchanged ...

@app.route('/')
def index():
    return render_template('index.html')

# (rest of routes unchanged)
