from __future__ import annotations

import json
import re
import secrets
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg

from . import controls, sqlcheck, tokens
from .config import AgentPolicy, Config, ConfigError, table_key
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
        self.masked_columns = {c for c, rule in cfg.masking_columns.items() if rule != "none"}

    def _identity(self, tool: str) -> tuple[dict, AgentPolicy]:
        try:
            claims = tokens.verify(self.key, self.token)
            agent = self.cfg.agent(claims["agent"])
        except (tokens.TokenError, ConfigError) as e:
            self.ledger.append({"event": "denied", "tool": tool, "reason": str(e)})
            raise GatewayError(f"access denied: {e}") from None
        purpose = claims.get("purpose")
        if self.cfg.require_purpose and not purpose:
            reason = "a declared purpose-of-use is required for this request"
            self.ledger.append({"event": "denied", "tool": tool, "agent": agent.name, "reason": reason})
            raise GatewayError(f"access denied: {reason}")
        if purpose and agent.purposes and purpose not in agent.purposes:
            reason = f"purpose '{purpose}' is not permitted for {agent.name}"
            self.ledger.append({"event": "denied", "tool": tool, "agent": agent.name, "reason": reason})
            raise GatewayError(f"access denied: {reason}")
        stopped = controls.check(self.cfg, agent, claims.get("jti"))
        if stopped:
            control, reason = stopped
            self.ledger.append({"event": "stopped", "control": control, "tool": tool, "agent": agent.name,
                                "token_id": claims.get("jti"), "reason": reason})
            raise GatewayError(f"stopped: {reason}")
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
        base = {"agent": agent.name, "role": agent.role, "on_behalf_of": claims.get("sub"),
                "token_id": claims.get("jti"), "tool": tool}
        if claims.get("purpose"):
            base["purpose"] = claims["purpose"]
        return base

    def _db_error(self, base: dict, sql_text: str, e: psycopg.Error) -> GatewayError:
        msg = getattr(getattr(e, "diag", None), "message_primary", None) or str(e).splitlines()[0]
        self.ledger.append({**base, "event": "db_refused", "sql": self.masker.scrub_text(sql_text),
                            "error": msg})
        return GatewayError(f"database refused the query: {msg}")

    def whoami(self) -> dict:
        claims, agent = self._identity("whoami")
        return {"agent": agent.name, "description": agent.description, "on_behalf_of": claims.get("sub"),
                "purpose": claims.get("purpose"), "expires_in_seconds": int(claims["exp"] - time.time()),
                "max_rows": agent.max_rows}

    def list_tables(self) -> dict:
        claims, agent = self._identity("list_tables")
        base = self._base(claims, agent, "list_tables")
        q = ("SELECT table_schema, table_name, column_name, privilege_type FROM information_schema.column_privileges "
             "WHERE grantee = current_user AND table_schema NOT IN ('pg_catalog', 'information_schema') "
             "ORDER BY table_schema, table_name, column_name")
        with self._connect(agent, read_only=True) as conn:
            rows = conn.execute(q).fetchall()
        tables: dict[str, dict[str, list[str]]] = {}
        for schema, table, column, priv in rows:
            t = tables.setdefault(table_key(schema, table), {"read": [], "write": []})
            bucket = "read" if priv == "SELECT" else "write"
            if column not in t[bucket]:
                t[bucket].append(column)
        self.ledger.append({**base, "event": "listed_tables", "tables": sorted(tables)})
        note = "Some values are masked."
        if agent.rows:
            note = f"You only see {agent.rows.describe()}. " + note
        return {"tables": tables, "note": note}

    def query(self, sql_text: str) -> dict:
        claims, agent = self._identity("query")
        base = self._base(claims, agent, "query")
        try:
            checked = sqlcheck.check_read(sql_text, self.masked_columns)
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
        if would_affect == 0:
            checked.warnings.append("changes no rows: they may not exist, or be outside this agent's scope")
        write_id = "w_" + secrets.token_hex(4)
        rule = agent.approval
        needed = rule.needed(checked.statement)
        record = {"id": write_id, "status": "pending", "agent": agent.name, "on_behalf_of": claims.get("sub"),
                  "sql": sql_text, "reason": reason, "statement": checked.statement, "tables": checked.tables,
                  "would_affect_rows": would_affect, "warnings": checked.warnings, "approvals_needed": needed,
                  "approvals": [], "created_at": time.time(), "token_id": claims.get("jti")}
        auto = rule.automatic(checked.statement, would_affect)
        if auto:
            limit = f"{rule.auto_max_rows} row" + ("" if rule.auto_max_rows == 1 else "s")
            record.update(status="approved", approved_by=f"policy (up to {limit})", approved_at=time.time())
        _save_pending(self.cfg, record)
        self.ledger.append({**base, "event": "write_proposed", "write_id": write_id,
                            "sql": self.masker.scrub_text(sql_text), "reason": reason,
                            "would_affect_rows": would_affect, "warnings": checked.warnings})
        if auto:
            self.ledger.append({**base, "event": "write_approved", "write_id": write_id,
                                "decided_by": record["approved_by"]})
            next_step = (f"Approved by policy: it changes {would_affect} row(s), within this agent's limit of "
                         f"{rule.auto_max_rows}. Call execute_write with this write_id now.")
        elif needed > 1:
            next_step = (f"Two different people must approve this, for example with `lokra approve {write_id}`. "
                         f"Then call execute_write with this write_id.")
        else:
            next_step = (f"A human must approve this with `lokra approve {write_id}`. "
                         f"Then call execute_write with this write_id.")
        return {"write_id": write_id, "status": record["status"], "would_affect_rows": would_affect,
                "approvals_needed": 0 if auto else needed, "warnings": checked.warnings, "next_step": next_step}

    def write_status(self, write_id: str) -> dict:
        claims, agent = self._identity("write_status")
        record = _load_pending(self.cfg, write_id)
        if record["agent"] != agent.name:
            raise GatewayError("unknown write_id")
        if record["status"] == "approved" and time.time() - record.get("approved_at", 0) > self.cfg.approval_ttl_seconds:
            record = {**record, "status": "expired"}
        return self._describe(record)

    def _describe(self, record: dict) -> dict:
        out = {k: record[k] for k in ("id", "status", "statement", "tables", "reason", "on_behalf_of",
                                      "approved_by", "denied_by", "would_affect_rows", "affected_rows",
                                      "warnings", "approvals_needed", "failure") if record.get(k) is not None}
        if record.get("approvals"):
            out["approvals"] = [a["by"] for a in record["approvals"]]
        out["sql"] = self.masker.scrub_text(record["sql"])
        return out

    def execute_write(self, write_id: str) -> dict:
        claims, agent = self._identity("execute_write")
        base = self._base(claims, agent, "execute_write")
        record = _load_pending(self.cfg, write_id)
        if record["agent"] != agent.name:
            raise GatewayError("unknown write_id")
        refusal = None
        if record["status"] != "approved":
            refusal = f"write {write_id} is '{record['status']}', not approved"
        elif time.time() - record.get("approved_at", 0) > self.cfg.approval_ttl_seconds:
            record["status"] = "expired"
            _save_pending(self.cfg, record)
            refusal = f"approval for {write_id} expired; propose it again"
        if refusal:
            self.ledger.append({**base, "event": "write_refused", "write_id": write_id, "reason": refusal})
            raise GatewayError(refusal)
        sqlcheck.check_write(record["sql"])
        approved_rows = record.get("would_affect_rows")
        try:
            with self._connect(agent, read_only=False) as conn:
                cur = conn.execute(record["sql"], prepare=True)
                affected = cur.rowcount
                grew = approved_rows is not None and affected > approved_rows
                if grew:
                    conn.rollback()
                else:
                    conn.commit()
        except psycopg.Error as e:
            record["status"] = "failed"
            _save_pending(self.cfg, record)
            raise self._db_error(base, record["sql"], e) from None
        if grew:
            failure = (f"it would now change {affected} rows, more than the {approved_rows} that were approved, "
                       f"so nothing was changed")
            record.update(status="failed", failure=failure)
            _save_pending(self.cfg, record)
            self.ledger.append({**base, "event": "write_rolled_back", "write_id": write_id,
                                "approved_rows": approved_rows, "would_change": affected})
            raise GatewayError(f"{failure}. Propose it again for a fresh approval.")
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
    p.write_text(json.dumps(record, indent=2), encoding="utf-8")
    p.chmod(0o600)


def _load_pending(cfg: Config, write_id: str) -> dict:
    p = _pending_path(cfg, write_id)
    if not p.exists():
        raise GatewayError("unknown write_id")
    return json.loads(p.read_text(encoding="utf-8"))


def list_pending(cfg: Config) -> list[dict]:
    if not cfg.pending_dir.exists():
        return []
    records = [json.loads(p.read_text(encoding="utf-8")) for p in cfg.pending_dir.glob("w_*.json")]
    return sorted(records, key=lambda r: r["created_at"])


def _person(by: str) -> str:
    """The same person may approve from the terminal, Slack or Cloud; the channel does not make them two people."""
    return re.sub(r"\s*\((?:cloud|slack|dashboard)\)$", "", by).strip().lower()


def decide(cfg: Config, write_id: str, approve: bool, by: str) -> dict:
    """Record one person's decision. A write that needs two people stays pending after the first approval."""
    record = _load_pending(cfg, write_id)
    if record["status"] != "pending":
        raise GatewayError(f"write {write_id} is already '{record['status']}'")
    entry = {"write_id": write_id, "agent": record["agent"], "on_behalf_of": record.get("on_behalf_of"),
             "decided_by": by}
    if approve:
        approvals = record.setdefault("approvals", [])
        if any(_person(a["by"]) == _person(by) for a in approvals):
            raise GatewayError(f"{by} has already approved {write_id}; it needs a different person")
        approvals.append({"by": by, "at": time.time()})
        needed = record.get("approvals_needed", 1)
        if len(approvals) < needed:
            _save_pending(cfg, record)
            Ledger(cfg.ledger_path, cfg.signing_key()).append(
                {"event": "write_partly_approved", **entry, "approvals": len(approvals), "needed": needed})
            return record
        record.update(status="approved", approved_by=", ".join(a["by"] for a in approvals), approved_at=time.time())
    else:
        record.update(status="denied", denied_by=by, denied_at=time.time())
    _save_pending(cfg, record)
    Ledger(cfg.ledger_path, cfg.signing_key()).append(
        {"event": "write_approved" if approve else "write_denied", **entry})
    return record
