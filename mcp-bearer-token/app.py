from flask import Flask, request
import os

app = Flask(__name__)

@app.route('/mcp/', methods=['GET', 'POST', 'OPTIONS', 'HEAD'])
def mcp_endpoint():
    method = request.method
    if method == 'OPTIONS':
        return ('', 204, {
            'Access-Control-Allow-Origin': request.headers.get('Origin', '*'),
            'Access-Control-Allow-Headers': request.headers.get('Access-Control-Request-Headers', '*'),
            'Access-Control-Allow-Methods': 'GET,POST,HEAD,OPTIONS',
            'Access-Control-Max-Age': '86400',
        })
    if method == 'HEAD':
        return ('', 200, {'Content-Type': 'application/json'})
    # GET or POST
    return {"status": "active", "mcp_id": "HealthMate"}, 200

@app.route('/', methods=['GET'])
def health_check():
    return {"status": "ok"}, 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)