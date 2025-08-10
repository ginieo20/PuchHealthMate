import asyncio
import threading
import os
from flask import Flask, request, jsonify, Response, stream_with_context
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


def run_mcp_server():
    asyncio.run(mcp_main())


@app.before_first_request
def start_mcp_in_thread():
    t = threading.Thread(target=run_mcp_server, daemon=True)
    t.start()


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

    if method == "GET":
        def generate():
            with httpx.stream("GET", target_url, headers=headers, params=request.args, timeout=None) as r:
                for chunk in r.iter_bytes():
                    if chunk:
                        yield chunk
        return Response(stream_with_context(generate()), status=200)

    elif method == "POST":
        data = request.get_data()
        def generate():
            with httpx.stream("POST", target_url, headers=headers, content=data, timeout=None) as r:
                for chunk in r.iter_bytes():
                    if chunk:
                        yield chunk
        return Response(stream_with_context(generate()), status=200)

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