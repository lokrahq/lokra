from __future__ import annotations

import logging
from dataclasses import dataclass

import sqlglot
from sqlglot import exp

logging.getLogger("sqlglot").setLevel(logging.ERROR)

WRITE_NODES = (exp.Insert, exp.Update, exp.Delete, exp.Merge)
FORBIDDEN_NODES = (
    exp.Create, exp.Drop, exp.Alter, exp.Command, exp.Set, exp.Transaction,
    exp.Commit, exp.Rollback, exp.Into, exp.Copy, exp.Grant, exp.Revoke,
    exp.TruncateTable, exp.Lock, exp.Use, exp.Pragma,
)
READ_ROOTS = (exp.Select, exp.SetOperation)

# Functions that read files, run programs, reach other systems, change the
# session, write sequences or stall the connection. Refused at the policy layer
# so an over-privileged agent role cannot reach them even by mistake; the
# database role and read-only transaction remain the backstop.
DANGEROUS_FUNCTIONS = {
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file",
    "pg_ls_logdir", "pg_ls_waldir", "lo_import", "lo_export",
    "dblink", "dblink_exec", "dblink_open", "dblink_send_query", "dblink_connect",
    "set_config", "set_role",
    "pg_sleep", "pg_sleep_for", "pg_sleep_until",
    "nextval", "setval",
    "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf",
    "pg_rotate_logfile", "pg_create_restore_point", "pg_switch_wal",
    "pg_read_server_files", "pg_execute_server_program",
}


class SqlRejected(Exception):
    pass


@dataclass
class Checked:
    kind: str
    statement: str
    tables: list[str]
    warnings: list[str]


def _single(sql: str) -> exp.Expression:
    if not sql or not sql.strip():
        raise SqlRejected("empty query")
    try:
        parsed = [p for p in sqlglot.parse(sql, read="postgres") if p is not None]
    except Exception as e:
        raise SqlRejected(f"could not parse SQL safely: {str(e).splitlines()[0][:200]}") from None
    if len(parsed) != 1:
        raise SqlRejected("send exactly one SQL statement per call")
    return parsed[0]


def _tables(tree: exp.Expression) -> list[str]:
    return sorted({t.name for t in tree.find_all(exp.Table) if t.name})


def _forbidden(tree: exp.Expression) -> str | None:
    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            return type(node).__name__.upper()
    return None


def _dangerous_function(tree: exp.Expression) -> str | None:
    for node in tree.find_all(exp.Anonymous):
        name = (node.this or "").lower()
        if name in DANGEROUS_FUNCTIONS:
            return name
    return None


def check_read(sql: str) -> Checked:
    tree = _single(sql)
    if isinstance(tree, WRITE_NODES):
        raise SqlRejected("this is a write; use propose_write so a human can approve it")
    if not isinstance(tree, READ_ROOTS):
        raise SqlRejected(f"only SELECT queries are allowed here (got {type(tree).__name__.upper()})")
    bad = _forbidden(tree)
    if bad:
        raise SqlRejected(f"{bad} is not allowed in a read query")
    if any(isinstance(n, WRITE_NODES) for n in tree.walk()):
        raise SqlRejected("data-modifying statements inside a SELECT (for example in a WITH clause) are not allowed")
    if tree.args.get("locks"):
        raise SqlRejected("row locks (FOR UPDATE / FOR SHARE) are not allowed in read queries")
    bad = _dangerous_function(tree)
    if bad:
        raise SqlRejected(f"the function {bad}() is not allowed")
    return Checked("read", "select", _tables(tree), [])


def check_write(sql: str) -> Checked:
    tree = _single(sql)
    if not isinstance(tree, (exp.Insert, exp.Update, exp.Delete)):
        raise SqlRejected("propose_write accepts a single INSERT, UPDATE or DELETE")
    bad = _forbidden(tree)
    if bad:
        raise SqlRejected(f"{bad} is not allowed in a write")
    bad = _dangerous_function(tree)
    if bad:
        raise SqlRejected(f"the function {bad}() is not allowed")
    warnings = []
    if isinstance(tree, (exp.Update, exp.Delete)) and not tree.args.get("where"):
        warnings.append("no WHERE clause: this changes every row the agent can see")
    return Checked("write", type(tree).__name__.lower(), _tables(tree), warnings)
