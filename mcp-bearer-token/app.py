import os
import asyncio
import threading

import httpx
from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.server.auth.providers.bearer import BearerAuthProvider, RSAKeyPair
from mcp.server.auth.provider import AccessToken
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

# Load env
load_dotenv()
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "test-token")
SERVER_ID = "HealthMate"
INTERNAL_MCP = "http://127.0.0.1:8765/mcp/"


class SimpleBearerAuthProvider(BearerAuthProvider):
    def __init__(self, token: str):
        k = RSAKeyPair.generate()
        super().__init__(public_key=k.public_key, jwks_uri=None, issuer=None, audience=None)
        self._token = token

    async def load_access_token(self, token: str) -> AccessToken | None:
        if token == self._token:
            return AccessToken(token=token, client_id="puch-client", scopes=["*"], expires_at=None)
        return None


# Start FastMCP internally on 127.0.0.1:8765
mcp = FastMCP(SERVER_ID, auth=SimpleBearerAuthProvider(AUTH_TOKEN))


def _start_internal_mcp():
    async def _run():
        print("[MCP] Starting internal MCP on 127.0.0.1:8765/mcp/")
        await mcp.run_async("streamable-http", host="127.0.0.1", port=8765)
    asyncio.run(_run())


# Health/root handlers
async def root(_: Request) -> Response:
    return JSONResponse({"message": "Server is running"})


async def health(_: Request) -> Response:
    return JSONResponse({"status": "ok"})


# Proxy helpers
def _forward_headers(req: Request) -> dict:
    headers: dict[str, str] = {}
    # Preserve Authorization, Accept, Content-Type if present
    for name in ("authorization", "accept", "content-type"):
        val = req.headers.get(name)
        if val:
            headers[name.title()] = val
    return headers


async def proxy_mcp(request: Request) -> Response:
    # Compose target URL (preserve any subpath)
    subpath = request.path_params.get("path", "")
    target = INTERNAL_MCP + subpath
    headers = _forward_headers(request)
    timeout = httpx.Timeout(None)

    if request.method == "OPTIONS":
        return Response("", status_code=204, headers={
            "Access-Control-Allow-Origin": request.headers.get("origin", "*"),
            "Access-Control-Allow-Headers": request.headers.get("access-control-request-headers", "*"),
            "Access-Control-Allow-Methods": request.headers.get("access-control-request-method", "GET,POST,HEAD,OPTIONS"),
            "Access-Control-Max-Age": "86400",
        })

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        if request.method == "HEAD":
            r = await client.request("HEAD", target, headers=headers, params=dict(request.query_params))
            resp = Response("", status_code=r.status_code)
            resp.headers["Content-Type"] = r.headers.get("content-type", "application/octet-stream")
            resp.headers["Cache-Control"] = r.headers.get("cache-control", "no-cache")
            resp.headers["Connection"] = r.headers.get("connection", "keep-alive")
            resp.headers["X-Accel-Buffering"] = "no"
            resp.headers["Access-Control-Allow-Origin"] = request.headers.get("origin", "*")
            return resp

        if request.method == "GET":
            upstream = await client.stream("GET", target, headers=headers, params=dict(request.query_params))
        elif request.method == "POST":
            body = await request.body()
            upstream = await client.stream("POST", target, headers=headers, content=body)
        else:
            return Response(status_code=405)

        async def aiter():
            async for chunk in upstream.aiter_bytes():
                if chunk:
                    yield chunk
            await upstream.aclose()

        content_type = upstream.headers.get("content-type", "application/octet-stream")
        resp = StreamingResponse(aiter(), status_code=upstream.status_code, media_type=content_type)
        resp.headers["Cache-Control"] = upstream.headers.get("cache-control", "no-cache")
        resp.headers["Connection"] = upstream.headers.get("connection", "keep-alive")
        resp.headers["X-Accel-Buffering"] = "no"
        resp.headers["Access-Control-Allow-Origin"] = request.headers.get("origin", "*")
        return resp


routes = [
    Route("/", root, methods=["GET"]),
    Route("/health", health, methods=["GET"]),
    Route("/mcp", proxy_mcp, methods=["GET", "POST", "HEAD", "OPTIONS"]),
    Route("/mcp/", proxy_mcp, methods=["GET", "POST", "HEAD", "OPTIONS"]),
    Route("/mcp/{path:path}", proxy_mcp, methods=["GET", "POST", "HEAD", "OPTIONS"]),
]

app = Starlette(routes=routes)


if __name__ == "__main__":
    # Start internal MCP in background
    threading.Thread(target=_start_internal_mcp, daemon=True).start()
    # Run Starlette on Render’s $PORT
    import uvicorn
    port = int(os.environ.get("PORT", "10000"))
    print(f"[HTTP] Health + MCP proxy on 0.0.0.0:{port} (health=/health, mcp=/mcp/)")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")