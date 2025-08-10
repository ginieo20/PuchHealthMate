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


@app.route("/mcp", methods=["GET", "POST", "OPTIONS", "HEAD"])
@app.route("/mcp/", methods=["GET", "POST", "OPTIONS", "HEAD"])  # explicit trailing-slash alias
@app.route("/mcp/<path:path>", methods=["GET", "POST", "OPTIONS", "HEAD"])
def mcp_http_proxy(path: str = ""):
    # Always use trailing slash base to avoid redirects from the internal server
    base = "http://127.0.0.1:8765/mcp/"
    target = base + (path or "")
    # Forward only essential headers, ensure Authorization is preserved exactly
    headers = {}
    auth_header = request.headers.get("Authorization") or request.headers.get("authorization")
    if auth_header:
        headers["Authorization"] = auth_header
    accept = request.headers.get("Accept")
    if accept:
        headers["Accept"] = accept
    content_type = request.headers.get("Content-Type")
    if content_type:
        headers["Content-Type"] = content_type

    if request.method == "OPTIONS":
        resp = Response("", status=204)
        resp.headers["Access-Control-Allow-Origin"] = request.headers.get("Origin", "*")
        resp.headers["Access-Control-Allow-Headers"] = request.headers.get("Access-Control-Request-Headers", "*")
        resp.headers["Access-Control-Allow-Methods"] = request.headers.get("Access-Control-Request-Method", "GET,POST,HEAD,OPTIONS")
        resp.headers["Access-Control-Max-Age"] = "86400"
        return resp

    client = httpx.Client(timeout=None, follow_redirects=False)

    if request.method == "HEAD":
        r = client.request("HEAD", target, headers=headers, params=request.args)
        resp = Response("", status=r.status_code)
        resp.headers["Content-Type"] = r.headers.get("content-type", "application/octet-stream")
        resp.headers["Cache-Control"] = r.headers.get("cache-control", "no-cache")
        resp.headers["Connection"] = r.headers.get("connection", "keep-alive")
        resp.headers["X-Accel-Buffering"] = "no"
        resp.headers["Access-Control-Allow-Origin"] = request.headers.get("Origin", "*")
        return resp

    if request.method == "GET":
        upstream = client.stream("GET", target, headers=headers, params=request.args)
    elif request.method == "POST":
        upstream = client.stream("POST", target, headers=headers, content=request.get_data())
    else:
        return Response(status=405)

    def generate_stream(r: httpx.Response):
        with r as rr:
            for chunk in rr.iter_bytes():
                if chunk:
                    yield chunk

    with upstream as r:
        status = r.status_code
        ctype = r.headers.get("content-type", "application/octet-stream")
        resp = Response(stream_with_context(generate_stream(r)), status=status)
        resp.headers["Content-Type"] = ctype
        resp.headers["Cache-Control"] = r.headers.get("cache-control", "no-cache")
        resp.headers["Connection"] = r.headers.get("connection", "keep-alive")
        resp.headers["X-Accel-Buffering"] = "no"
        resp.headers["Access-Control-Allow-Origin"] = request.headers.get("Origin", "*")
        return resp


# ===== MAIN ENTRY =====
if __name__ == "__main__":
    threading.Thread(target=run_mcp, daemon=True).start()
    print(f"[MCP] Server started with ID: {SERVER_ID} and token: {AUTH_TOKEN}")
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)