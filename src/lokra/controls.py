"""Brakes for running agents: pause an agent, revoke one token, cap an agent's request rate.

State lives in controls.json next to the ledger and is read on every request, so a pause or a
revocation takes effect immediately for every Lokra process that shares the same home.
"""
from __future__ import annotations

import json
import time
from contextlib import contextmanager

from .config import AgentPolicy, Config
from .locks import hold

WINDOW_SECONDS = 60


@contextmanager
def _state(cfg: Config, write: bool):
    cfg.ensure_home()
    path = cfg.home / "controls.json"
    with hold(cfg.home / "controls.lock", shared=not write):
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        for key in ("paused", "revoked", "recent"):
            data.setdefault(key, {})
        yield data
        if write:
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            path.chmod(0o600)


def snapshot(cfg: Config) -> dict:
    with _state(cfg, write=False) as data:
        return {"paused": dict(data["paused"]), "revoked": dict(data["revoked"])}


def pause(cfg: Config, agent: str, by: str, reason: str = "") -> None:
    with _state(cfg, write=True) as data:
        data["paused"][agent] = {"by": by, "at": time.time(), "reason": reason}


def resume(cfg: Config, agent: str) -> bool:
    with _state(cfg, write=True) as data:
        return data["paused"].pop(agent, None) is not None


def revoke(cfg: Config, token_id: str, by: str) -> None:
    with _state(cfg, write=True) as data:
        data["revoked"][token_id] = {"by": by, "at": time.time()}


def check(cfg: Config, agent: AgentPolicy, token_id: str | None) -> tuple[str, str] | None:
    """Return (control, reason) when this request must be stopped, else None."""
    with _state(cfg, write=bool(agent.rate_limit)) as data:
        paused = data["paused"].get(agent.name)
        if paused:
            return "paused", f"{agent.name} is paused by {paused['by']}" + (
                f": {paused['reason']}" if paused.get("reason") else "")
        if token_id and token_id in data["revoked"]:
            return "revoked", "this token was revoked; ask for a new one"
        if agent.rate_limit:
            now = time.time()
            recent = [t for t in data["recent"].get(agent.name, []) if now - t < WINDOW_SECONDS]
            if len(recent) >= agent.rate_limit:
                data["recent"][agent.name] = recent
                return "rate_limit", (f"rate limit of {agent.rate_limit} requests a minute reached; "
                                      f"wait before retrying")
            data["recent"][agent.name] = recent + [now]
    return None


def pause_agent(cfg: Config, agent: AgentPolicy, by: str, reason: str = "", admin_dsn: str | None = None) -> str:
    """Pause in Lokra and log it; with an admin connection, also block the database login."""
    from .ledger import Ledger
    from .provision import set_login
    pause(cfg, agent.name, by, reason)
    Ledger(cfg.ledger_path, cfg.signing_key()).append(
        {"event": "agent_paused", "agent": agent.name, "decided_by": by, "reason": reason})
    if not admin_dsn:
        return "database login unchanged (no admin connection; pass --admin-dsn to change it too)"
    try:
        ended = set_login(cfg, admin_dsn, agent, allowed=False)
    except Exception as e:
        return f"could not disable the database login: {e}"
    return f"database login disabled, {ended} open session(s) ended"


def resume_agent(cfg: Config, agent: AgentPolicy, by: str, admin_dsn: str | None = None) -> tuple[bool, str]:
    from .ledger import Ledger
    from .provision import set_login
    was_paused = resume(cfg, agent.name)
    Ledger(cfg.ledger_path, cfg.signing_key()).append(
        {"event": "agent_resumed", "agent": agent.name, "decided_by": by})
    if not admin_dsn:
        return was_paused, "database login unchanged (no admin connection; pass --admin-dsn to change it too)"
    try:
        set_login(cfg, admin_dsn, agent, allowed=True)
    except Exception as e:
        return was_paused, f"could not re-enable the database login: {e}"
    return was_paused, "database login enabled"
