import asyncio
import os
from dotenv import load_dotenv

from fastmcp import FastMCP
from fastmcp.server.auth.providers.bearer import BearerAuthProvider, RSAKeyPair
from mcp.server.auth.provider import AccessToken

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


async def main():
    port = int(os.environ.get("PORT", "10000"))
    print(f"🚀 Starting MCP server '{SERVER_ID}' on http://0.0.0.0:{port}/mcp/")
    await mcp.run_async("streamable-http", host="0.0.0.0", port=port)


if __name__ == "__main__":
    asyncio.run(main())