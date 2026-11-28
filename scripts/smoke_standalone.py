"""Check that a standalone lokra build starts, serves MCP and parses SQL, with no database needed.

    python scripts/smoke_standalone.py path/to/lokra

The release workflow runs this against the build on every platform before publishing it.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

POLICY = """database: {host: 127.0.0.1, port: 1, dbname: shop}
agents:
  support-bot:
    read: {orders: "*"}
"""


async def serve(binary: str, config: str) -> None:
    params = StdioServerParameters(command=binary, args=["--config", config, "serve", "--dev-agent", "support-bot"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert {"list_tables", "query", "propose_write", "execute_write"} <= names, names
            result = await session.call_tool("query", {"sql": "DROP TABLE orders"})
            text = " ".join(getattr(part, "text", "") for part in result.content)
            assert "only SELECT" in text, text


def main() -> int:
    path = Path(sys.argv[1])
    if sys.platform == "win32" and path.suffix != ".exe":
        path = path.with_suffix(".exe")
    binary = str(path.resolve())
    print(subprocess.run([binary, "--version"], capture_output=True, text=True, check=True).stdout.strip())
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / "policies.yaml"
        config.write_text(POLICY, encoding="utf-8")
        asyncio.run(asyncio.wait_for(serve(binary, str(config)), 90))
    print("serves MCP over stdio and refuses a DROP without a database")
    return 0


if __name__ == "__main__":
    sys.exit(main())
