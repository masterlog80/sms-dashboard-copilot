from flask import Flask, render_template, request, jsonify
import os
import json
import serial
import time
from datetime import datetime
import sys
import threading
import re

app = Flask(__name__, template_folder='.')

# Setup logging
log_file = '/app/data/modem.log'
os.makedirs('/app/data', exist_ok=True)

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
        # Check if it looks like hex (all hex characters)
        if not all(c in '0123456789ABCDEFabcdef' for c in text.strip()):
            return text  # Not hex encoded, return as-is
        
        hex_str = text.strip()
        
        # Try UTF-16BE decoding first (UCS2 - common for Unicode SMS)
        try:
            decoded = bytes.fromhex(hex_str).decode('utf-16-be', errors='ignore')
            if decoded and len(decoded) > 0:
                return decoded
        except:
            pass
        
        # Try UTF-8 decoding
        try:
            decoded = bytes.fromhex(hex_str).decode('utf-8', errors='ignore')
            if decoded and len(decoded) > 0:
                return decoded
        except:
            pass
        
        # Try Latin-1 (ISO-8859-1) decoding
        try:
            decoded = bytes.fromhex(hex_str).decode('latin-1', errors='ignore')
            if decoded and len(decoded) > 0:
                return decoded
        except:
            pass
        
    except Exception as e:
        log_message(f"[DECODE ERROR] {str(e)}")
        pass
    
    return text  # Return original if decoding fails

# In-memory message storage
messages = {
    'sent': [],
    'received': []
}

# Track modem SMS indices for deletion
sms_modem_indices = {}  # Map of message index to modem message ID

# Track SMS parts for concatenation - DON'T store yet
pending_parts = {}  # Map of (phone, timestamp) to list of parts with timestamps

# Modem lock to prevent simultaneous access
modem_lock = threading.Lock()

# GSM Modem setup
modem = None
modem_port = '/dev/ttyUSB0'
modem_baudrate = 9600
modem_connected = False
receiver_thread = None
stop_receiver = False
processed_messages = set()  # Track processed message IDs

def init_modem():
    """Initialize GSM modem connection"""
    global modem, modem_connected
    
    log_message(f"[MODEM] Attempting to initialize on {modem_port}...")
    
    # Check if device exists
    if not os.path.exists(modem_port):
        log_message(f"[MODEM ERROR] Device {modem_port} does not exist")
        modem_connected = False
        return False
    
    try:
        log_message(f"[MODEM] Port exists, opening {modem_port}...")
        modem = serial.Serial(modem_port, modem_baudrate, timeout=2)
        log_message(f"[MODEM] Port opened successfully")
        time.sleep(2)
        
        # Clear buffer
        modem.flushInput()
        modem.flushOutput()
        
        # Test modem with AT command
        log_message("[MODEM] Sending AT command...")
        modem.write(b'AT\r\n')
        time.sleep(0.5)
        response = modem.read(100)
        log_message(f"[MODEM] Response: {response}")
        
        if b'OK' in response:
            log_message("[MODEM] ✓ Modem detected!")
            
            # Set text mode
            log_message("[MODEM] Setting text mode...")
            modem.write(b'AT+CMGF=1\r\n')
            time.sleep(0.5)
            response = modem.read(100)
            log_message(f"[MODEM] Text mode response: {response}")
            
            # Set SMS storage to SIM card (SM)
            log_message("[MODEM] Setting SMS storage to SIM card...")
            modem.write(b'AT+CPMS="SM","SM","SR"\r\n')
            time.sleep(0.5)
            response = modem.read(200)
            log_message(f"[MODEM] Storage response: {response}")
            
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
    global modem
    
    with modem_lock:
        try:
            if modem is None or not modem_connected:
                return "Modem not connected"
            
            log_message("[DIAG] Starting modem diagnosis...")
            
            # Check signal strength
            modem.write(b'AT+CSQ\r\n')
            time.sleep(0.5)
            response = modem.read(100)
            log_message(f"[DIAG] Signal strength: {response}")
            
            # Check network registration
            modem.write(b'AT+CREG?\r\n')
            time.sleep(0.5)
            response = modem.read(100)
            log_message(f"[DIAG] Network registration: {response}")
            
            # Check operator
            modem.write(b'AT+COPS?\r\n')
            time.sleep(0.5)
            response = modem.read(100)
            log_message(f"[DIAG] Operator: {response}")
            
            # Check SMS service center
            modem.write(b'AT+CSCA?\r\n')
            time.sleep(0.5)
            response = modem.read(100)
            log_message(f"[DIAG] SMS Service Center: {response}")
            
            # Check phone number
            modem.write(b'AT+CNUM\r\n')
            time.sleep(0.5)
            response = modem.read(100)
            log_message(f"[DIAG] Phone number: {response}")
            
            return "Diagnosis complete - check logs"
        except Exception as e:
            log_message(f"[DIAG ERROR] {str(e)}")
            return f"Diagnosis error: {str(e)}"

def get_sms_service_center():
    """Get current SMS Service Center"""
    global modem
    
    with modem_lock:
        try:
            if modem is None or not modem_connected:
                return None, "Modem not connected"
            
            modem.write(b'AT+CSCA?\r\n')
            time.sleep(0.5)
            response = modem.read(200)
            response_str = response.decode('utf-8', errors='ignore')
            
            log_message(f"[CSCA] Response: {response_str}")
            
            # Parse: +CSCA: "+393935000001",145
            if '+CSCA:' in response_str:
                # Extract the phone number between quotes
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
    global modem
    
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
    global modem
    
    with modem_lock:
        try:
            if modem is None or not modem_connected:
                return {'used': 0, 'total': 20}
            
            modem.write(b'AT+CPMS?\r\n')
            time.sleep(0.5)
            response = modem.read(200)
            response_str = response.decode('utf-8', errors='ignore')
            
            # Parse: +CPMS: "SM",0,20,"SM",0,20,"SR",0,20
            if '+CPMS:' in response_str:
                # Extract numbers from response
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
    global modem, processed_messages, pending_parts
    
    with modem_lock:
        try:
            if modem is None or not modem_connected:
                return
            
            # List unread SMS from SIM card
            log_message("[RECEIVER] Querying unread SMS from SIM card...")
            modem.write(b'AT+CMGL="REC UNREAD"\r\n')
            time.sleep(1)
            
            response = modem.read(2000)
            response_str = response.decode('utf-8', errors='ignore')
            
            log_message(f"[RECEIVER] Raw response length: {len(response_str)}")
            
            if '+CMGL:' in response_str:
                log_message(f"[RECEIVER] Found SMS in response")
                
                # Parse messages
                lines = response_str.split('\r\n')
                i = 0
                
                while i < len(lines):
                    line = lines[i]
                    
                    if '+CMGL:' in line:
                        try:
                            # Format: +CMGL: <id>,<stat>,"<oa/da>","<alpha>",<scts>[,<tooa>,<length>]
                            log_message(f"[RECEIVER] Parsing line: {line}")
                            
                            # Extract message ID, phone number, and timestamp
                            parts = line.split(',')
                            
                            if len(parts) >= 5:
                                msg_id = parts[0].split(':')[1].strip()
                                stat = parts[1].strip()
                                phone = parts[2].strip().strip('"')
                                # Reconstruct timestamp
                                timestamp_part = parts[4].strip().strip('"')
                                
                                # Check if this specific message ID was already processed
                                if msg_id in processed_messages:
                                    log_message(f"[RECEIVER] Message {msg_id} already processed, skipping")
                                    i += 1
                                    continue
                                
                                # Message text is on next line
                                if i + 1 < len(lines):
                                    message_text = lines[i + 1].strip()
                                    
                                    if message_text:  # Only decode if there's text
                                        # Try to decode if it's hex
                                        decoded_text = try_decode_hex(message_text)
                                        
                                        if decoded_text:
                                            # Create a key for grouping multi-part messages
                                            msg_key = (phone, timestamp_part)
                                            
                                            # Store this part
                                            if msg_key not in pending_parts:
                                                pending_parts[msg_key] = {
                                                    'parts': [],
                                                    'first_seen': time.time()
                                                }
                                            
                                            pending_parts[msg_key]['parts'].append({
                                                'id': msg_id,
                                                'text': decoded_text,
                                            })
                                            
                                            processed_messages.add(msg_id)
                                            log_message(f"[RECEIVER] Part {len(pending_parts[msg_key]['parts'])} from {phone}: {decoded_text[:30]}...")
                        except Exception as e:
                            log_message(f"[RECEIVER] Error parsing line '{line}': {str(e)}")
                    
                    i += 1
            else:
                log_message(f"[RECEIVER] No +CMGL: in response (no messages or error)")
            
            # Now check pending parts for completion
            current_time = time.time()
            keys_to_remove = []
            
            for msg_key, msg_data in list(pending_parts.items()):
                parts_list = msg_data['parts']
                first_seen = msg_data['first_seen']
                phone, timestamp_part = msg_key
                
                # Wait at least 6 seconds for all parts to arrive, OR if no new messages were found
                if (current_time - first_seen > 6) or (len(response_str) < 100):
                    # Combine and store this message
                    combined_text = ''.join([p['text'] for p in parts_list])
                    
                    # Check if we already have this combined message
                    already_stored = any(
                        m['phone'] == phone and m['message'] == combined_text 
                        for m in messages['received']
                    )
                    
                    if not already_stored and combined_text:
                        # Store combined message
                        message = {
                            'type': 'received',
                            'phone': phone,
                            'message': combined_text,
                            'timestamp': datetime.now().isoformat(),
                            'status': 'received'
                        }
                        
                        msg_index = len(messages['received'])
                        messages['received'].append(message)
                        
                        # Store all part IDs for deletion
                        part_ids = [p['id'] for p in parts_list]
                        sms_modem_indices[msg_index] = part_ids
                        
                        log_message(f"[RECEIVER] ✓ COMBINED SMS from {phone} ({len(parts_list)} parts): {combined_text[:50]}")
                        
                        keys_to_remove.append(msg_key)
            
            # Remove stored messages from pending
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
            time.sleep(3)  # Check every 3 seconds
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
    global modem
    
    with modem_lock:
        log_message(f"[SMS] Attempting to send to {phone}")
        
        try:
            if modem is None or not modem_connected:
                log_message(f"[SMS ERROR] Modem not connected")
                return False, "Modem not connected. Check /dev/ttyUSB0"
            
            # Clear buffer before sending
            modem.flushInput()
            modem.flushOutput()
            time.sleep(0.5)
            
            # Send SMS command
            log_message(f"[SMS] Sending CMGS command...")
            cmd = f'AT+CMGS="{phone}"\r\n'
            modem.write(cmd.encode())
            time.sleep(1.5)
            
            response = modem.read(100)
            log_message(f"[SMS] CMGS response: {response}")
            
            if b'>' in response:
                log_message(f"[SMS] Modem ready, sending message...")
                # Send message text
                modem.write(message_text.encode())
                time.sleep(0.5)
                # Send Ctrl+Z to complete
                modem.write(b'\x1A')
                time.sleep(3)  # Wait longer for response
                
                # Read response
                response = modem.read(500)
                log_message(f"[SMS] Send response: {response}")
                response_str = response.decode('utf-8', errors='ignore')
                
                # Check for success indicators
                if b'+CMGS:' in response or b'OK' in response:
                    log_message(f"[SMS] ✓ Message sent successfully")
                    return True, "SMS sent successfully"
                elif b'ERROR' in response or b'CMS ERROR' in response:
                    # Extract error code if possible
                    error_match = re.search(r'ERROR:\s*(\d+)', response_str)
                    error_code = error_match.group(1) if error_match else "Unknown"
                    log_message(f"[SMS] ✗ CMS Error {error_code}")
                    
                    # CMS ERROR 500 usually means device doesn't support feature
                    # Try sending without fancy encoding
                    log_message(f"[SMS] Retrying with basic encoding...")
                    modem.flushInput()
                    modem.flushOutput()
                    time.sleep(0.5)
                    
                    # Try again
                    modem.write(cmd.encode())
                    time.sleep(1.5)
                    response = modem.read(100)
                    
                    if b'>' in response:
                        modem.write(message_text.encode('utf-8', errors='ignore'))
                        time.sleep(0.5)
                        modem.write(b'\x1A')
                        time.sleep(3)
                        response = modem.read(500)
                        
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
    global modem
    
    with modem_lock:
        try:
            if modem is None or not modem_connected:
                return False, "Modem not connected"
            
            # Handle both single ID and list of IDs
            if isinstance(modem_ids, list):
                for modem_id in modem_ids:
                    log_message(f"[DELETE] Deleting SMS with ID {modem_id} from modem...")
                    cmd = f'AT+CMGD={modem_id}\r\n'
                    modem.write(cmd.encode())
                    time.sleep(0.5)
                    response = modem.read(100)
                    log_message(f"[DELETE] Response: {response}")
            else:
                modem_id = modem_ids
                log_message(f"[DELETE] Deleting SMS with ID {modem_id} from modem...")
                cmd = f'AT+CMGD={modem_id}\r\n'
                modem.write(cmd.encode())
                time.sleep(0.5)
                response = modem.read(100)
                log_message(f"[DELETE] Response: {response}")
            
            log_message(f"[DELETE] ✓ SMS deleted from modem")
            return True, "SMS deleted"
        except Exception as e:
            log_message(f"[DELETE ERROR] {str(e)}")
            return False, str(e)

def clear_sim_storage():
    """Delete all SMS from modem SIM card storage"""
    global modem
    
    with modem_lock:
        try:
            if modem is None or not modem_connected:
                return False, "Modem not connected"
            
            log_message(f"[CLEAR] Deleting all SMS from SIM card...")
            modem.write(b'AT+CMGD=1,4\r\n')  # Delete all messages
            time.sleep(2)  # Increased wait time
            response = modem.read(200)
            log_message(f"[CLEAR] Response: {response}")
            
            # Check for OK in response
            if b'OK' in response or len(response) > 0:
                log_message(f"[CLEAR] ✓ All SMS deleted from SIM card")
                return True, "SIM storage cleared"
            else:
                log_message(f"[CLEAR] Checking storage status...")
                # Verify by checking storage
                modem.write(b'AT+CPMS?\r\n')
                time.sleep(1)
                status_response = modem.read(200)
                log_message(f"[CLEAR] Storage status: {status_response}")
                
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
    # Get SIM card usage
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
        
        # Send via modem
        success, status = send_sms(phone, message_text)
        
        # Store message
        message = {
            'type': 'sent',
            'phone': phone,
            'message': message_text,
            'timestamp': datetime.now().isoformat(),
            'status': 'sent' if success else 'failed'
        }
        
        messages['sent'].append(message)
        
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
    """Delete a message from the list and modem"""
    try:
        log_message(f"[API] Delete request for message index {message_index}")
        
        # Find the message in the list
        all_messages = messages['sent'] + messages['received']
        all_messages.sort(key=lambda x: x['timestamp'], reverse=True)
        
        if message_index >= len(all_messages):
            return jsonify({'status': 'error', 'message': 'Message not found'}), 404
        
        message = all_messages[message_index]
        
        # If it's a received message, try to delete from modem
        if message['type'] == 'received':
            # Find the modem ID(s)
            for idx, msg in enumerate(messages['received']):
                if msg == message:
                    if idx in sms_modem_indices:
                        modem_ids = sms_modem_indices[idx]
                        success, status = delete_sms_from_modem(modem_ids)
                        if not success:
                            log_message(f"[API] Warning: Failed to delete from modem but removing from list")
                    
                    # Remove from in-memory storage
                    messages['received'].pop(idx)
                    if idx in sms_modem_indices:
                        del sms_modem_indices[idx]
                    break
        else:
            # For sent messages, just remove from memory
            for idx, msg in enumerate(messages['sent']):
                if msg == message:
                    messages['sent'].pop(idx)
                    break
        
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
        
        # Delete all from modem
        success, status = clear_sim_storage()
        
        if success:
            # Also clear the in-memory storage
            messages['sent'].clear()
            messages['received'].clear()
            sms_modem_indices.clear()
            pending_parts.clear()
            processed_messages.clear()
            
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
        return jsonify({'logs': logs[-100:]})  # Last 100 lines
    except:
        return jsonify({'logs': ['No logs available']})

if __name__ == '__main__':
    log_message("="*50)
    log_message("SMS DASHBOARD STARTING...")
    log_message("="*50)
    
    # Initialize modem on startup
    init_modem()
    
    log_message("="*50)
    log_message(f"Modem Status: {'CONNECTED' if modem_connected else 'NOT CONNECTED'}")
    log_message("="*50)
    
    # Start SMS receiver thread
    if modem_connected:
        start_receiver()
    
    try:
        app.run(debug=False, host='0.0.0.0', port=5000)
    finally:
        stop_receiver_thread()
        if modem:
            log_message("[MODEM] Closing connection...")
            modem.close()
