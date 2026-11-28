"""Validate a policy against the real database it will run on.

Catches the mistakes that make a policy wrong or unsafe: grants on tables or
columns that do not exist, sensitive columns an agent can read with nothing
masking them, writes without a matching read, and masking rules that never match.
"""
from __future__ import annotations

from .config import Config
from .introspect import _match, connect, relations, view_problems
from .provision import ensure_same_database


def check(cfg: Config, dsn: str) -> list[tuple[str, str]]:
    """Introspect the database (read-only) and validate the policy against it."""
    with connect(dsn) as conn:
        conn.read_only = True
        ensure_same_database(conn, cfg)
        colmap, views = relations(conn, sorted({s for a in cfg.agents.values() for s in a.schemas}))
    return analyze(cfg, colmap, views)


def analyze(cfg: Config, colmap: dict[str, set[str]],
            views: dict[str, str] | None = None) -> list[tuple[str, str]]:
    """Return a list of (level, message); level is 'error', 'warn' or 'info'."""
    views = views or {}
    all_columns = {c.lower() for cols in colmap.values() for c in cols}
    masked = set(cfg.masking_columns)           # already lowercased by load_config
    detectors = set(cfg.masking_detectors)
    findings: list[tuple[str, str]] = []

    for agent in cfg.agents.values():
        findings.extend(("error", p) for p in view_problems(agent, views))
        for table, cols in agent.read.items():
            if table not in colmap:
                findings.append(("error", f"{agent.name}: reads table '{table}' which does not exist"))
                continue
            if cols in ("*", ["*"]):
                wanted = colmap[table]
            else:
                wanted = set(cols)
                for c in sorted(wanted - colmap[table]):
                    findings.append(("error", f"{agent.name}: reads column '{table}.{c}' which does not exist"))
                wanted &= colmap[table]
            for c in sorted(wanted):
                m = _match(c)
                if not m or c.lower() in masked:
                    continue
                if m[1] and m[1] in detectors:
                    continue
                findings.append(("warn", f"{agent.name}: can read '{table}.{c}', which looks sensitive, "
                                         f"with no masking rule or detector"))
        if agent.rows:
            for table in sorted((set(agent.read) | set(agent.write)) & set(colmap) - set(views)):
                column = agent.rows.column_for(table)
                if column not in colmap[table]:
                    findings.append(("warn", f"{agent.name}: sees every row of '{table}', which has no "
                                             f"'{column}' column to lock rows by"))
        for table in agent.write:
            if table not in colmap:
                findings.append(("error", f"{agent.name}: writes table '{table}' which does not exist"))
            elif table not in agent.read:
                findings.append(("warn", f"{agent.name}: can write '{table}' but cannot read it"))

    for mc in sorted(masked):
        if mc not in all_columns:
            findings.append(("info", f"masking rule for column '{mc}' matches no column in the database"))

    return findings
