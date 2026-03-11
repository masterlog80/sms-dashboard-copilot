from flask import Flask, render_template, request, jsonify
import os

app = Flask(__name__, template_folder='.')

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/message', methods=['POST'])
def receive_message():
    data = request.json
    response = {'status': 'success', 'data': data}
    return jsonify(response)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
