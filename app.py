from flask import Flask, render_template, request, jsonify
import os
import json
import serial
import time
from datetime import datetime
import sys
import threading

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
        if all(c in '0123456789ABCDEFabcdef' for c in text.strip()):
            # Try UTF-16BE decoding (common for hex SMS)
            decoded = bytes.fromhex(text).decode('utf-16-be', errors='ignore')
            if decoded and len(decoded) > 0:
                return decoded
    except:
        pass
    
    try:
        # Try UTF-8 decoding
        if all(c in '0123456789ABCDEFabcdef' for c in text.strip()):
            decoded = bytes.fromhex(text).decode('utf-8', errors='ignore')
            if decoded and len(decoded) > 0:
                return decoded
    except:
        pass
    
    return text  # Return original if decoding fails

# In-memory message storage
messages = {
    'sent': [],
    'received': []
}

# Track modem SMS indices for deletion
sms_modem_indices = {}  # Map of message index to modem message ID

# Track SMS parts for concatenation
sms_parts = {}  # Map of (phone, timestamp) to list of parts

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

def read_sms_from_sim():
    """Read SMS from SIM card storage"""
    global modem, processed_messages, sms_modem_indices, sms_parts
    
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
                            
                            # Skip if we've already processed this message
                            if msg_id in processed_messages:
                                log_message(f"[RECEIVER] Message {msg_id} already processed, skipping")
                                i += 1
                                continue
                            
                            # Message text is on next line
                            if i + 1 < len(lines):
                                message_text = lines[i + 1].strip()
                                
                                # Try to decode if it's hex
                                decoded_text = try_decode_hex(message_text)
                                
                                if decoded_text:
                                    # Create a key for grouping multi-part messages
                                    msg_key = (phone, timestamp_part)
                                    
                                    # Store this part
                                    if msg_key not in sms_parts:
                                        sms_parts[msg_key] = []
                                    
                                    sms_parts[msg_key].append({
                                        'id': msg_id,
                                        'text': decoded_text,
                                        'order': len(sms_parts[msg_key])
                                    })
                                    
                                    processed_messages.add(msg_id)
                                    log_message(f"[RECEIVER] Part {len(sms_parts[msg_key])} from {phone}: {decoded_text[:30]}...")
                    except Exception as e:
                        log_message(f"[RECEIVER] Error parsing line '{line}': {str(e)}")
                
                i += 1
            
            # Now combine multi-part messages and store them
            for msg_key, parts_list in list(sms_parts.items()):
                # Sort parts by order
                parts_list.sort(key=lambda x: x['order'])
                
                # Check if we have all parts (this is a heuristic - we assume a message is complete after no new parts)
                # For now, just combine what we have
                combined_text = ''.join([p['text'] for p in parts_list])
                phone, timestamp_part = msg_key
                
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
                    
                    # Remove from sms_parts since we've stored it
                    del sms_parts[msg_key]
        else:
            log_message(f"[RECEIVER] No +CMGL: in response (no messages or error)")
            
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
    
    log_message(f"[SMS] Attempting to send to {phone}")
    
    try:
        if modem is None or not modem_connected:
            log_message(f"[SMS ERROR] Modem not connected")
            return False, "Modem not connected. Check /dev/ttyUSB0"
        
        # Send SMS command
        log_message(f"[SMS] Sending CMGS command...")
        cmd = f'AT+CMGS="{phone}"\r\n'
        modem.write(cmd.encode())
        time.sleep(1)
        
        response = modem.read(100)
        log_message(f"[SMS] CMGS response: {response}")
        
        if b'>' in response:
            log_message(f"[SMS] Modem ready, sending message...")
            modem.write(message_text.encode())
            time.sleep(0.5)
            modem.write(b'\x1A')  # Ctrl+Z
            time.sleep(2)
            
            response = modem.read(200)
            log_message(f"[SMS] Send response: {response}")
            
            if b'+CMGS:' in response or b'OK' in response:
                log_message(f"[SMS] ✓ Message sent successfully")
                return True, "SMS sent successfully"
            else:
                log_message(f"[SMS] ✗ Unexpected response")
                return False, f"Unexpected modem response"
        else:
            log_message(f"[SMS] ✗ Modem not ready for message input")
            return False, "Modem did not accept message command"
            
    except Exception as e:
        log_message(f"[SMS ERROR] {str(e)}")
        return False, f"Error sending SMS: {str(e)}"

def delete_sms_from_modem(modem_ids):
    """Delete SMS from modem by ID(s)"""
    global modem
    
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

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/stats', methods=['GET'])
def get_stats():
    return jsonify({
        'total_messages': len(messages['sent']) + len(messages['received']),
        'unread_messages': len(messages['received']),
        'sent_messages': len(messages['sent']),
        'received_messages': len(messages['received']),
        'modem_connected': modem_connected
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
