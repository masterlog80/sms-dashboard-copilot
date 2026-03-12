from flask import Flask, render_template, request, jsonify
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

app = Flask(__name__, template_folder='.')

# Setup logging
log_file = '/app/data/modem.log'
os.makedirs('/app/data', exist_ok=True)

# Forwarding config file
forwarding_config_file = '/app/data/forwarding_config.json'

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
                return json.load(f)
    except Exception as e:
        log_message(f"[FORWARDING ERROR] Failed to load config: {str(e)}")
    
    # Return default config
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
        with open(forwarding_config_file, 'w') as f:
            json.dump(config, f, indent=2)
        log_message("[FORWARDING] Configuration saved")
        return True
    except Exception as e:
        log_message(f"[FORWARDING ERROR] Failed to save config: {str(e)}")
        return False

def send_forwarding_email_async(phone, message):
    """Send email via SMTP with received SMS (async - non-blocking)"""
    def send_email():
        try:
            config = load_forwarding_config()
            
            if not config['enabled']:
                log_message("[FORWARDING] Email forwarding is disabled, skipping")
                return False
            
            log_message(f"[FORWARDING] Sending email for SMS from {phone}")
            
            # Prepare subject and body
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
            
            # Create message
            msg = MIMEMultipart()
            msg['From'] = f"{config['sender_name']} <{config['sender_address']}>"
            msg['To'] = config['destination_address']
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'plain'))
            
            # Connect and send
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
            
            # Login
            log_message(f"[FORWARDING] Logging in as {username}")
            server.login(username, password)
            
            # Send
            server.send_message(msg)
            server.quit()
            
            log_message(f"[FORWARDING] ✓ Email sent successfully to {config['destination_address']}")
            return True
            
        except Exception as e:
            log_message(f"[FORWARDING ERROR] Failed to send email: {str(e)}")
            return False
    
    # Send email in separate thread to avoid blocking SMS reception
    email_thread = threading.Thread(target=send_email, daemon=True)
    email_thread.start()

# Track SMS parts for concatenation
pending_parts = {}

# Modem lock to prevent simultaneous access
modem_lock = threading.Lock()

# GSM Modem setup
modem = None
modem_port = '/dev/ttyUSB0'
modem_baudrate = 9600
modem_connected = False
receiver_thread = None
stop_receiver = False
processed_messages = set()

# Health check variables
last_successful_command_time = time.time()
modem_health_check_interval = 30
modem_health_timeout = 60
health_check_thread = None
stop_health_check = False

def restart_application():
    """Restart the entire application"""
    global modem, modem_connected
    
    log_message("="*50)
    log_message("⚠️ RESTARTING APPLICATION DUE TO MODEM FAILURE")
    log_message("="*50)
    
    try:
        stop_receiver_thread()
        
        if modem:
            try:
                modem.close()
                log_message("[RESTART] Modem connection closed")
            except:
                pass
        
        modem = None
        modem_connected = False
        
        time.sleep(5)
        
        log_message("[RESTART] Exiting application for container restart...")
        os._exit(1)
    except Exception as e:
        log_message(f"[RESTART ERROR] {str(e)}")
        os._exit(1)

def modem_health_check():
    """Periodically check if modem is still responsive"""
    global last_successful_command_time, modem_connected, stop_health_check
    
    log_message("[HEALTH] Modem health check thread started")
    
    while not stop_health_check:
        try:
            time.sleep(modem_health_check_interval)
            
            if not modem_connected:
                continue
            
            current_time = time.time()
            time_since_last_command = current_time - last_successful_command_time
            
            log_message(f"[HEALTH] Last successful command was {int(time_since_last_command)} seconds ago")
            
            if time_since_last_command > modem_health_timeout:
                log_message(f"[HEALTH] ⚠️ MODEM UNRESPONSIVE! No successful command for {int(time_since_last_command)} seconds")
                log_message(f"[HEALTH] Threshold is {modem_health_timeout} seconds")
                restart_application()
            
            with modem_lock:
                try:
                    if modem and modem_connected:
                        modem.write(b'AT\r\n')
                        time.sleep(0.5)
                        response = modem.read(100)
                        if b'OK' in response:
                            last_successful_command_time = time.time()
                            log_message("[HEALTH] ✓ Modem is responsive")
                        else:
                            log_message("[HEALTH] ⚠️ Modem not responding to AT command")
                            restart_application()
                except Exception as e:
                    log_message(f"[HEALTH] ⚠️ Health check failed: {str(e)}")
                    restart_application()
        except Exception as e:
            log_message(f"[HEALTH ERROR] {str(e)}")
            time.sleep(5)
    
    log_message("[HEALTH] Modem health check thread stopped")

def start_health_check():
    """Start the modem health check thread"""
    global health_check_thread, stop_health_check
    
    stop_health_check = False
    health_check_thread = threading.Thread(target=modem_health_check, daemon=True)
    health_check_thread.start()
    log_message("[HEALTH] Started health check thread")

def stop_health_check_thread():
    """Stop the modem health check thread"""
    global stop_health_check
    stop_health_check = True
    log_message("[HEALTH] Stopping health check thread...")

def init_modem():
    """Initialize GSM modem connection"""
    global modem, modem_connected, last_successful_command_time
    
    log_message(f"[MODEM] Attempting to initialize on {modem_port}...")
    
    if not os.path.exists(modem_port):
        log_message(f"[MODEM ERROR] Device {modem_port} does not exist")
        modem_connected = False
        return False
    
    try:
        log_message(f"[MODEM] Port exists, opening {modem_port}...")
        modem = serial.Serial(modem_port, modem_baudrate, timeout=2)
        log_message(f"[MODEM] Port opened successfully")
        time.sleep(2)
        
        modem.flushInput()
        modem.flushOutput()
        
        log_message("[MODEM] Sending AT command...")
        modem.write(b'AT\r\n')
        time.sleep(0.5)
        response = modem.read(100)
        log_message(f"[MODEM] Response: {response}")
        
        if b'OK' in response:
            log_message("[MODEM] ✓ Modem detected!")
            last_successful_command_time = time.time()
            
            log_message("[MODEM] Setting text mode...")
            modem.write(b'AT+CMGF=1\r\n')
            time.sleep(0.5)
            response = modem.read(100)
            log_message(f"[MODEM] Text mode response: {response}")
            last_successful_command_time = time.time()
            
            log_message("[MODEM] Setting SMS storage to SIM card...")
            modem.write(b'AT+CPMS="SM","SM","SR"\r\n')
            time.sleep(0.5)
            response = modem.read(200)
            log_message(f"[MODEM] Storage response: {response}")
            last_successful_command_time = time.time()
            
            modem_connected = True
            log_message("[MODEM] ✓ Modem fully initialized")
            return True
        else:
            log_message("[MODEM] ✗ No OK response from modem")
            modem.close()
            modem = None
            modem_connected = False
            return False
    except serial.SerialException as e:
        log_message(f"[MODEM ERROR] Serial exception: {str(e)}")
        modem_connected = False
        return False
    except Exception as e:
        log_message(f"[MODEM ERROR] {str(e)}")
        modem_connected = False
        return False

def diagnose_modem():
    """Diagnose modem and network status"""
    global modem, last_successful_command_time
    
    with modem_lock:
        try:
            if modem is None or not modem_connected:
                return "Modem not connected"
            
            log_message("[DIAG] Starting modem diagnosis...")
            
            modem.write(b'AT+CSQ\r\n')
            time.sleep(0.5)
            response = modem.read(100)
            log_message(f"[DIAG] Signal strength: {response}")
            last_successful_command_time = time.time()
            
            modem.write(b'AT+CREG?\r\n')
            time.sleep(0.5)
            response = modem.read(100)
            log_message(f"[DIAG] Network registration: {response}")
            last_successful_command_time = time.time()
            
            modem.write(b'AT+COPS?\r\n')
            time.sleep(0.5)
            response = modem.read(100)
            log_message(f"[DIAG] Operator: {response}")
            last_successful_command_time = time.time()
            
            modem.write(b'AT+CSCA?\r\n')
            time.sleep(0.5)
            response = modem.read(100)
            log_message(f"[DIAG] SMS Service Center: {response}")
            last_successful_command_time = time.time()
            
            modem.write(b'AT+CNUM\r\n')
            time.sleep(0.5)
            response = modem.read(100)
            log_message(f"[DIAG] Phone number: {response}")
            last_successful_command_time = time.time()
            
            return "Diagnosis complete - check logs"
        except Exception as e:
            log_message(f"[DIAG ERROR] {str(e)}")
            return f"Diagnosis error: {str(e)}"

def get_signal_strength():
    """Get current signal strength from modem"""
    global modem, last_successful_command_time
    
    with modem_lock:
        try:
            if modem is None or not modem_connected:
                return None, None, "Modem not connected"
            
            modem.write(b'AT+CSQ\r\n')
            time.sleep(0.5)
            response = modem.read(100)
            response_str = response.decode('utf-8', errors='ignore')
            last_successful_command_time = time.time()
            
            log_message(f"[SIGNAL] Response: {response_str}")
            
            if '+CSQ:' in response_str:
                match = re.search(r'\+CSQ:\s*(\d+),(\d+)', response_str)
                if match:
                    rssi_val = int(match.group(1))
                    ber = int(match.group(2))
                    
                    if rssi_val == 99:
                        rssi_dbm = "Unknown"
                        quality = 0
                    else:
                        rssi_dbm = -113 + (2 * rssi_val)
                        quality = rssi_val
                    
                    log_message(f"[SIGNAL] RSSI: {rssi_dbm} dBm, Quality: {quality}, BER: {ber}")
                    return rssi_dbm, quality, "OK"
        except Exception as e:
            log_message(f"[SIGNAL ERROR] {str(e)}")
        
        return None, None, "Unable to read signal strength"

def get_sms_service_center():
    """Get current SMS Service Center"""
    global modem, last_successful_command_time
    
    with modem_lock:
        try:
            if modem is None or not modem_connected:
                return None, "Modem not connected"
            
            modem.write(b'AT+CSCA?\r\n')
            time.sleep(0.5)
            response = modem.read(200)
            response_str = response.decode('utf-8', errors='ignore')
            last_successful_command_time = time.time()
            
            log_message(f"[CSCA] Response: {response_str}")
            
            if '+CSCA:' in response_str:
                match = re.search(r'\+CSCA:\s*"([^"]+)"', response_str)
                if match:
                    sca_number = match.group(1)
                    log_message(f"[CSCA] Current SCA: {sca_number}")
                    return sca_number, "OK"
        except Exception as e:
            log_message(f"[CSCA ERROR] {str(e)}")
        
        return None, "Unable to read SCA"

def set_sms_service_center(sca_number):
    """Set SMS Service Center"""
    global modem, last_successful_command_time
    
    with modem_lock:
        try:
            if modem is None or not modem_connected:
                return False, "Modem not connected"
            
            log_message(f"[SCA] Setting SMS Service Center to {sca_number}...")
            cmd = f'AT+CSCA="{sca_number}",145\r\n'
            modem.write(cmd.encode())
            time.sleep(1)
            response = modem.read(100)
            log_message(f"[SCA] Response: {response}")
            last_successful_command_time = time.time()
            
            if b'OK' in response:
                log_message(f"[SCA] ✓ SMS Service Center set successfully")
                return True, "SMS Service Center updated"
            else:
                log_message(f"[SCA] ✗ Failed to set SMS Service Center")
                return False, "Failed to set SMS Service Center"
        except Exception as e:
            log_message(f"[SCA ERROR] {str(e)}")
            return False, str(e)

def get_sim_card_usage():
    """Get SIM card storage usage from modem"""
    global modem, last_successful_command_time
    
    with modem_lock:
        try:
            if modem is None or not modem_connected:
                return {'used': 0, 'total': 20}
            
            modem.write(b'AT+CPMS?\r\n')
            time.sleep(0.5)
            response = modem.read(200)
            response_str = response.decode('utf-8', errors='ignore')
            last_successful_command_time = time.time()
            
            if '+CPMS:' in response_str:
                numbers = re.findall(r'(\d+)', response_str)
                if len(numbers) >= 2:
                    used = int(numbers[0])
                    total = int(numbers[1])
                    return {'used': used, 'total': total}
        except:
            pass
        
        return {'used': 0, 'total': 20}

def read_sms_from_sim():
    """Read SMS from SIM card storage"""
    global modem, processed_messages, pending_parts, last_successful_command_time
    
    with modem_lock:
        try:
            if modem is None or not modem_connected:
                return
            
            log_message("[RECEIVER] Querying unread SMS from SIM card...")
            modem.write(b'AT+CMGL="REC UNREAD"\r\n')
            time.sleep(1)
            
            response = modem.read(2000)
            response_str = response.decode('utf-8', errors='ignore')
            last_successful_command_time = time.time()
            
            log_message(f"[RECEIVER] Raw response length: {len(response_str)}")
            
            if '+CMGL:' in response_str:
                log_message(f"[RECEIVER] Found SMS in response")
                
                lines = response_str.split('\r\n')
                i = 0
                
                while i < len(lines):
                    line = lines[i]
                    
                    if '+CMGL:' in line:
                        try:
                            log_message(f"[RECEIVER] Parsing line: {line}")
                            
                            parts = line.split(',')
                            
                            if len(parts) >= 5:
                                msg_id = parts[0].split(':')[1].strip()
                                stat = parts[1].strip()
                                phone = parts[2].strip().strip('"')
                                timestamp_part = parts[4].strip().strip('"')
                                
                                if msg_id in processed_messages:
                                    log_message(f"[RECEIVER] Message {msg_id} already processed, skipping")
                                    i += 1
                                    continue
                                
                                if i + 1 < len(lines):
                                    message_text = lines[i + 1].strip()
                                    
                                    if message_text:
                                        decoded_text = try_decode_hex(message_text)
                                        
                                        if decoded_text:
                                            msg_key = (phone, timestamp_part)
                                            
                                            if msg_key not in pending_parts:
                                                pending_parts[msg_key] = {
                                                    'parts': [],
                                                    'modem_ids': [],
                                                    'first_seen': time.time()
                                                }
                                            
                                            pending_parts[msg_key]['parts'].append({
                                                'id': msg_id,
                                                'text': decoded_text,
                                            })
                                            pending_parts[msg_key]['modem_ids'].append(msg_id)
                                            
                                            processed_messages.add(msg_id)
                                            log_message(f"[RECEIVER] Part {len(pending_parts[msg_key]['parts'])} from {phone}: {decoded_text[:30]}...")
                        except Exception as e:
                            log_message(f"[RECEIVER] Error parsing line '{line}': {str(e)}")
                    
                    i += 1
            else:
                log_message(f"[RECEIVER] No +CMGL: in response (no messages or error)")
            
            current_time = time.time()
            keys_to_remove = []
            
            for msg_key, msg_data in list(pending_parts.items()):
                parts_list = msg_data['parts']
                modem_ids = msg_data['modem_ids']
                first_seen = msg_data['first_seen']
                phone, timestamp_part = msg_key
                
                if (current_time - first_seen > 6) or (len(response_str) < 100):
                    combined_text = ''.join([p['text'] for p in parts_list])
                    
                    already_stored = any(
                        m['phone'] == phone and m['message'] == combined_text 
                        for m in messages['received']
                    )
                    
                    if not already_stored and combined_text:
                        message = {
                            'type': 'received',
                            'phone': phone,
                            'message': combined_text,
                            'timestamp': datetime.now().isoformat(),
                            'status': 'received'
                        }
                        
                        messages['received'].append(message)
                        save_messages_to_file()
                        
                        log_message(f"[RECEIVER] ✓ COMBINED SMS from {phone} ({len(parts_list)} parts): {combined_text[:50]}")
                        log_message(f"[RECEIVER] Message stored locally and will be deleted from SIM card")
                        
                        delete_sms_from_modem(modem_ids)
                        keys_to_remove.append(msg_key)
            
            for key in keys_to_remove:
                del pending_parts[key]
                
        except Exception as e:
            log_message(f"[RECEIVER ERROR] {str(e)}")

def receive_sms_loop():
    """Continuously poll for incoming SMS from SIM card"""
    global modem, stop_receiver
    
    log_message("[RECEIVER] SMS receiver thread started")
    
    while not stop_receiver and modem_connected:
        try:
            read_sms_from_sim()
            time.sleep(3)
        except Exception as e:
            log_message(f"[RECEIVER ERROR] {str(e)}")
            time.sleep(3)
    
    log_message("[RECEIVER] SMS receiver thread stopped")

def start_receiver():
    """Start the SMS receiver thread"""
    global receiver_thread, stop_receiver
    
    stop_receiver = False
    receiver_thread = threading.Thread(target=receive_sms_loop, daemon=True)
    receiver_thread.start()
    log_message("[RECEIVER] Started receiver thread")

def stop_receiver_thread():
    """Stop the SMS receiver thread"""
    global stop_receiver
    stop_receiver = True
    log_message("[RECEIVER] Stopping receiver thread...")

def send_sms(phone, message_text):
    """Send SMS via GSM modem"""
    global modem, last_successful_command_time
    
    with modem_lock:
        log_message(f"[SMS] Attempting to send to {phone}")
        
        try:
            if modem is None or not modem_connected:
                log_message(f"[SMS ERROR] Modem not connected")
                return False, "Modem not connected. Check /dev/ttyUSB0"
            
            modem.flushInput()
            modem.flushOutput()
            time.sleep(0.5)
            
            log_message(f"[SMS] Sending CMGS command...")
            cmd = f'AT+CMGS="{phone}"\r\n'
            modem.write(cmd.encode())
            time.sleep(1.5)
            
            response = modem.read(100)
            log_message(f"[SMS] CMGS response: {response}")
            last_successful_command_time = time.time()
            
            if b'>' in response:
                log_message(f"[SMS] Modem ready, sending message...")
                modem.write(message_text.encode())
                time.sleep(0.5)
                modem.write(b'\x1A')
                time.sleep(3)
                
                response = modem.read(500)
                log_message(f"[SMS] Send response: {response}")
                response_str = response.decode('utf-8', errors='ignore')
                last_successful_command_time = time.time()
                
                if b'+CMGS:' in response or b'OK' in response:
                    log_message(f"[SMS] ✓ Message sent successfully")
                    return True, "SMS sent successfully"
                elif b'ERROR' in response or b'CMS ERROR' in response:
                    error_match = re.search(r'ERROR:\s*(\d+)', response_str)
                    error_code = error_match.group(1) if error_match else "Unknown"
                    log_message(f"[SMS] ✗ CMS Error {error_code}")
                    
                    log_message(f"[SMS] Retrying with basic encoding...")
                    modem.flushInput()
                    modem.flushOutput()
                    time.sleep(0.5)
                    
                    modem.write(cmd.encode())
                    time.sleep(1.5)
                    response = modem.read(100)
                    
                    if b'>' in response:
                        modem.write(message_text.encode('utf-8', errors='ignore'))
                        time.sleep(0.5)
                        modem.write(b'\x1A')
                        time.sleep(3)
                        response = modem.read(500)
                        last_successful_command_time = time.time()
                        
                        if b'+CMGS:' in response or b'OK' in response:
                            log_message(f"[SMS] ✓ Message sent successfully on retry")
                            return True, "SMS sent successfully"
                    
                    return False, f"SMS sending failed with error {error_code}"
                else:
                    log_message(f"[SMS] ✗ Unexpected response: {response_str}")
                    return False, "Unexpected modem response"
            else:
                log_message(f"[SMS] ✗ Modem not ready for message input")
                return False, "Modem did not accept message command"
                
        except Exception as e:
            log_message(f"[SMS ERROR] {str(e)}")
            return False, f"Error sending SMS: {str(e)}"

def delete_sms_from_modem(modem_ids):
    """Delete SMS from modem by ID(s)"""
    global modem, last_successful_command_time
    
    with modem_lock:
        try:
            if modem is None or not modem_connected:
                return False, "Modem not connected"
            
            if isinstance(modem_ids, list):
                for modem_id in modem_ids:
                    log_message(f"[DELETE] Deleting SMS with ID {modem_id} from modem...")
                    cmd = f'AT+CMGD={modem_id}\r\n'
                    modem.write(cmd.encode())
                    time.sleep(0.5)
                    response = modem.read(100)
                    log_message(f"[DELETE] Response: {response}")
                    last_successful_command_time = time.time()
            else:
                modem_id = modem_ids
                log_message(f"[DELETE] Deleting SMS with ID {modem_id} from modem...")
                cmd = f'AT+CMGD={modem_id}\r\n'
                modem.write(cmd.encode())
                time.sleep(0.5)
                response = modem.read(100)
                log_message(f"[DELETE] Response: {response}")
                last_successful_command_time = time.time()
            
            log_message(f"[DELETE] ✓ SMS deleted from modem")
            return True, "SMS deleted"
        except Exception as e:
            log_message(f"[DELETE ERROR] {str(e)}")
            return False, str(e)

def clear_sim_storage():
    """Delete all SMS from modem SIM card storage"""
    global modem, last_successful_command_time
    
    with modem_lock:
        try:
            if modem is None or not modem_connected:
                return False, "Modem not connected"
            
            log_message(f"[CLEAR] Deleting all SMS from SIM card...")
            modem.write(b'AT+CMGD=1,4\r\n')
            time.sleep(2)
            response = modem.read(200)
            log_message(f"[CLEAR] Response: {response}")
            last_successful_command_time = time.time()
            
            if b'OK' in response or len(response) > 0:
                log_message(f"[CLEAR] ✓ All SMS deleted from SIM card")
                return True, "SIM storage cleared"
            else:
                log_message(f"[CLEAR] Checking storage status...")
                modem.write(b'AT+CPMS?\r\n')
                time.sleep(1)
                status_response = modem.read(200)
                log_message(f"[CLEAR] Storage status: {status_response}")
                last_successful_command_time = time.time()
                
                if b'CPMS' in status_response or b'OK' in status_response:
                    log_message(f"[CLEAR] ✓ All SMS deleted from SIM card")
                    return True, "SIM storage cleared"
                else:
                    log_message(f"[CLEAR] ✗ Failed to clear SIM storage")
                    return False, "Failed to clear SIM storage"
        except Exception as e:
            log_message(f"[CLEAR ERROR] {str(e)}")
            return False, str(e)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/stats', methods=['GET'])
def get_stats():
    sim_usage = get_sim_card_usage()
    
    return jsonify({
        'total_messages': len(messages['sent']) + len(messages['received']),
        'unread_messages': len(messages['received']),
        'sent_messages': len(messages['sent']),
        'received_messages': len(messages['received']),
        'modem_connected': modem_connected,
        'sim_usage': sim_usage
    })

@app.route('/api/messages', methods=['GET'])
def get_messages():
    all_messages = messages['sent'] + messages['received']
    all_messages.sort(key=lambda x: x['timestamp'], reverse=True)
    return jsonify(all_messages[:50])

@app.route('/api/message', methods=['POST'])
def send_message():
    try:
        data = request.json
        phone = data.get('phone')
        message_text = data.get('message')
        
        log_message(f"[API] Received SMS request: {phone} - {message_text[:30]}...")
        
        success, status = send_sms(phone, message_text)
        
        message = {
            'type': 'sent',
            'phone': phone,
            'message': message_text,
            'timestamp': datetime.now().isoformat(),
            'status': 'sent' if success else 'failed'
        }
        
        messages['sent'].append(message)
        save_messages_to_file()
        
        if success:
            log_message(f"[API] ✓ SMS stored and sent")
            return jsonify({
                'status': 'success',
                'message': 'SMS sent successfully',
                'data': message
            }), 200
        else:
            log_message(f"[API] ✗ SMS stored but failed to send: {status}")
            return jsonify({
                'status': 'error',
                'message': status,
                'data': message
            }), 500
    except Exception as e:
        log_message(f"[API ERROR] {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/api/message/<int:message_index>', methods=['DELETE'])
def delete_message(message_index):
    """Delete a message from the list"""
    try:
        log_message(f"[API] Delete request for message index {message_index}")
        
        all_messages = messages['sent'] + messages['received']
        all_messages.sort(key=lambda x: x['timestamp'], reverse=True)
        
        if message_index >= len(all_messages):
            return jsonify({'status': 'error', 'message': 'Message not found'}), 404
        
        message = all_messages[message_index]
        
        if message['type'] == 'received':
            for idx, msg in enumerate(messages['received']):
                if msg == message:
                    messages['received'].pop(idx)
                    break
        else:
            for idx, msg in enumerate(messages['sent']):
                if msg == message:
                    messages['sent'].pop(idx)
                    break
        
        save_messages_to_file()
        
        log_message(f"[API] ✓ Message deleted")
        return jsonify({
            'status': 'success',
            'message': 'Message deleted successfully'
        }), 200
        
    except Exception as e:
        log_message(f"[API ERROR] Delete failed: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/api/clear-sim-storage', methods=['POST'])
def clear_sim_storage_api():
    """Clear all SMS from SIM card storage and dashboard"""
    try:
        log_message(f"[API] Clear SIM storage request")
        
        success, status = clear_sim_storage()
        
        if success:
            messages['sent'].clear()
            messages['received'].clear()
            pending_parts.clear()
            processed_messages.clear()
            save_messages_to_file()
            
            log_message(f"[API] ✓ SIM storage and dashboard cleared")
            return jsonify({
                'status': 'success',
                'message': 'SIM storage and dashboard cleared successfully'
            }), 200
        else:
            log_message(f"[API] ✗ Failed to clear SIM storage: {status}")
            return jsonify({
                'status': 'error',
                'message': status
            }), 500
    except Exception as e:
        log_message(f"[API ERROR] Clear storage failed: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/api/modem/diagnose', methods=['GET'])
def diagnose():
    """Run modem diagnostics"""
    result = diagnose_modem()
    return jsonify({'status': 'ok', 'message': result})

@app.route('/api/modem/signal-strength', methods=['GET'])
def signal_strength():
    """Get current signal strength"""
    rssi, quality, status = get_signal_strength()
    
    if rssi is not None:
        return jsonify({
            'status': 'success',
            'rssi': rssi,
            'quality': quality
        }), 200
    else:
        return jsonify({
            'status': 'error',
            'message': status
        }), 500

@app.route('/api/modem/get-sca', methods=['GET'])
def get_sca():
    """Get current SMS Service Center"""
    sca_number, status = get_sms_service_center()
    
    if sca_number:
        return jsonify({
            'status': 'success',
            'sca_number': sca_number
        }), 200
    else:
        return jsonify({
            'status': 'error',
            'message': status
        }), 500

@app.route('/api/modem/set-sca', methods=['POST'])
def set_sca():
    """Set SMS Service Center"""
    try:
        data = request.json
        sca_number = data.get('sca_number')
        
        if not sca_number:
            return jsonify({'status': 'error', 'message': 'SCA number required'}), 400
        
        log_message(f"[API] Set SCA request: {sca_number}")
        success, status = set_sms_service_center(sca_number)
        
        if success:
            return jsonify({
                'status': 'success',
                'message': status
            }), 200
        else:
            return jsonify({
                'status': 'error',
                'message': status
            }), 500
    except Exception as e:
        log_message(f"[API ERROR] Set SCA failed: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/api/modem/status', methods=['GET'])
def modem_status():
    """Check modem connection status"""
    return jsonify({
        'connected': modem_connected,
        'port': modem_port,
        'baudrate': modem_baudrate,
        'device_exists': os.path.exists(modem_port)
    })

@app.route('/api/logs', methods=['GET'])
def get_logs():
    """Get modem logs"""
    try:
        with open(log_file, 'r') as f:
            logs = f.readlines()
        return jsonify({'logs': logs[-100:]})
    except:
        return jsonify({'logs': ['No logs available']})

@app.route('/api/forwarding/config', methods=['GET'])
def get_forwarding_config():
    """Get forwarding configuration"""
    try:
        config = load_forwarding_config()
        return jsonify({
            'status': 'success',
            'config': config
        }), 200
    except Exception as e:
        log_message(f"[API ERROR] Failed to get config: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/api/forwarding/save', methods=['POST'])
def save_forwarding_config_api():
    """Save forwarding configuration"""
    try:
        data = request.json
        
        if save_forwarding_config(data):
            return jsonify({
                'status': 'success',
                'message': 'Configuration saved successfully'
            }), 200
        else:
            return jsonify({
                'status': 'error',
                'message': 'Failed to save configuration'
            }), 500
    except Exception as e:
        log_message(f"[API ERROR] Failed to save config: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/api/forwarding/test', methods=['POST'])
def test_forwarding_config():
    """Test forwarding configuration"""
    try:
        data = request.json
        
        # Create a temporary config for testing
        test_config = {
            'enabled': True,
            'sender_address': data.get('sender_address'),
            'sender_name': data.get('sender_name', 'SMS Copilot'),
            'subject': data.get('subject', 'Test Email from SMS Copilot'),
            'destination_address': data.get('destination_address'),
            'smtp_server': data.get('smtp_server'),
            'smtp_port': data.get('smtp_port', 587),
            'encryption': data.get('encryption'),
            'encryption_protocol': data.get('encryption_protocol'),
            'smtp_username': data.get('smtp_username'),
            'smtp_password': data.get('smtp_password')
        }
        
        log_message("[FORWARDING] Testing email configuration...")
        
        # Create message
        msg = MIMEMultipart()
        msg['From'] = f"{test_config['sender_name']} <{test_config['sender_address']}>"
        msg['To'] = test_config['destination_address']
        msg['Subject'] = test_config['subject']
        
        body = "This is a test email from SMS Dashboard Copilot.\n\nIf you received this, your SMTP configuration is working correctly!"
        msg.attach(MIMEText(body, 'plain'))
        
        # Connect and send
        smtp_server = test_config['smtp_server']
        smtp_port = test_config['smtp_port']
        encryption = test_config['encryption']
        protocol = test_config['encryption_protocol']
        username = test_config['smtp_username']
        password = test_config['smtp_password']
        
        log_message(f"[FORWARDING] Test: Connecting to {smtp_server}:{smtp_port} with {encryption}")
        
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
        
        log_message(f"[FORWARDING] Test: Logging in as {username}")
        server.login(username, password)
        
        server.send_message(msg)
        server.quit()
        
        log_message(f"[FORWARDING] ✓ Test email sent successfully to {test_config['destination_address']}")
        
        return jsonify({
            'status': 'success',
            'message': f'Test email sent successfully to {test_config["destination_address"]}'
        }), 200
        
    except smtplib.SMTPAuthenticationError as e:
        log_message(f"[FORWARDING ERROR] Authentication failed: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': f'Authentication failed: Check your username and password'
        }), 500
    except smtplib.SMTPException as e:
        log_message(f"[FORWARDING ERROR] SMTP error: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': f'SMTP error: {str(e)}'
        }), 500
    except Exception as e:
        log_message(f"[FORWARDING ERROR] Test failed: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': f'Test failed: {str(e)}'
        }), 500

if __name__ == '__main__':
    log_message("="*50)
    log_message("SMS DASHBOARD STARTING...")
    log_message("="*50)
    
    load_messages_from_file()
    
    init_modem()
    
    log_message("="*50)
    log_message(f"Modem Status: {'CONNECTED' if modem_connected else 'NOT CONNECTED'}")
    log_message("="*50)
    
    if modem_connected:
        start_receiver()
        start_health_check()
    
    try:
        app.run(debug=False, host='0.0.0.0', port=5000)
    finally:
        stop_receiver_thread()
        stop_health_check_thread()
        if modem:
            log_message("[MODEM] Closing connection...")
            modem.close()
