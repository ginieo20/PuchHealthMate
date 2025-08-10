import asyncio
import threading
import os
from flask import Flask, request, jsonify
from flask_sock import Sock
from dotenv import load_dotenv
from huggingface_hub import InferenceClient
import websockets
import httpx

# Reuse the MCP server from mcp_starter
from mcp_starter import main as mcp_main

load_dotenv()

HF_TOKEN = os.environ.get("HF_TOKEN") or os.environ.get("HF_API_TOKEN")
HF_PROVIDER = os.environ.get("HF_PROVIDER")
MODEL_ID = os.environ.get("MODEL_ID", "google/flan-t5-large")
AUTH_TOKEN = os.environ.get("AUTH_TOKEN")
MCP_INTERNAL_WS = "ws://127.0.0.1:8086/mcp"

app = Flask(__name__)
sock = Sock(app)


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


@sock.route('/mcp')
def mcp_ws(ws):
    # Validate bearer token on initial headers if provided by client
    # Some clients send Authorization via query or subprotocols; we trust MCP server to enforce too.
    async def bridge():
        async with websockets.connect(
            MCP_INTERNAL_WS,
            extra_headers={"Authorization": f"Bearer {AUTH_TOKEN}"} if AUTH_TOKEN else None,
            max_size=None,
        ) as upstream:
            async def client_to_server():
                while True:
                    msg = ws.receive()
                    if msg is None:
                        await upstream.close()
                        break
                    await upstream.send(msg)

            async def server_to_client():
                async for message in upstream:
                    ws.send(message)

            await asyncio.gather(asyncio.to_thread(client_to_server), server_to_client())

    asyncio.run(bridge())


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
    # Flask-Sock uses gevent/werkzeug dev server for local; Render provides the reverse proxy.
    app.run(host="0.0.0.0", port=port)