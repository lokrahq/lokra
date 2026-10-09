from __future__ import annotations

from dataclasses import dataclass, field

import psycopg
from psycopg import sql as _sql


@dataclass
class Outcome:
    refused: bool
    error: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list = field(default_factory=list)


class Adapter:
    name = "adapter"

    def read(self, sql: str) -> Outcome:
        raise NotImplementedError

    def close(self) -> None:
        pass


class LokraAdapter(Adapter):
    """Targets Lokra through its gateway, as the scheduling-bot agent (clinic 1)."""
    name = "Lokra"

    def __init__(self, config_path=None):
        from lokra.config import load_config
        from lokra.gateway import Gateway
        from lokra import tokens
        self.cfg = load_config(config_path)
        self.cfg.agent("scheduling-bot")
        token = tokens.mint(self.cfg.signing_key(), "scheduling-bot", "conformance", 300)
        self.gw = Gateway(self.cfg, token)

    def read(self, sql: str) -> Outcome:
        from lokra.gateway import GatewayError
        try:
            result = self.gw.query(sql)
        except GatewayError as e:
            return Outcome(refused=True, error=str(e))
        return Outcome(refused=False, columns=result["columns"], rows=result["rows"])


class NaiveProxyAdapter(Adapter):
    """The pattern behind the 2025-26 read-only bypass CVEs: one broad, shared
    database connection, with writes 'blocked' by inspecting the first SQL
    keyword. Point it at a privileged connection (as those servers were) to see
    how the keyword filter collapses."""
    name = "Naive proxy (keyword filter)"
    BLOCK = {"insert", "update", "delete", "drop", "alter", "create", "truncate", "grant", "revoke"}

    def __init__(self, dsn: str):
        self.conn = psycopg.connect(dsn, autocommit=True)

    def read(self, sql: str) -> Outcome:
        first = sql.lstrip().split(None, 1)[0].lower() if sql.strip() else ""
        if first in self.BLOCK:
            return Outcome(refused=True, error=f"blocked keyword: {first}")
        try:
            cur = self.conn.execute(sql)
            cols = [d.name for d in cur.description] if cur.description else []
            rows = cur.fetchall() if cur.description else []
            return Outcome(refused=False, columns=cols, rows=[list(r) for r in rows])
        except psycopg.Error as e:
            self.conn.rollback()
            msg = getattr(getattr(e, "diag", None), "message_primary", None) or str(e).splitlines()[0]
            return Outcome(refused=True, error=msg)

    def close(self) -> None:
        self.conn.close()



