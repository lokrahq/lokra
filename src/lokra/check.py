"""Validate a policy against the real database it will run on.

Catches the mistakes that make a policy wrong or unsafe: grants on tables or
columns that do not exist, sensitive columns an agent can read with nothing
masking them, writes without a matching read, and masking rules that never match.
"""
from __future__ import annotations

from .config import Config
from .introspect import _match, introspect


def check(cfg: Config, dsn: str) -> list[tuple[str, str]]:
    """Introspect the database (read-only) and validate the policy against it."""
    tables = introspect(dsn)
    return analyze(cfg, {t: {c for c, _ in cols} for t, cols in tables.items()})


def analyze(cfg: Config, colmap: dict[str, set[str]]) -> list[tuple[str, str]]:
    """Return a list of (level, message); level is 'error', 'warn' or 'info'."""
    all_columns = {c.lower() for cols in colmap.values() for c in cols}
    masked = set(cfg.masking_columns)           # already lowercased by load_config
    detectors = set(cfg.masking_detectors)
    findings: list[tuple[str, str]] = []

    for agent in cfg.agents.values():
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
        for table in agent.write:
            if table not in colmap:
                findings.append(("error", f"{agent.name}: writes table '{table}' which does not exist"))
            elif table not in agent.read:
                findings.append(("warn", f"{agent.name}: can write '{table}' but cannot read it"))

    for mc in sorted(masked):
        if mc not in all_columns:
            findings.append(("info", f"masking rule for column '{mc}' matches no column in the database"))

    return findings
