from flask import Flask, render_template, request, jsonify
import os
import json
import serial
import time
from datetime import datetime

app = Flask(__name__, template_folder='.')

# In-memory message storage
messages = {
    'sent': [],
    'received': []
}

# GSM Modem setup
modem = None
modem_port = '/dev/ttyUSB0'
modem_baudrate = 9600
modem_connected = False

def init_modem():
    """Initialize GSM modem connection"""
    global modem, modem_connected
    
    print(f"[MODEM] Attempting to initialize on {modem_port}...")
    
    # Check if device exists
    if not os.path.exists(modem_port):
        print(f"[MODEM ERROR] Device {modem_port} does not exist")
        modem_connected = False
        return False
    
    try:
        modem = serial.Serial(modem_port, modem_baudrate, timeout=1)
        print(f"[MODEM] Port opened: {modem_port}")
        time.sleep(2)
        
        # Clear buffer
        modem.flushInput()
        modem.flushOutput()
        
        # Test modem with AT command
        print("[MODEM] Sending AT command...")
        modem.write(b'AT\r\n')
        time.sleep(1)
        response = modem.read(100)
        print(f"[MODEM] Response: {response}")
        
        if b'OK' in response:
            print("[MODEM] ✓ Modem detected!")
            
            # Set text mode
            print("[MODEM] Setting text mode...")
            modem.write(b'AT+CMGF=1\r\n')
            time.sleep(0.5)
            response = modem.read(100)
            print(f"[MODEM] Text mode response: {response}")
            
            modem_connected = True
            return True
        else:
            print("[MODEM] ✗ No OK response from modem")
            modem.close()
            modem = None
            modem_connected = False
            return False
    except serial.SerialException as e:
        print(f"[MODEM ERROR] Serial exception: {str(e)}")
        modem_connected = False
        return False
    except Exception as e:
        print(f"[MODEM ERROR] {str(e)}")
        modem_connected = False
        return False

def send_sms(phone, message_text):
    """Send SMS via GSM modem"""
    global modem
    
    print(f"[SMS] Attempting to send to {phone}")
    
    try:
        if modem is None or not modem_connected:
            print(f"[SMS ERROR] Modem not connected")
            return False, "Modem not connected. Check /dev/ttyUSB0"
        
        # Send SMS command
        print(f"[SMS] Sending CMGS command...")
        cmd = f'AT+CMGS="{phone}"\r\n'
        modem.write(cmd.encode())
        time.sleep(1)
        
        response = modem.read(100)
        print(f"[SMS] CMGS response: {response}")
        
        if b'>' in response:
            print(f"[SMS] Modem ready, sending message...")
            modem.write(message_text.encode())
            time.sleep(0.5)
            modem.write(b'\x1A')  # Ctrl+Z
            time.sleep(2)
            
            response = modem.read(200)
            print(f"[SMS] Send response: {response}")
            
            if b'+CMGS:' in response or b'OK' in response:
                print(f"[SMS] ✓ Message sent successfully")
                return True, "SMS sent successfully"
            else:
                print(f"[SMS] ✗ Unexpected response")
                return False, f"Unexpected modem response"
        else:
            print(f"[SMS] ✗ Modem not ready for message input")
            return False, "Modem did not accept message command"
            
    except Exception as e:
        print(f"[SMS ERROR] {str(e)}")
        return False, f"Error sending SMS: {str(e)}"

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
        
        print(f"\n[API] Received SMS request: {phone} - {message_text[:30]}...")
        
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
            print(f"[API] ✓ SMS stored and sent")
            return jsonify({
                'status': 'success',
                'message': 'SMS sent successfully',
                'data': message
            }), 200
        else:
            print(f"[API] ✗ SMS stored but failed to send: {status}")
            return jsonify({
                'status': 'error',
                'message': status,
                'data': message
            }), 500
    except Exception as e:
        print(f"[API ERROR] {str(e)}")
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

if __name__ == '__main__':
    print("\n" + "="*50)
    print("SMS DASHBOARD STARTING...")
    print("="*50)
    
    # Initialize modem on startup
    init_modem()
    
    print("="*50)
    print(f"Modem Status: {'CONNECTED' if modem_connected else 'NOT CONNECTED'}")
    print("="*50 + "\n")
    
    try:
        app.run(debug=False, host='0.0.0.0', port=5000)
    finally:
        if modem:
            print("[MODEM] Closing connection...")
            modem.close()
