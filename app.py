from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route('/api/message', methods=['POST'])
def receive_message():
    data = request.json
    # Process the incoming message
    response = {'status': 'success', 'data': data}
    return jsonify(response)

if __name__ == '__main__':
    app.run(debug=True)