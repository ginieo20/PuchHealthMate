import os
import threading
import asyncio
from flask import Flask, request, jsonify
from flask_sock import Sock

from fastmcp.server import Server
from fastmcp.server.auth.providers.bearer import BearerAuthProvider, RSAKeyPair

# ===== MCP SETUP =====
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "test-token")
SERVER_ID = "HealthMate"

mcp_server = Server(
    id=SERVER_ID,
    auth_provider=BearerAuthProvider(
        token=AUTH_TOKEN,
        key_pair=RSAKeyPair.generate()
    )
)

# Example MCP tool
@mcp_server.tool()
async def ping():
    return {"message": "pong"}


def run_mcp():
    asyncio.run(mcp_server.run(host="127.0.0.1", port=8765))  # internal only

# ===== FLASK API SETUP =====
app = Flask(__name__)
sock = Sock(app)


@app.route("/")
def home():
    return "HealthMate + MCP is running"


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.json or {}
    messages = data.get("messages", [])
    # For now, just echo
    return jsonify({"reply": f"Echo: {messages[-1]['content'] if messages else ''}"})

# ===== /mcp WebSocket bridge =====
import websockets


@sock.route('/mcp')
def mcp_proxy(ws):
    """WebSocket proxy between Puch AI and internal MCP server."""
    uri = "ws://127.0.0.1:8765/mcp"

    async def bridge():
        async with websockets.connect(uri, extra_headers={"Authorization": f"Bearer {AUTH_TOKEN}"}) as mcp_ws:
            async def ws_to_mcp():
                while True:
                    msg = await asyncio.to_thread(ws.receive)
                    if msg is None:
                        break
                    await mcp_ws.send(msg)

            async def mcp_to_ws():
                async for msg in mcp_ws:
                    await asyncio.to_thread(ws.send, msg)

            await asyncio.gather(ws_to_mcp(), mcp_to_ws())

    asyncio.run(bridge())


# ===== MAIN ENTRY =====
if __name__ == "__main__":
    # Start MCP server in background
    threading.Thread(target=run_mcp, daemon=True).start()
    print(f"[MCP] Server started with ID: {SERVER_ID} and token: {AUTH_TOKEN}")

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)