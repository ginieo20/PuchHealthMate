import asyncio
import threading
import os
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from huggingface_hub import InferenceClient
import httpx
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse, Response
from starlette.routing import Route, Mount
from starlette.middleware.wsgi import WSGIMiddleware

# Reuse the MCP server from mcp_starter
from mcp_starter import main as mcp_main

load_dotenv()

HF_TOKEN = os.environ.get("HF_TOKEN") or os.environ.get("HF_API_TOKEN")
HF_PROVIDER = os.environ.get("HF_PROVIDER")
MODEL_ID = os.environ.get("MODEL_ID", "google/flan-t5-large")
MCP_INTERNAL = "http://127.0.0.1:8086"

# Flask app for REST endpoints
flask_app = Flask(__name__)


# Start MCP server in a background thread exactly once
_mcp_started = False
_mcp_lock = threading.Lock()

def run_mcp_server():
    asyncio.run(mcp_main())


def ensure_mcp_started():
    global _mcp_started
    if not _mcp_started:
        with _mcp_lock:
            if not _mcp_started:
                t = threading.Thread(target=run_mcp_server, daemon=True)
                t.start()
                _mcp_started = True

# Ensure MCP starts at import time
ensure_mcp_started()


@flask_app.post("/api/chat")
def chat_completion():
    data = request.get_json(force=True)
    messages = data.get("messages")
    model = data.get("model") or MODEL_ID
    provider = data.get("provider") or HF_PROVIDER
    if not isinstance(messages, list) or not messages:
        return jsonify({"error": "messages must be a non-empty list of {role, content}"}), 400
    try:
        client = InferenceClient(provider=provider, api_key=HF_TOKEN)
        completion = client.chat.completions.create(model=model, messages=messages)
        content = completion.choices[0].message.content
        return jsonify({"content": content})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@flask_app.post("/api/generate")
def text_generate():
    data = request.get_json(force=True)
    prompt = data.get("prompt")
    model = data.get("model") or MODEL_ID
    if not prompt:
        return jsonify({"error": "prompt is required"}), 400
    try:
        client = InferenceClient(api_key=HF_TOKEN)
        text = client.text_generation(
            prompt,
            model=model,
            max_new_tokens=int(data.get("max_new_tokens", 256)),
            temperature=float(data.get("temperature", 0.7)),
            top_p=float(data.get("top_p", 0.95)),
            stream=False,
        )
        if isinstance(text, dict) and "generated_text" in text:
            text = text["generated_text"]
        return jsonify({"text": str(text)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# Starlette handlers for root health and /mcp proxy
async def root(request: Request) -> Response:
    return JSONResponse({"service": "HealthMate", "status": "ok"})


def _build_forward_headers(req: Request) -> dict[str, str]:
    headers: dict[str, str] = {}
    for k_bytes, v_bytes in req.headers.raw:
        k = k_bytes.decode() if isinstance(k_bytes, (bytes, bytearray)) else str(k_bytes)
        if k.lower() == "host":
            continue
        v = v_bytes.decode() if isinstance(v_bytes, (bytes, bytearray)) else str(v_bytes)
        headers[k] = v
    return headers


async def proxy_mcp(request: Request) -> Response:
    # Proxy GET/POST to internal MCP (supports streaming)
    path_suffix = "/" + request.path_params.get("path", "") if request.path_params.get("path") else ""
    target_url = f"{MCP_INTERNAL}/mcp{path_suffix}"
    headers = _build_forward_headers(request)
    timeout = httpx.Timeout(None)

    if request.method == "OPTIONS":
        return Response("", status_code=204, headers={
            "Access-Control-Allow-Origin": request.headers.get("origin", "*"),
            "Access-Control-Allow-Headers": request.headers.get("access-control-request-headers", "*"),
            "Access-Control-Allow-Methods": request.headers.get("access-control-request-method", "GET,POST,OPTIONS"),
            "Access-Control-Max-Age": "86400",
        })

    async with httpx.AsyncClient(timeout=timeout) as client:
        if request.method == "GET":
            upstream = await client.stream("GET", target_url, headers=headers, params=dict(request.query_params))
        elif request.method == "POST":
            body = await request.body()
            upstream = await client.stream("POST", target_url, headers=headers, content=body)
        else:
            return Response(status_code=405)

        async def aiter_bytes():
            async for chunk in upstream.aiter_bytes():
                if chunk:
                    yield chunk
            await upstream.aclose()

        content_type = upstream.headers.get("content-type", "application/octet-stream")
        resp = StreamingResponse(aiter_bytes(), status_code=upstream.status_code, media_type=content_type)
        resp.headers["Cache-Control"] = upstream.headers.get("cache-control", "no-cache")
        resp.headers["Connection"] = upstream.headers.get("connection", "keep-alive")
        if upstream.headers.get("x-accel-buffering"):
            resp.headers["X-Accel-Buffering"] = upstream.headers["x-accel-buffering"]
        resp.headers["Access-Control-Allow-Origin"] = request.headers.get("origin", "*")
        return resp


routes = [
    Route("/", root, methods=["GET"]),
    Route("/mcp", proxy_mcp, methods=["GET", "POST", "OPTIONS"]),
    Route("/mcp/{path:path}", proxy_mcp, methods=["GET", "POST", "OPTIONS"]),
    Mount("/", app=WSGIMiddleware(flask_app)),
]

middleware = [Middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"], allow_credentials=True)]

asgi_app = Starlette(routes=routes, middleware=middleware)


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(asgi_app, host="0.0.0.0", port=port, log_level="info")