import asyncio
import threading
import os
from flask import Flask, request, jsonify, Response, stream_with_context, make_response
from dotenv import load_dotenv
from huggingface_hub import InferenceClient
import httpx

# Reuse the MCP server from mcp_starter
from mcp_starter import main as mcp_main

load_dotenv()

HF_TOKEN = os.environ.get("HF_TOKEN") or os.environ.get("HF_API_TOKEN")
HF_PROVIDER = os.environ.get("HF_PROVIDER")
MODEL_ID = os.environ.get("MODEL_ID", "google/flan-t5-large")
MCP_INTERNAL = "http://127.0.0.1:8086"

app = Flask(__name__)


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


@app.get("/")
def health_root():
    return jsonify({"service": "HealthMate", "status": "ok"}), 200


# Reverse proxy any /mcp* path to the internal MCP server
@app.route("/mcp", defaults={"path": ""}, methods=["GET", "POST", "OPTIONS"])
@app.route("/mcp/<path:path>", methods=["GET", "POST", "OPTIONS"])
def proxy_mcp(path: str):
    target_url = f"{MCP_INTERNAL}/mcp/{path}" if path else f"{MCP_INTERNAL}/mcp"
    headers = {k: v for k, v in request.headers if k.lower() != "host"}
    method = request.method.upper()

    if method == "OPTIONS":
        resp = make_response("", 204)
        resp.headers["Access-Control-Allow-Origin"] = request.headers.get("Origin", "*")
        resp.headers["Access-Control-Allow-Headers"] = request.headers.get("Access-Control-Request-Headers", "*")
        resp.headers["Access-Control-Allow-Methods"] = request.headers.get("Access-Control-Request-Method", "GET,POST,OPTIONS")
        resp.headers["Access-Control-Max-Age"] = "86400"
        return resp

    if method == "GET":
        client = httpx.Client(timeout=None)
        r = client.build_request("GET", target_url, headers=headers, params=request.args)
        def generate():
            with client.stream(r.method, r.url, headers=r.headers, params=request.args, timeout=None) as upstream:
                for chunk in upstream.iter_bytes():
                    if chunk:
                        yield chunk
        # Fetch only headers and status first
        with client.stream("GET", target_url, headers=headers, params=request.args, timeout=None) as upstream_head:
            status = upstream_head.status_code
            content_type = upstream_head.headers.get("content-type", "application/octet-stream")
            resp = Response(stream_with_context(generate()), status=status, direct_passthrough=True)
            resp.headers["Content-Type"] = content_type
            # Pass through cache/control headers helpful for SSE
            for h in ("cache-control", "connection", "transfer-encoding", "x-accel-buffering"):
                if upstream_head.headers.get(h):
                    resp.headers[h.title()] = upstream_head.headers[h]
            return resp

    elif method == "POST":
        data = request.get_data()
        client = httpx.Client(timeout=None)
        with client.stream("POST", target_url, headers=headers, content=data, timeout=None) as upstream:
            status = upstream.status_code
            content_type = upstream.headers.get("content-type", "application/octet-stream")
            def generate_post():
                for chunk in upstream.iter_bytes():
                    if chunk:
                        yield chunk
            resp = Response(stream_with_context(generate_post()), status=status, direct_passthrough=True)
            resp.headers["Content-Type"] = content_type
            for h in ("cache-control", "connection", "transfer-encoding", "x-accel-buffering"):
                if upstream.headers.get(h):
                    resp.headers[h.title()] = upstream.headers[h]
            return resp

    return Response(status=405)


@app.post("/api/chat")
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


@app.post("/api/generate")
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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)