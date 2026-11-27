"""Read-only database introspection to scaffold a starter policy."""
from __future__ import annotations

import re

import psycopg
from psycopg import conninfo

from .config import AgentPolicy, table_key

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
    ("bsb", "redact", None),
]
# Columns that are sensitive only as a pair of words: an account number is, an account id is not.
_PII_PAIRS = [
    ({"account", "number"}, "redact"),
    ({"account", "no"}, "redact"),
    ({"account", "num"}, "redact"),
    ({"acct", "number"}, "redact"),
    ({"acct", "no"}, "redact"),
]
# Free text can hold anything, so these run on every policy init writes.
BASE_DETECTORS = ("card", "email", "phone", "au_phone")

_TABLES_QUERY = """
SELECT c.table_schema, c.table_name, c.column_name, c.data_type
FROM information_schema.columns c
JOIN information_schema.tables t
  ON t.table_schema = c.table_schema AND t.table_name = c.table_name
WHERE c.table_schema = ANY(%s) AND t.table_type = 'BASE TABLE'
ORDER BY c.table_schema, c.table_name, c.ordinal_position
"""

_RELATIONS_QUERY = """
SELECT n.nspname, c.relname, c.relkind, coalesce(c.reloptions, '{}'), a.attname
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
WHERE n.nspname = ANY(%s) AND c.relkind IN ('r', 'p', 'f', 'v', 'm')
"""
_INVOKER = {f"security_invoker={v}" for v in ("true", "on", "yes", "1")}


def _words(column: str) -> set[str]:
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", column)
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", spaced)
    return set(re.split(r"[^a-z0-9]+", spaced.lower())) - {""}


def _match(column: str):
    toks = _words(column)
    low = column.lower()
    for kw, strategy, detector in _PII:
        if kw in toks or (len(kw) >= 5 and kw in low):
            return strategy, detector
    for pair, strategy in _PII_PAIRS:
        if pair <= toks:
            return strategy, None
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


def relations(conn: psycopg.Connection, schemas: list[str]) -> tuple[dict[str, set[str]], dict[str, str]]:
    """Columns of every table, view and materialized view in the schemas, and what each one that is not a
    table is: 'view' (runs with its owner's rights), 'invoker_view' (security_invoker) or 'materialized'."""
    columns: dict[str, set[str]] = {}
    kinds: dict[str, str] = {}
    for schema, name, relkind, options, column in conn.execute(_RELATIONS_QUERY, (list(schemas),)).fetchall():
        key = table_key(schema, name)
        cols = columns.setdefault(key, set())
        if column:
            cols.add(column)
        if relkind == "m":
            kinds[key] = "materialized"
        elif relkind == "v":
            kinds[key] = "invoker_view" if _INVOKER & {o.lower() for o in options} else "view"
    return columns, kinds


def view_problems(agent: AgentPolicy, kinds: dict[str, str]) -> list[str]:
    """A row lock is row-level security on tables, which a view respects only when it runs as the agent."""
    if not agent.rows:
        return []
    problems = []
    for table in agent.tables:
        if kinds.get(table) == "view":
            problems.append(f"{agent.name}: {table} is a view, which runs with its owner's rights, so the row lock "
                            f"would not apply through it; grant the tables it reads instead, or make it run as "
                            f"the agent (Postgres 15+): ALTER VIEW {table} SET (security_invoker = true)")
        elif kinds.get(table) == "materialized":
            problems.append(f"{agent.name}: {table} is a materialized view, which a row lock cannot reach; "
                            f"grant the tables it reads instead")
    return problems


def build_policy(dsn: str, tables: dict[str, list[tuple[str, str]]]) -> dict:
    info = conninfo.conninfo_to_dict(dsn)
    columns: dict[str, str] = {}
    detectors: set[str] = set(BASE_DETECTORS)
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
