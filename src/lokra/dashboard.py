"""A local web dashboard for the audit ledger and policy configuration.

`lokra dashboard` serves a single page on 127.0.0.1. It shows the same activity
and risk report as `lokra report`, refreshed live, and lets an operator edit the
policy (agents, grants, masking) and reprovision database roles from the browser.
It binds to the loopback address and talks to the local database only.
"""
from __future__ import annotations

import getpass
import http.server
import json
import re
import time
from urllib.parse import parse_qs, urlsplit

import psycopg
from psycopg import sql

from . import controls
from .config import Config, ConfigError, dump_policy, load_config
from .gateway import GatewayError, decide, list_pending
from .ledger import Ledger
from .masking import DETECTORS, Masker
from .provision import WRITE_OPS, provision
from .report import build_report, to_dict

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover
    yaml = None

STRATEGIES = ["none", "redact", "year_only", "hash", "last2", "last3", "last4"]
MAX_BODY = 256 * 1024
DECISION_PATH = re.compile(r"^/api/writes/(w_[0-9a-f]+)/(approve|deny)$")
AGENT_PATH = re.compile(r"^/api/agents/([A-Za-z0-9 _-]+)/(pause|resume)$")


def _detail(e: dict) -> str:
    ev = e.get("event")
    if ev == "read":
        return f"{e.get('rows', 0)} rows, {sum((e.get('masked') or {}).values())} masked"
    if ev in ("rejected",):
        return e.get("reason", "")
    if ev == "db_refused":
        return e.get("error", "")
    if ev == "write_proposed":
        return f"{e.get('would_affect_rows', 0)} rows would change"
    if ev == "write_executed":
        return f"{e.get('affected_rows', 0)} rows changed"
    if ev in ("write_approved", "write_denied"):
        return f"by {e.get('decided_by', '-')}"
    if ev in ("denied", "stopped"):
        return e.get("reason", "")
    if ev == "agent_paused":
        return f"by {e.get('decided_by', '-')}" + (f": {e['reason']}" if e.get("reason") else "")
    if ev == "agent_resumed":
        return f"by {e.get('decided_by', '-')}"
    if ev == "token_revoked":
        return f"{e.get('token_id', '')} by {e.get('decided_by', '-')}"
    if ev == "listed_tables":
        return ", ".join(e.get("tables") or [])
    return ""


def api_payload(cfg: Config) -> dict:
    led = Ledger(cfg.ledger_path, cfg.signing_key())
    chain_ok = led.verify()[0]
    entries = led.entries()
    recent = [{"ts": (e.get("ts") or "")[11:19], "seq": e.get("seq"),
               "event": e.get("event"), "agent": e.get("agent") or "—",
               "detail": _detail(e)} for e in entries[-40:]][::-1]
    return {"report": to_dict(build_report(entries), chain_ok), "recent": recent}


def activity_payload(cfg: Config, limit: int = 500) -> dict:
    led = Ledger(cfg.ledger_path, cfg.signing_key())
    chain_ok = led.verify()[0]
    entries = led.entries()
    sliced = entries[-limit:][::-1]
    rows = [{"ts": e.get("ts") or "", "seq": e.get("seq"), "event": e.get("event"),
             "agent": e.get("agent") or "—", "detail": _detail(e)} for e in sliced]
    return {"entries": rows, "total": len(entries), "shown": len(rows), "chain_verified": chain_ok}


def write_view(record: dict, masker: Masker, ttl: int) -> dict:
    status = record["status"]
    if status == "approved" and time.time() - record.get("approved_at", 0) > ttl:
        status = "expired"
    return {
        "id": record["id"], "status": status, "agent": record["agent"],
        "on_behalf_of": record.get("on_behalf_of"), "reason": record.get("reason") or "",
        "statement": record.get("statement"), "tables": record.get("tables") or [],
        "sql": masker.scrub_text(record["sql"]), "warnings": record.get("warnings") or [],
        "would_affect_rows": record.get("would_affect_rows"), "affected_rows": record.get("affected_rows"),
        "decided_by": record.get("approved_by") or record.get("denied_by"),
        "created_at": record.get("created_at"),
    }


def writes_payload(cfg: Config, recent: int = 10) -> dict:
    masker = Masker(cfg.masking_columns, cfg.masking_detectors)
    records = list_pending(cfg)
    pending = [write_view(r, masker, cfg.approval_ttl_seconds) for r in records if r["status"] == "pending"]
    decided = [r for r in records if r["status"] != "pending"]
    decided.sort(key=lambda r: max(r.get(k) or 0 for k in ("created_at", "approved_at", "denied_at", "executed_at")),
                 reverse=True)
    return {"pending": pending,
            "recent": [write_view(r, masker, cfg.approval_ttl_seconds) for r in decided[:recent]],
            "approval_ttl_seconds": cfg.approval_ttl_seconds}


def agents_payload(cfg: Config) -> dict:
    paused = controls.snapshot(cfg)["paused"]
    return {"agents": [{"name": a.name, "description": a.description, "role": a.role,
                        "rate_limit": a.rate_limit, "paused": paused.get(a.name)}
                       for a in sorted(cfg.agents.values(), key=lambda a: a.name)]}


def set_agent_paused(cfg: Config, admin_dsn: str, name: str, pause: bool, reason: str = "") -> dict:
    agent = cfg.agent(name)
    by = f"{getpass.getuser()} (dashboard)"
    if pause:
        detail = controls.pause_agent(cfg, agent, by, reason, admin_dsn)
    else:
        detail = controls.resume_agent(cfg, agent, by, admin_dsn)[1]
    return {"ok": True, "agent": name, "paused": pause, "detail": detail}


def decide_write(cfg: Config, write_id: str, approve: bool, by: str | None = None) -> dict:
    record = decide(cfg, write_id, approve, by or f"{getpass.getuser()} (dashboard)")
    return write_view(record, Masker(cfg.masking_columns, cfg.masking_detectors), cfg.approval_ttl_seconds)


def policy_payload(cfg: Config) -> dict:
    raw = yaml.safe_load(cfg.path.read_text()) or {}
    agents = []
    for name, a in (raw.get("agents") or {}).items():
        a = a or {}
        read = []
        for t, cols in (a.get("read") or {}).items():
            read.append({"table": t, "columns": "*" if cols in ("*", ["*"]) else list(cols)})
        write = [{"table": t, "ops": list(ops or [])} for t, ops in (a.get("write") or {}).items()]
        agents.append({
            "name": name,
            "description": a.get("description", ""),
            "rows": a.get("rows") or None,
            "read": read,
            "write": write,
            "max_rows": int(a.get("max_rows", 200)),
            "purposes": [str(p) for p in (a.get("purposes") or [])],
        })
    masking = raw.get("masking") or {}
    columns = [{"column": c, "strategy": s} for c, s in (masking.get("columns") or {}).items()]
    return {
        "database": {"host": cfg.db_host, "port": cfg.db_port, "dbname": cfg.db_name},
        "approval_ttl_seconds": int(raw.get("approval_ttl_seconds", cfg.approval_ttl_seconds)),
        "require_purpose": bool(raw.get("require_purpose", cfg.require_purpose)),
        "agents": agents,
        "masking": {"columns": columns, "detectors": list(masking.get("detectors") or [])},
        "options": {"write_ops": list(WRITE_OPS), "detectors": list(DETECTORS), "strategies": STRATEGIES},
    }


def _valid_strategy(s: str) -> bool:
    return s in ("none", "redact", "year_only", "hash") or (
        s.startswith("last") and s[4:].isdigit() and int(s[4:]) > 0)


def _rows_entry(agent: str, rows, errs: list[str]) -> dict | None:
    rows = rows or {}
    column = str(rows.get("column") or "").strip()
    values = rows.get("values") or []
    if isinstance(values, str):
        values = values.split(",")
    values = [v.strip() if isinstance(v, str) else v for v in values]
    values = [int(v) if isinstance(v, str) and v.lstrip("-").isdigit() else v for v in values if v not in ("", None)]
    if not column and not values:
        return None
    if not column or not values:
        errs.append(f"{agent}: locking rows needs both a column and at least one value")
        return None
    entry = {"column": column, "values": values}
    tables = {str(t).strip(): str(c).strip() for t, c in (rows.get("tables") or {}).items()
              if str(t).strip() and str(c).strip()}
    if tables:
        entry["tables"] = tables
    return entry


def _build_doc(cfg: Config, data: dict) -> dict:
    errs: list[str] = []
    agents: dict = {}
    seen: set[str] = set()
    for a in data.get("agents", []):
        name = (a.get("name") or "").strip()
        if not name:
            errs.append("every agent needs a name")
            continue
        if not all(ch.isalnum() or ch in " _-" for ch in name):
            errs.append(f"agent name has unexpected characters: {name!r}")
            continue
        if name in seen:
            errs.append(f"duplicate agent name: {name}")
            continue
        seen.add(name)
        read: dict = {}
        for row in a.get("read", []):
            t = (row.get("table") or "").strip()
            if not t:
                continue
            cols = row.get("columns")
            if isinstance(cols, str):
                cols = cols.strip()
                cols = "*" if cols in ("", "*") else [c.strip() for c in cols.split(",") if c.strip()]
            elif isinstance(cols, list):
                cols = [str(c).strip() for c in cols if str(c).strip()] or "*"
            else:
                cols = "*"
            read[t] = cols
        write: dict = {}
        for row in a.get("write", []):
            t = (row.get("table") or "").strip()
            if not t:
                continue
            ops = [o for o in (row.get("ops") or []) if o in WRITE_OPS]
            if ops:
                write[t] = ops
        rows = _rows_entry(name, a.get("rows"), errs)
        try:
            mr = int(a.get("max_rows", 200))
            if mr <= 0:
                raise ValueError
        except (TypeError, ValueError):
            errs.append(f"{name}: max rows must be a positive whole number")
            mr = 200
        purposes = [str(p).strip() for p in (a.get("purposes") or []) if str(p).strip()]
        entry = {"description": (a.get("description") or "").strip()}
        if rows:
            entry["rows"] = rows
        if read:
            entry["read"] = read
        if write:
            entry["write"] = write
        if purposes:
            entry["purposes"] = purposes
        entry["max_rows"] = mr
        agents[name] = entry
    if not agents:
        errs.append("define at least one agent")
    masking_in = data.get("masking") or {}
    columns: dict = {}
    for row in masking_in.get("columns", []):
        col = (row.get("column") or "").strip().lower()
        strat = (row.get("strategy") or "").strip()
        if not col:
            continue
        if not _valid_strategy(strat):
            errs.append(f"unknown masking strategy for {col}: {strat!r}")
            continue
        columns[col] = strat
    detectors = [d for d in (masking_in.get("detectors") or []) if d in DETECTORS]
    try:
        ttl = int(data.get("approval_ttl_seconds", cfg.approval_ttl_seconds))
        if ttl <= 0:
            raise ValueError
    except (TypeError, ValueError):
        errs.append("approval window must be a positive number of seconds")
        ttl = cfg.approval_ttl_seconds
    if errs:
        raise ValueError("; ".join(errs))
    doc = {"database": {"host": cfg.db_host, "port": cfg.db_port, "dbname": cfg.db_name}}
    if data.get("require_purpose"):
        doc["require_purpose"] = True
    doc["agents"] = agents
    doc["masking"] = {"columns": columns, "detectors": detectors}
    doc["approval_ttl_seconds"] = ttl
    kept = yaml.safe_load(cfg.path.read_text()) if cfg.path.exists() else {}
    if (kept or {}).get("slack"):
        doc["slack"] = kept["slack"]
    return doc


def _drop_removed_roles(new_cfg: Config, admin_dsn: str) -> list[str]:
    saved = new_cfg.db_secrets()
    current = {a.role for a in new_cfg.agents.values()}
    stale = [r for r in saved if r not in current]
    if not stale:
        return []
    out: list[str] = []
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        for role in stale:
            exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
            if exists:
                conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
                conn.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))
            out.append(f"removed role {role}")
    for role in stale:
        saved.pop(role, None)
    new_cfg.save_db_secrets(saved)
    return out


def apply_policy(cfg: Config, admin_dsn: str, data: dict) -> dict:
    doc = _build_doc(cfg, data)
    cfg.path.write_text(dump_policy(doc))
    new_cfg = load_config(cfg.path, cfg.home)
    dropped = _drop_removed_roles(new_cfg, admin_dsn)
    log = provision(new_cfg, admin_dsn)
    return {"ok": True, "log": log + dropped, "roles": sorted(a.role for a in new_cfg.agents.values())}


def make_handler(cfg: Config, admin_dsn: str):
    from importlib import resources
    html = resources.files("lokra").joinpath("dashboard.html").read_text()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):  # quiet
            pass

        def _send(self, body: bytes, content_type: str, status: int = 200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; font-src 'self'")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, status: int = 200):
            self._send(json.dumps(obj, default=str).encode(), "application/json", status)

        def _same_origin(self) -> bool:
            origin = self.headers.get("Origin")
            if origin is None:
                return True
            host = self.headers.get("Host") or ""
            return origin in (f"http://{host}", f"https://{host}")

        def do_GET(self):
            parsed = urlsplit(self.path)
            path = parsed.path
            if path == "/":
                self._send(html.encode(), "text/html; charset=utf-8")
            elif path == "/api/report":
                self._json(api_payload(cfg))
            elif path == "/api/activity":
                try:
                    limit = min(2000, max(1, int(parse_qs(parsed.query).get("limit", ["500"])[0])))
                except ValueError:
                    limit = 500
                self._json(activity_payload(cfg, limit))
            elif path == "/api/writes":
                self._json(writes_payload(cfg))
            elif path == "/api/agents":
                self._json(agents_payload(cfg))
            elif path == "/api/policy":
                try:
                    self._json(policy_payload(cfg))
                except Exception as e:  # noqa: BLE001
                    self._json({"error": str(e)}, 500)
            elif path.startswith("/assets/") and path.endswith(".woff2") and "/" not in path[8:]:
                try:
                    data = resources.files("lokra.assets").joinpath(path[8:]).read_bytes()
                    self._send(data, "font/woff2")
                except (FileNotFoundError, ModuleNotFoundError):
                    self._send(b"not found", "text/plain", 404)
            else:
                self._send(b"not found", "text/plain", 404)

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            decision = DECISION_PATH.match(path)
            agent_action = AGENT_PATH.match(path)
            if path != "/api/policy" and not decision and not agent_action:
                self._send(b"not found", "text/plain", 404)
                return
            if not self._same_origin():
                self._json({"ok": False, "error": "cross-origin request refused"}, 403)
                return
            if decision:
                try:
                    self._json({"ok": True, "write": decide_write(cfg, decision[1], decision[2] == "approve")})
                except GatewayError as e:
                    self._json({"ok": False, "error": str(e)}, 409)
                return
            if agent_action:
                length = min(int(self.headers.get("Content-Length") or 0), 4096)
                try:
                    reason = str((json.loads(self.rfile.read(length) or b"{}") or {}).get("reason") or "")[:300]
                except json.JSONDecodeError:
                    reason = ""
                try:
                    self._json(set_agent_paused(cfg, admin_dsn, agent_action[1], agent_action[2] == "pause", reason))
                except ConfigError as e:
                    self._json({"ok": False, "error": str(e)}, 404)
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self._json({"ok": False, "error": "request too large"}, 413)
                return
            try:
                data = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._json({"ok": False, "error": "invalid JSON"}, 400)
                return
            try:
                self._json(apply_policy(cfg, admin_dsn, data))
            except (ValueError, ConfigError) as e:
                self._json({"ok": False, "error": str(e)}, 400)
            except psycopg.Error as e:
                self._json({"ok": False, "error": f"database error: {e}"}, 502)

    return Handler


def serve(cfg: Config, port: int, admin_dsn: str) -> None:
    if yaml is None:  # pragma: no cover
        raise ConfigError("PyYAML is required for the dashboard")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), make_handler(cfg, admin_dsn))
    print(f"Lokra dashboard on http://127.0.0.1:{port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
