from __future__ import annotations

import os

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError

from .config import Config, load_config
from .gateway import Gateway, GatewayError

INSTRUCTIONS = (
    "You are connected to a database through Lokra, a policy proxy. "
    "Call list_tables first to see what you may read. Use query for one SELECT at a time. "
    "Rows outside your scope are invisible, and sensitive values are masked. "
    "To change data, call propose_write; a human must approve it before execute_write will run."
)


def bearer_token(ctx: Context) -> str | None:
    try:
        request = ctx.request_context.request
    except ValueError:
        return None
    if request is None:
        return None
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value.strip() or None


def build_server(cfg: Config, token: str | None = None, http: bool = False) -> MCPServer:
    """Over stdio every call uses `token`; over HTTP each request must carry its own bearer token."""
    server = MCPServer("lokra", instructions=INSTRUCTIONS)

    def call(ctx: Context, method: str, *args):
        gw = Gateway(cfg, bearer_token(ctx) if http else token)
        try:
            return getattr(gw, method)(*args)
        except GatewayError as e:
            raise ToolError(str(e)) from None

    @server.tool()
    def whoami(ctx: Context) -> dict:
        """Show which agent identity and human you are acting for, and when your access expires."""
        return call(ctx, "whoami")

    @server.tool()
    def list_tables(ctx: Context) -> dict:
        """List the tables and columns this agent may read or write."""
        return call(ctx, "list_tables")

    @server.tool()
    def query(sql: str, ctx: Context) -> dict:
        """Run ONE read-only SQL SELECT. Rows outside your scope are invisible and sensitive values are masked."""
        return call(ctx, "query", sql)

    @server.tool()
    def propose_write(sql: str, reason: str, ctx: Context) -> dict:
        """Propose ONE INSERT, UPDATE or DELETE. It is dry-run and queued for human approval, not executed."""
        return call(ctx, "propose_write", sql, reason)

    @server.tool()
    def write_status(write_id: str, ctx: Context) -> dict:
        """Check whether a proposed write is pending, approved, denied or executed."""
        return call(ctx, "write_status", write_id)

    @server.tool()
    def execute_write(write_id: str, ctx: Context) -> dict:
        """Execute a write that a human has approved."""
        return call(ctx, "execute_write", write_id)

    return server


def main() -> None:
    build_server(load_config(), os.environ.get("LOKRA_TOKEN")).run("stdio")


def main_http(host: str, port: int) -> None:
    build_server(load_config(), http=True).run("streamable-http", host=host, port=port,
                                               stateless_http=True, json_response=True)
