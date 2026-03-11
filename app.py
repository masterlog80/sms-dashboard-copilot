from flask import Flask, render_template, request, jsonify
import os
import json
from datetime import datetime

app = Flask(__name__, template_folder='.')

# In-memory message storage (replace with database later)
messages = {
    'sent': [],
    'received': []
}

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
    # Sort by timestamp (newest first)
    all_messages.sort(key=lambda x: x['timestamp'], reverse=True)
    return jsonify(all_messages[:50])  # Return last 50 messages

@app.route('/api/message', methods=['POST'])
def send_message():
    try:
        data = request.json
        
        # Create message object
        message = {
            'type': 'sent',
            'phone': data.get('phone'),
            'message': data.get('message'),
            'timestamp': datetime.now().isoformat(),
            'status': 'sent'
        }
        
        # Store message
        messages['sent'].append(message)
        
        print(f"SMS sent to {data.get('phone')}: {data.get('message')}")
        
        return jsonify({
            'status': 'success',
            'message': 'SMS sent successfully',
            'data': message
        }), 200
    except Exception as e:
        print(f"Error sending SMS: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/api/receive', methods=['POST'])
def receive_sms():
    """Endpoint to receive incoming SMS from GSM modem"""
    try:
        data = request.json
        
        message = {
            'type': 'received',
            'phone': data.get('phone'),
            'message': data.get('message'),
            'timestamp': datetime.now().isoformat(),
            'status': 'received'
        }
        
        messages['received'].append(message)
        
        print(f"SMS received from {data.get('phone')}: {data.get('message')}")
        
        return jsonify({
            'status': 'success',
            'message': 'SMS received'
        }), 200
    except Exception as e:
        print(f"Error receiving SMS: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
