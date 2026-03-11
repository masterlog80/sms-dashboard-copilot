from flask import Flask, render_template, request, jsonify
import os
import json
import serial
import time
from datetime import datetime
import threading

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

def init_modem():
    """Initialize GSM modem connection"""
    global modem
    try:
        modem = serial.Serial(modem_port, modem_baudrate, timeout=1)
        time.sleep(1)
        
        # Test modem with AT command
        modem.write(b'AT\r\n')
        time.sleep(0.5)
        response = modem.read(100)
        
        if b'OK' in response:
            print("✓ GSM Modem initialized successfully")
            # Set text mode
            modem.write(b'AT+CMGF=1\r\n')
            time.sleep(0.5)
            modem.read(100)
            return True
        else:
            print("✗ GSM Modem not responding")
            modem.close()
            modem = None
            return False
    except Exception as e:
        print(f"✗ Error initializing modem: {str(e)}")
        modem = None
        return False

def send_sms(phone, message_text):
    """Send SMS via GSM modem"""
    try:
        if modem is None:
            return False, "Modem not connected"
        
        # Send SMS command
        cmd = f'AT+CMGS="{phone}"\r\n'
        modem.write(cmd.encode())
        time.sleep(0.5)
        
        # Send message text
        modem.write(message_text.encode())
        time.sleep(0.5)
        
        # Send Ctrl+Z to send
        modem.write(b'\x1A')
        time.sleep(1)
        
        response = modem.read(200).decode('utf-8', errors='ignore')
        
        if '+CMGS:' in response or 'OK' in response:
            return True, "SMS sent successfully"
        else:
            return False, f"Modem error: {response}"
    except Exception as e:
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
        'received_messages': len(messages['received'])
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
            print(f"✓ SMS sent to {phone}")
            return jsonify({
                'status': 'success',
                'message': 'SMS sent successfully',
                'data': message
            }), 200
        else:
            print(f"✗ Failed to send SMS to {phone}: {status}")
            return jsonify({
                'status': 'error',
                'message': status,
                'data': message
            }), 500
    except Exception as e:
        print(f"✗ Error: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/api/modem/status', methods=['GET'])
def modem_status():
    """Check modem connection status"""
    status = modem is not None
    return jsonify({
        'connected': status,
        'port': modem_port,
        'baudrate': modem_baudrate
    })

if __name__ == '__main__':
    # Initialize modem on startup
    init_modem()
    
    try:
        app.run(debug=False, host='0.0.0.0', port=5000)
    finally:
        if modem:
            modem.close()
