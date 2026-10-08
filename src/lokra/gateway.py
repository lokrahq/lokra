from __future__ import annotations

import json
import secrets
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg

from . import sqlcheck, tokens
from .config import AgentPolicy, Config, ConfigError
from .ledger import Ledger
from .masking import Masker


# Agent SQL always runs with prepare=True: Postgres refuses to prepare more than
# one statement, so stacked queries fail even if sqlcheck misses them.


class GatewayError(Exception):
    pass


def _jsonable(v: Any) -> Any:
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (bytes, memoryview)):
        return "<binary>"
    return v


class Gateway:
    def __init__(self, cfg: Config, token: str | None):
        self.cfg = cfg
        self.key = cfg.signing_key()
        self.token = token
        self.ledger = Ledger(cfg.ledger_path, self.key)
        self.masker = Masker(cfg.masking_columns, cfg.masking_detectors)

    def _identity(self, tool: str) -> tuple[dict, AgentPolicy]:
        try:
            claims = tokens.verify(self.key, self.token)
            agent = self.cfg.agent(claims["agent"])
        except (tokens.TokenError, ConfigError) as e:
            self.ledger.append({"event": "denied", "tool": tool, "reason": str(e)})
            raise GatewayError(f"access denied: {e}") from None
        return claims, agent

    def _connect(self, agent: AgentPolicy, read_only: bool) -> psycopg.Connection:
        password = self.cfg.db_secrets().get(agent.role)
        if not password:
            raise GatewayError(f"agent '{agent.name}' is not provisioned; run `lokra provision`")
        conn = psycopg.connect(host=self.cfg.db_host, port=self.cfg.db_port, dbname=self.cfg.db_name,
                               user=agent.role, password=password, connect_timeout=5,
                               application_name=f"lokra:{agent.name}")
        conn.read_only = read_only
        return conn

    def _base(self, claims: dict, agent: AgentPolicy, tool: str) -> dict:
        return {"agent": agent.name, "role": agent.role, "on_behalf_of": claims.get("sub"),
                "token_id": claims.get("jti"), "tool": tool}

    def _db_error(self, base: dict, sql_text: str, e: psycopg.Error) -> GatewayError:
        msg = getattr(getattr(e, "diag", None), "message_primary", None) or str(e).splitlines()[0]
        self.ledger.append({**base, "event": "db_refused", "sql": self.masker.scrub_text(sql_text),
                            "error": msg})
        return GatewayError(f"database refused the query: {msg}")

    def whoami(self) -> dict:
        claims, agent = self._identity("whoami")
        return {"agent": agent.name, "description": agent.description, "on_behalf_of": claims.get("sub"),
                "expires_in_seconds": int(claims["exp"] - time.time()), "max_rows": agent.max_rows}

    def list_tables(self) -> dict:
        claims, agent = self._identity("list_tables")
        base = self._base(claims, agent, "list_tables")
        q = ("SELECT table_name, column_name, privilege_type FROM information_schema.column_privileges "
             "WHERE grantee = current_user AND table_schema = 'public' ORDER BY table_name, column_name")
        with self._connect(agent, read_only=True) as conn:
            rows = conn.execute(q).fetchall()
        tables: dict[str, dict[str, list[str]]] = {}
        for table, column, priv in rows:
            t = tables.setdefault(table, {"read": [], "write": []})
            bucket = "read" if priv == "SELECT" else "write"
            if column not in t[bucket]:
                t[bucket].append(column)
        self.ledger.append({**base, "event": "listed_tables", "tables": sorted(tables)})
        return {"tables": tables, "note": "Rows are limited to your clinic scope. Some values are masked."}

    def query(self, sql_text: str) -> dict:
        claims, agent = self._identity("query")
        base = self._base(claims, agent, "query")
        try:
            checked = sqlcheck.check_read(sql_text)
        except sqlcheck.SqlRejected as e:
            self.ledger.append({**base, "event": "rejected", "sql": self.masker.scrub_text(sql_text),
                                "reason": str(e)})
            raise GatewayError(str(e)) from None
        started = time.monotonic()
        try:
            with self._connect(agent, read_only=True) as conn:
                cur = conn.execute(sql_text, prepare=True)
                columns = [d.name for d in (cur.description or [])]
                rows = cur.fetchmany(agent.max_rows + 1)
                conn.rollback()
        except psycopg.Error as e:
            raise self._db_error(base, sql_text, e) from None
        truncated = len(rows) > agent.max_rows
        rows = rows[: agent.max_rows]
        masked_rows, masked = self.masker.mask_rows(columns, rows)
        result = {"columns": columns, "rows": [[_jsonable(v) for v in r] for r in masked_rows],
                  "row_count": len(rows), "truncated": truncated, "masked": masked}
        self.ledger.append({**base, "event": "read", "sql": self.masker.scrub_text(sql_text),
                            "tables": checked.tables, "rows": len(rows), "truncated": truncated,
                            "masked": masked, "ms": int((time.monotonic() - started) * 1000)})
        return result

    def propose_write(self, sql_text: str, reason: str) -> dict:
        claims, agent = self._identity("propose_write")
        base = self._base(claims, agent, "propose_write")
        try:
            checked = sqlcheck.check_write(sql_text)
        except sqlcheck.SqlRejected as e:
            self.ledger.append({**base, "event": "rejected", "sql": self.masker.scrub_text(sql_text),
                                "reason": str(e)})
            raise GatewayError(str(e)) from None
        try:
            with self._connect(agent, read_only=False) as conn:
                cur = conn.execute(sql_text, prepare=True)
                would_affect = cur.rowcount
                conn.rollback()
        except psycopg.Error as e:
            raise self._db_error(base, sql_text, e) from None
        write_id = "w_" + secrets.token_hex(4)
        record = {"id": write_id, "status": "pending", "agent": agent.name, "on_behalf_of": claims.get("sub"),
                  "sql": sql_text, "reason": reason, "statement": checked.statement, "tables": checked.tables,
                  "would_affect_rows": would_affect, "warnings": checked.warnings,
                  "created_at": time.time(), "token_id": claims.get("jti")}
        _save_pending(self.cfg, record)
        self.ledger.append({**base, "event": "write_proposed", "write_id": write_id,
                            "sql": self.masker.scrub_text(sql_text), "reason": reason,
                            "would_affect_rows": would_affect, "warnings": checked.warnings})
        return {"write_id": write_id, "status": "pending", "would_affect_rows": would_affect,
                "warnings": checked.warnings,
                "next_step": f"A human must approve this with `lokra approve {write_id}`. "
                             f"Then call execute_write with this write_id."}

    def write_status(self, write_id: str) -> dict:
        claims, agent = self._identity("write_status")
        record = _load_pending(self.cfg, write_id)
        if record["agent"] != agent.name:
            raise GatewayError("unknown write_id")
        return self._describe(record)

    def _describe(self, record: dict) -> dict:
        out = {k: record[k] for k in ("id", "status", "statement", "tables", "reason", "on_behalf_of",
                                      "approved_by", "denied_by", "would_affect_rows", "affected_rows",
                                      "warnings") if record.get(k) is not None}
        out["sql"] = self.masker.scrub_text(record["sql"])
        return out

    def execute_write(self, write_id: str) -> dict:
        claims, agent = self._identity("execute_write")
        base = self._base(claims, agent, "execute_write")
        record = _load_pending(self.cfg, write_id)
        if record["agent"] != agent.name:
            raise GatewayError("unknown write_id")
        if record["status"] != "approved":
            raise GatewayError(f"write {write_id} is '{record['status']}', not approved")
        if time.time() - record.get("approved_at", 0) > self.cfg.approval_ttl_seconds:
            record["status"] = "expired"
            _save_pending(self.cfg, record)
            raise GatewayError(f"approval for {write_id} expired; propose it again")
        sqlcheck.check_write(record["sql"])
        try:
            with self._connect(agent, read_only=False) as conn:
                cur = conn.execute(record["sql"], prepare=True)
                affected = cur.rowcount
                conn.commit()
        except psycopg.Error as e:
            record["status"] = "failed"
            _save_pending(self.cfg, record)
            raise self._db_error(base, record["sql"], e) from None
        record.update(status="executed", executed_at=time.time(), affected_rows=affected)
        _save_pending(self.cfg, record)
        self.ledger.append({**base, "event": "write_executed", "write_id": write_id,
                            "approved_by": record.get("approved_by"), "affected_rows": affected})
        return {"write_id": write_id, **self._describe(record)}


def _pending_path(cfg: Config, write_id: str) -> Path:
    if not write_id.startswith("w_") or not write_id[2:].isalnum():
        raise GatewayError("invalid write_id")
    return cfg.pending_dir / f"{write_id}.json"


def _save_pending(cfg: Config, record: dict) -> None:
    cfg.ensure_home()
    p = _pending_path(cfg, record["id"])
    p.write_text(json.dumps(record, indent=2))
    p.chmod(0o600)


def _load_pending(cfg: Config, write_id: str) -> dict:
    p = _pending_path(cfg, write_id)
    if not p.exists():
        raise GatewayError("unknown write_id")
    return json.loads(p.read_text())


def list_pending(cfg: Config) -> list[dict]:
    if not cfg.pending_dir.exists():
        return []
    records = [json.loads(p.read_text()) for p in cfg.pending_dir.glob("w_*.json")]
    return sorted(records, key=lambda r: r["created_at"])


def decide(cfg: Config, write_id: str, approve: bool, by: str) -> dict:
    record = _load_pending(cfg, write_id)
    if record["status"] != "pending":
        raise GatewayError(f"write {write_id} is already '{record['status']}'")
    record["status"] = "approved" if approve else "denied"
    record["approved_by" if approve else "denied_by"] = by
    record["approved_at" if approve else "denied_at"] = time.time()
    _save_pending(cfg, record)
    Ledger(cfg.ledger_path, cfg.signing_key()).append({
        "event": "write_approved" if approve else "write_denied", "write_id": write_id,
        "agent": record["agent"], "on_behalf_of": record.get("on_behalf_of"), "decided_by": by})
    return record
