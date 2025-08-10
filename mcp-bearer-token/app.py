import os
import threading
import asyncio
from flask import Flask, request, jsonify, Response, stream_with_context
from flask_sock import Sock
from dotenv import load_dotenv
import httpx

from fastmcp import FastMCP
from fastmcp.server.auth.providers.bearer import BearerAuthProvider, RSAKeyPair
from mcp.server.auth.provider import AccessToken

# ===== MCP SETUP =====
load_dotenv()
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "test-token")
SERVER_ID = "HealthMate"


class SimpleBearerAuthProvider(BearerAuthProvider):
    def __init__(self, token: str):
        k = RSAKeyPair.generate()
        super().__init__(public_key=k.public_key, jwks_uri=None, issuer=None, audience=None)
        self._token = token

    async def load_access_token(self, token: str) -> AccessToken | None:
        if token == self._token:
            return AccessToken(token=token, client_id="puch-client", scopes=["*"], expires_at=None)
        return None


mcp = FastMCP(SERVER_ID, auth=SimpleBearerAuthProvider(AUTH_TOKEN))


async def run_mcp_async():
    await mcp.run_async("streamable-http", host="127.0.0.1", port=8765)


def run_mcp():
    asyncio.run(run_mcp_async())

# ===== FLASK API SETUP =====
app = Flask(__name__)
sock = Sock(app)


@app.route("/")
def home():
    return "HealthMate + MCP is running"


@app.route("/mcp", methods=["GET", "POST", "OPTIONS"])
@app.route("/mcp/<path:path>", methods=["GET", "POST", "OPTIONS"])
def mcp_http_proxy(path: str = ""):
    target = f"http://127.0.0.1:8765/mcp" + (f"/{path}" if path else "")
    headers = {k: v for k, v in request.headers if k.lower() != "host"}

    if request.method == "OPTIONS":
        resp = Response("", status=204)
        resp.headers["Access-Control-Allow-Origin"] = request.headers.get("Origin", "*")
        resp.headers["Access-Control-Allow-Headers"] = request.headers.get("Access-Control-Request-Headers", "*")
        resp.headers["Access-Control-Allow-Methods"] = request.headers.get("Access-Control-Request-Method", "GET,POST,OPTIONS")
        resp.headers["Access-Control-Max-Age"] = "86400"
        return resp

    client = httpx.Client(timeout=None)

    if request.method == "GET":
        upstream = client.stream("GET", target, headers=headers, params=request.args)
    elif request.method == "POST":
        upstream = client.stream("POST", target, headers=headers, content=request.get_data())
    else:
        return Response(status=405)

    def generate():
        with upstream as r:
            for chunk in r.iter_bytes():
                if chunk:
                    yield chunk

    with client.stream("GET", target, headers=headers, params=request.args if request.method == "GET" else None) as rhead:
        resp = Response(stream_with_context(generate()), status=rhead.status_code)
        ctype = rhead.headers.get("content-type", "application/octet-stream")
        resp.headers["Content-Type"] = ctype
        resp.headers["Cache-Control"] = rhead.headers.get("cache-control", "no-cache")
        resp.headers["Connection"] = rhead.headers.get("connection", "keep-alive")
        resp.headers["X-Accel-Buffering"] = "no"
        resp.headers["Access-Control-Allow-Origin"] = request.headers.get("Origin", "*")
        return resp


# ===== MAIN ENTRY =====
if __name__ == "__main__":
    threading.Thread(target=run_mcp, daemon=True).start()
    print(f"[MCP] Server started with ID: {SERVER_ID} and token: {AUTH_TOKEN}")
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)