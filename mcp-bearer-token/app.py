from flask import Flask
import os

app = Flask(__name__)

@app.route('/mcp/', methods=['GET'])
def mcp_endpoint():
    try:
        return {"status": "active", "mcp_id": "HealthMate"}, 200
    except Exception as e:
        return {"error": str(e)}, 500

@app.route('/', methods=['GET'])
def health_check():
    return {"status": "ok"}, 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)