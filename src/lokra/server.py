from __future__ import annotations

import os

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .config import load_config
from .gateway import Gateway, GatewayError

INSTRUCTIONS = (
    "You are connected to a clinic database through a policy proxy. "
    "Call list_tables first to see what you may read. Use query for one SELECT at a time. "
    "Rows outside your clinic scope are invisible, and identifiers such as Medicare numbers are masked. "
    "To change data, call propose_write; a human must approve it before execute_write will run."
)


def build_server(gw: Gateway) -> MCPServer:
    server = MCPServer("lokra", instructions=INSTRUCTIONS)

    def call(fn, *args):
        try:
            return fn(*args)
        except GatewayError as e:
            raise ToolError(str(e)) from None

    @server.tool()
    def whoami() -> dict:
        """Show which agent identity and human you are acting for, and when your access expires."""
        return call(gw.whoami)

    @server.tool()
    def list_tables() -> dict:
        """List the tables and columns this agent may read or write."""
        return call(gw.list_tables)

    @server.tool()
    def query(sql: str) -> dict:
        """Run ONE read-only SQL SELECT. Results are scoped to your clinics and sensitive values are masked."""
        return call(gw.query, sql)

    @server.tool()
    def propose_write(sql: str, reason: str) -> dict:
        """Propose ONE INSERT, UPDATE or DELETE. It is dry-run and queued for human approval, not executed."""
        return call(gw.propose_write, sql, reason)

    @server.tool()
    def write_status(write_id: str) -> dict:
        """Check whether a proposed write is pending, approved, denied or executed."""
        return call(gw.write_status, write_id)

    @server.tool()
    def execute_write(write_id: str) -> dict:
        """Execute a write that a human has approved."""
        return call(gw.execute_write, write_id)

    return server


def main() -> None:
    cfg = load_config()
    build_server(Gateway(cfg, os.environ.get("LOKRA_TOKEN"))).run("stdio")
