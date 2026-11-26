"""Add a Lokra MCP server to an agent client's config, so an agent connects in one command."""
from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import psycopg

from .config import AgentPolicy, Config

CLIENTS = ("claude-code", "claude-desktop", "cursor", "vscode", "windsurf")

_READABLE_TABLES = """
SELECT count(*) FROM (
  SELECT DISTINCT table_schema, table_name FROM information_schema.column_privileges
  WHERE grantee = current_user AND privilege_type = 'SELECT'
    AND table_schema NOT IN ('pg_catalog', 'information_schema')
) t
"""


class ConnectError(Exception):
    pass


@dataclass(frozen=True)
class Client:
    label: str
    path: Path
    next_step: str
    key: str = "mcpServers"
    typed: bool = False


def _claude_desktop_config() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming") / "Claude/claude_desktop_config.json"
    return Path.home() / ".config/Claude/claude_desktop_config.json"


def client(name: str, cwd: Path | None = None) -> Client:
    cwd = cwd or Path.cwd()
    if name == "claude-code":
        return Client("Claude Code", cwd / ".mcp.json",
                      "Start Claude Code in this folder and allow {server} when it asks.")
    if name == "claude-desktop":
        return Client("Claude Desktop", _claude_desktop_config(), "Quit and reopen Claude Desktop.")
    if name == "cursor":
        return Client("Cursor", cwd / ".cursor" / "mcp.json",
                      "Open this folder in Cursor and switch {server} on in its MCP settings.")
    if name == "vscode":
        return Client("VS Code", cwd / ".vscode" / "mcp.json",
                      "Open this folder in VS Code; {server} appears in Copilot Chat's agent tools.",
                      key="servers", typed=True)
    if name == "windsurf":
        return Client("Windsurf", Path.home() / ".codeium/windsurf/mcp_config.json",
                      "Refresh the MCP servers in Windsurf.")
    raise ConnectError(f"unknown client '{name}', expected one of: {', '.join(CLIENTS)}")


def lokra_command() -> list[str]:
    """How a GUI app, which does not see your shell's PATH, should start this lokra."""
    script = Path(sys.argv[0])
    if script.name == "__main__.py":
        return [sys.executable, "-m", "lokra"]
    if script.stem == "lokra" and script.exists():
        return [str(script.absolute())]
    found = shutil.which("lokra")
    return [str(Path(found).absolute())] if found else [sys.executable, "-m", "lokra"]


def entry(cfg: Config, agent: AgentPolicy, purpose: str | None = None, typed: bool = False) -> dict:
    command, *args = lokra_command()
    args += ["--config", str(cfg.path)]
    if cfg.home != (cfg.path.parent / ".lokra").resolve():
        args += ["--home", str(cfg.home)]
    args += ["serve", "--dev-agent", agent.name]
    if purpose:
        args += ["--purpose", purpose]
    return {**({"type": "stdio"} if typed else {}), "command": command, "args": args}


def readable_tables(cfg: Config, agent: AgentPolicy) -> int:
    """Log in as the agent's own role and count the tables it can read."""
    password = cfg.db_secrets().get(agent.role)
    if not password:
        raise ConnectError(f"{agent.name} is not provisioned yet; run "
                           f'lokra provision --admin-dsn "<admin connection string>" first')
    try:
        with psycopg.connect(host=cfg.db_host, port=cfg.db_port, dbname=cfg.db_name, user=agent.role,
                             password=password, connect_timeout=5,
                             application_name=f"lokra:{agent.name}") as conn:
            conn.read_only = True
            return conn.execute(_READABLE_TABLES).fetchone()[0]
    except psycopg.Error as e:
        msg = getattr(getattr(e, "diag", None), "message_primary", None) or str(e).strip().splitlines()[0]
        raise ConnectError(f"{agent.name} could not log in to {cfg.db_name} at {cfg.db_host}:{cfg.db_port}: "
                           f"{msg} (--no-check writes the config anyway)") from None


def _is_lokra(server) -> bool:
    args = server.get("args") if isinstance(server, dict) else None
    return isinstance(args, list) and "serve" in args and "--config" in args


def install(target: Client, name: str, server: dict, force: bool = False) -> str:
    """Add or update one server in the client's JSON config, keeping everything else in it."""
    doc: dict = {}
    if target.path.exists() and target.path.read_text().strip():
        try:
            doc = json.loads(target.path.read_text())
        except json.JSONDecodeError as e:
            raise ConnectError(f"{target.path} is not plain JSON ({e.msg}, line {e.lineno}); add the entry "
                               f"by hand from `lokra connect --print`") from None
        if not isinstance(doc, dict):
            raise ConnectError(f"{target.path} should hold a JSON object")
    servers = doc.setdefault(target.key, {})
    if not isinstance(servers, dict):
        raise ConnectError(f'"{target.key}" in {target.path} should be a JSON object')
    current = servers.get(name)
    if current == server:
        return "unchanged"
    if current is not None and not (force or _is_lokra(current)):
        raise ConnectError(f"{target.path} already has a server called {name} that is not Lokra; "
                           f"choose another --name, or --force to replace it")
    servers[name] = server
    target.path.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.path.with_name(target.path.name + ".lokra-tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n")
    if target.path.exists():
        shutil.copymode(target.path, tmp)
    os.replace(tmp, target.path)
    return "added" if current is None else "updated"


def shown(path: Path) -> str:
    path = path.absolute()
    for base, label in ((Path.cwd(), ""), (Path.home(), "~/")):
        try:
            return label + str(path.relative_to(base))
        except ValueError:
            continue
    return str(path)
