"""Read-only database introspection to scaffold a starter policy."""
from __future__ import annotations

import re

import psycopg
from psycopg import conninfo

from .config import table_key

# (keyword, masking strategy, value detector or None), most specific first.
_PII = [
    ("medicare", "last4", "medicare"),
    ("ihi", "last4", "ihi"),
    ("email", "redact", "email"),
    ("mobile", "last3", "phone"),
    ("phone", "last3", "phone"),
    ("fax", "last3", None),
    ("dob", "year_only", None),
    ("birth", "year_only", None),
    ("ssn", "last4", "us_ssn"),
    ("tfn", "last4", None),
    ("passport", "last4", None),
    ("licence", "last4", None),
    ("license", "last4", None),
    ("national", "last4", None),
    ("card", "redact", "card"),
    ("iban", "redact", "iban"),
    ("account", "redact", None),
]

_TABLES_QUERY = """
SELECT c.table_schema, c.table_name, c.column_name, c.data_type
FROM information_schema.columns c
JOIN information_schema.tables t
  ON t.table_schema = c.table_schema AND t.table_name = c.table_name
WHERE c.table_schema = ANY(%s) AND t.table_type = 'BASE TABLE'
ORDER BY c.table_schema, c.table_name, c.ordinal_position
"""


def _match(column: str):
    toks = set(re.split(r"[^a-z0-9]+", column.lower()))
    low = column.lower()
    for kw, strategy, detector in _PII:
        if kw in toks or (len(kw) >= 5 and kw in low):
            return strategy, detector
    return None


def introspect(dsn: str, schemas: list[str] | tuple[str, ...] = ("public",)) -> dict[str, list[tuple[str, str]]]:
    """Return {table: [(column, type)]} for base tables in the given schemas. Read-only.

    Tables outside public are keyed as 'schema.table', the same way policies name them.
    """
    with psycopg.connect(dsn, connect_timeout=10) as conn:
        conn.read_only = True
        rows = conn.execute(_TABLES_QUERY, (list(schemas),)).fetchall()
    tables: dict[str, list[tuple[str, str]]] = {}
    for schema, table, column, dtype in rows:
        tables.setdefault(table_key(schema, table), []).append((column, dtype))
    return tables


def build_policy(dsn: str, tables: dict[str, list[tuple[str, str]]]) -> dict:
    info = conninfo.conninfo_to_dict(dsn)
    columns: dict[str, str] = {}
    detectors: set[str] = set()
    for cols in tables.values():
        for column, _ in cols:
            m = _match(column)
            if m:
                columns[column.lower()] = m[0]
                if m[1]:
                    detectors.add(m[1])
    agent = {
        "description": "Read-only access scaffolded by lokra init. Review and tighten before provisioning.",
        "read": {table: "*" for table in tables},
        "max_rows": 500,
    }
    return {
        "database": {
            "host": info.get("host", "127.0.0.1"),
            "port": int(info.get("port", 5432)),
            "dbname": info.get("dbname") or info.get("user") or "postgres",
        },
        "agents": {"app-reader": agent},
        "masking": {"columns": columns, "detectors": sorted(detectors)},
        "approval_ttl_seconds": 3600,
    }
