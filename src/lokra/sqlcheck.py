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
    return Checked("read", "select", _tables(tree), [])


def check_write(sql: str) -> Checked:
    tree = _single(sql)
    if not isinstance(tree, (exp.Insert, exp.Update, exp.Delete)):
        raise SqlRejected("propose_write accepts a single INSERT, UPDATE or DELETE")
    bad = _forbidden(tree)
    if bad:
        raise SqlRejected(f"{bad} is not allowed in a write")
    warnings = []
    if isinstance(tree, (exp.Update, exp.Delete)) and not tree.args.get("where"):
        warnings.append("no WHERE clause: this changes every row the agent can see")
    return Checked("write", type(tree).__name__.lower(), _tables(tree), warnings)
