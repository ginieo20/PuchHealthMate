import asyncio
from typing import Annotated
import os
from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.server.auth.providers.bearer import BearerAuthProvider, RSAKeyPair
from mcp import ErrorData, McpError
from mcp.server.auth.provider import AccessToken
from mcp.types import TextContent, ImageContent, INVALID_PARAMS, INTERNAL_ERROR
from pydantic import BaseModel, Field, AnyUrl

import markdownify
import httpx
import readabilipy
from huggingface_hub import InferenceClient

# --- Load environment variables ---
load_dotenv()

TOKEN = os.environ.get("AUTH_TOKEN")
MY_NUMBER = os.environ.get("MY_NUMBER")
HF_TOKEN = os.environ.get("HF_TOKEN") or os.environ.get("HF_API_TOKEN")
HF_PROVIDER = os.environ.get("HF_PROVIDER")
MODEL_ID = os.environ.get("MODEL_ID")

assert TOKEN is not None, "Please set AUTH_TOKEN in your .env file"
assert MY_NUMBER is not None, "Please set MY_NUMBER in your .env file"

# --- Auth Provider ---
class SimpleBearerAuthProvider(BearerAuthProvider):
    def __init__(self, token: str):
        k = RSAKeyPair.generate()
        super().__init__(public_key=k.public_key, jwks_uri=None, issuer=None, audience=None)
        self.token = token

    async def load_access_token(self, token: str) -> AccessToken | None:
        if token == self.token:
            return AccessToken(
                token=token,
                client_id="puch-client",
                scopes=["*"],
                expires_at=None,
            )
        return None

# --- Rich Tool Description model ---
class RichToolDescription(BaseModel):
    description: str
    use_when: str
    side_effects: str | None = None

# --- Fetch Utility Class ---
class Fetch:
    USER_AGENT = "Puch/1.0 (Autonomous)"

    @classmethod
    async def fetch_url(
        cls,
        url: str,
        user_agent: str,
        force_raw: bool = False,
    ) -> tuple[str, str]:
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    url,
                    follow_redirects=True,
                    headers={"User-Agent": user_agent},
                    timeout=30,
                )
            except httpx.HTTPError as e:
                raise McpError(ErrorData(code=INTERNAL_ERROR, message=f"Failed to fetch {url}: {e!r}"))

            if response.status_code >= 400:
                raise McpError(ErrorData(code=INTERNAL_ERROR, message=f"Failed to fetch {url} - status code {response.status_code}"))

            page_raw = response.text

        content_type = response.headers.get("content-type", "")
        is_page_html = "text/html" in content_type

        if is_page_html and not force_raw:
            return cls.extract_content_from_html(page_raw), ""

        return (
            page_raw,
            f"Content type {content_type} cannot be simplified to markdown, but here is the raw content:\n",
        )

    @staticmethod
    def extract_content_from_html(html: str) -> str:
        """Extract and convert HTML content to Markdown format."""
        ret = readabilipy.simple_json.simple_json_from_html_string(html, use_readability=True)
        if not ret or not ret.get("content"):
            return "<error>Page failed to be simplified from HTML</error>"
        content = markdownify.markdownify(ret["content"], heading_style=markdownify.ATX)
        return content

    @staticmethod
    async def google_search_links(query: str, num_results: int = 5) -> list[str]:
        """
        Perform a scoped DuckDuckGo search and return a list of job posting URLs.
        (Using DuckDuckGo because Google blocks most programmatic scraping.)
        """
        ddg_url = f"https://html.duckduckgo.com/html/?q={query.replace(' ', '+')}"
        links = []

        async with httpx.AsyncClient() as client:
            resp = await client.get(ddg_url, headers={"User-Agent": Fetch.USER_AGENT})
            if resp.status_code != 200:
                return ["<error>Failed to perform search.</error>"]

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a", class_="result__a", href=True):
            href = a["href"]
            if "http" in href:
                links.append(href)
            if len(links) >= num_results:
                break

        return links or ["<error>No results found.</error>"]

# --- MCP Server Setup ---
mcp = FastMCP(
    "Job Finder MCP Server",
    auth=SimpleBearerAuthProvider(TOKEN),
)

# --- Tool: validate (required by Puch) ---
@mcp.tool
async def validate() -> str:
    return MY_NUMBER

# --- Tool: job_finder (now smart!) ---
JobFinderDescription = RichToolDescription(
    description="Smart job tool: analyze descriptions, fetch URLs, or search jobs based on free text.",
    use_when="Use this to evaluate job descriptions or search for jobs using freeform goals.",
    side_effects="Returns insights, fetched job descriptions, or relevant job links.",
)

@mcp.tool(description=JobFinderDescription.model_dump_json())
async def job_finder(
    user_goal: Annotated[str, Field(description="The user's goal (can be a description, intent, or freeform query)")],
    job_description: Annotated[str | None, Field(description="Full job description text, if available.")] = None,
    job_url: Annotated[AnyUrl | None, Field(description="A URL to fetch a job description from.")] = None,
    raw: Annotated[bool, Field(description="Return raw HTML content if True")] = False,
) -> str:
    """
    Handles multiple job discovery methods: direct description, URL fetch, or freeform search query.
    """
    if job_description:
        return (
            f"📝 **Job Description Analysis**\n\n"
            f"---\n{job_description.strip()}\n---\n\n"
            f"User Goal: **{user_goal}**\n\n"
            f"💡 Suggestions:\n- Tailor your resume.\n- Evaluate skill match.\n- Consider applying if relevant."
        )

    if job_url:
        content, _ = await Fetch.fetch_url(str(job_url), Fetch.USER_AGENT, force_raw=raw)
        return (
            f"🔗 **Fetched Job Posting from URL**: {job_url}\n\n"
            f"---\n{content.strip()}\n---\n\n"
            f"User Goal: **{user_goal}**"
        )

    if "look for" in user_goal.lower() or "find" in user_goal.lower():
        links = await Fetch.google_search_links(user_goal)
        return (
            f"🔍 **Search Results for**: _{user_goal}_\n\n" +
            "\n".join(f"- {link}" for link in links)
        )

    raise McpError(ErrorData(code=INVALID_PARAMS, message="Please provide either a job description, a job URL, or a search query in user_goal."))


# Image inputs and sending images

MAKE_IMG_BLACK_AND_WHITE_DESCRIPTION = RichToolDescription(
    description="Convert an image to black and white and save it.",
    use_when="Use this tool when the user provides an image URL and requests it to be converted to black and white.",
    side_effects="The image will be processed and saved in a black and white format.",
)

@mcp.tool(description=MAKE_IMG_BLACK_AND_WHITE_DESCRIPTION.model_dump_json())
async def make_img_black_and_white(
    puch_image_data: Annotated[str, Field(description="Base64-encoded image data to convert to black and white")] = None,
) -> list[TextContent | ImageContent]:
    import base64
    import io

    from PIL import Image

    try:
        image_bytes = base64.b64decode(puch_image_data)
        image = Image.open(io.BytesIO(image_bytes))

        bw_image = image.convert("L")

        buf = io.BytesIO()
        bw_image.save(buf, format="PNG")
        bw_bytes = buf.getvalue()
        bw_base64 = base64.b64encode(bw_bytes).decode("utf-8")

        return [ImageContent(type="image", mimeType="image/png", data=bw_base64)]
    except Exception as e:
        raise McpError(ErrorData(code=INTERNAL_ERROR, message=str(e)))

# --- Hugging Face Tools ---
HF_TEXT_DESCRIPTION = RichToolDescription(
    description="Generate text using a Hugging Face text-generation model via Inference API.",
    use_when="Use this to generate or continue text given a prompt using a HF-hosted model.",
)

@mcp.tool(description=HF_TEXT_DESCRIPTION.model_dump_json())
async def hf_text_generate(
    model: Annotated[str, Field(description="Hugging Face model id, e.g., 'meta-llama/Llama-3.2-3B-Instruct' or 'Qwen/Qwen2.5-7B-Instruct'")],
    prompt: Annotated[str, Field(description="The input prompt for generation")],
    max_new_tokens: Annotated[int | None, Field(description="Maximum new tokens to generate")] = 256,
    temperature: Annotated[float | None, Field(description="Sampling temperature (higher = more random)")] = 0.7,
    top_p: Annotated[float | None, Field(description="Top-p nucleus sampling cutoff")] = 0.95,
) -> str:
    try:
        client = InferenceClient(api_key=HF_TOKEN)
        # Run blocking HF call in a thread to avoid blocking the event loop
        generated_text = await asyncio.to_thread(
            client.text_generation,
            prompt,
            model=model,
            max_new_tokens=max_new_tokens,
            temperature=temperature,
            top_p=top_p,
            stream=False,
        )
        # client.text_generation may return a string or a dict depending on version; normalize to string
        if isinstance(generated_text, dict) and "generated_text" in generated_text:
            return str(generated_text["generated_text"])
        return str(generated_text)
    except Exception as e:
        raise McpError(ErrorData(code=INTERNAL_ERROR, message=f"Hugging Face text generation failed: {e}"))

HF_IMAGE_DESCRIPTION = RichToolDescription(
    description="Generate an image from text using a Hugging Face diffusion model via Inference API.",
    use_when="Use this when the user requests an image given a text prompt.",
    side_effects="Returns a generated image as base64-encoded PNG.",
)

@mcp.tool(description=HF_IMAGE_DESCRIPTION.model_dump_json())
async def hf_text_to_image(
    model: Annotated[str, Field(description="Hugging Face model id for text-to-image, e.g., 'stabilityai/stable-diffusion-xl-base-1.0' or 'black-forest-labs/FLUX.1-dev'")],
    prompt: Annotated[str, Field(description="The text description for the image")],
    guidance_scale: Annotated[float | None, Field(description="Classifier-free guidance scale (model-dependent)")] = 7.5,
    num_inference_steps: Annotated[int | None, Field(description="Number of denoising steps (model-dependent)")] = 28,
) -> list[TextContent | ImageContent]:
    import base64
    import io
    from PIL import Image

    try:
        client = InferenceClient(api_key=HF_TOKEN)
        image: Image.Image = await asyncio.to_thread(
            client.text_to_image,
            prompt,
            model=model,
            guidance_scale=guidance_scale,
            num_inference_steps=num_inference_steps,
        )
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        return [ImageContent(type="image", mimeType="image/png", data=img_b64)]
    except Exception as e:
        raise McpError(ErrorData(code=INTERNAL_ERROR, message=f"Hugging Face image generation failed: {e}"))

# Chat completion tool matching test.py style
HF_CHAT_DESCRIPTION = RichToolDescription(
    description="Chat completion via Hugging Face Inference API providers (e.g., fireworks-ai).",
    use_when="Use this to call chat models like deepseek-ai/DeepSeek-R1 or others with role-based messages.",
)

@mcp.tool(description=HF_CHAT_DESCRIPTION.model_dump_json())
async def hf_chat_completion(
    messages: Annotated[list[dict], Field(description="List of {'role','content'} chat messages")],
    model: Annotated[str | None, Field(description="Model id to use. Defaults to env MODEL_ID if not provided.")] = None,
    provider: Annotated[str | None, Field(description="HF provider (e.g., 'fireworks-ai'). Defaults to env HF_PROVIDER if not provided.")] = None,
    remove_think_tags: Annotated[bool, Field(description="Strip <think>...</think> tags from output if present")] = True,
) -> str:
    import re
    try:
        resolved_model = model or MODEL_ID
        if not resolved_model:
            raise ValueError("Model id not provided and MODEL_ID not set in environment")
        client = InferenceClient(provider=provider or HF_PROVIDER, api_key=HF_TOKEN)
        completion = await asyncio.to_thread(
            client.chat.completions.create,
            model=resolved_model,
            messages=messages,
        )
        content = completion.choices[0].message.content
        if remove_think_tags and isinstance(content, str):
            content = re.sub(r"<think>.*?</think>\n*", "", content, flags=re.DOTALL)
        return content.strip() if isinstance(content, str) else str(content)
    except Exception as e:
        raise McpError(ErrorData(code=INTERNAL_ERROR, message=f"Hugging Face chat completion failed: {e}"))

# --- Run MCP Server ---
async def main():
    print("🚀 Starting MCP server on http://0.0.0.0:8086")
    await mcp.run_async("streamable-http", host="0.0.0.0", port=8086)

if __name__ == "__main__":
    asyncio.run(main())
