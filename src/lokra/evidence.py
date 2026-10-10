"""Turn the policy and audit ledger into a control evidence pack.

`lokra report` answers "what happened"; this answers "show me the controls and
the proof they ran", in the shape a security or privacy reviewer asks for.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .config import Config
from .report import build_report

# Lokra control -> (review theme, Australian Privacy Principles).
# Referenced by name only; see each framework for the authoritative text.
MAPPING = [
    ("Least-privilege access per agent", "Access control / identity", "APP 11"),
    ("Masking of sensitive values", "Data minimisation", "APP 3, APP 11"),
    ("Human approval for changes", "Change management", "APP 11"),
    ("Tamper-evident audit log", "Logging and monitoring", "APP 1, APP 11"),
    ("Blocked access attempts", "Threat detection", "APP 11"),
]


def build(cfg: Config, entries: list[dict], chain_ok: bool) -> dict:
    rep = build_report(entries)
    acts = rep["agents"]

    by_type: dict[str, int] = {}
    for a in acts.values():
        for k, v in a.masked.items():
            t = k.split(":")[-1]
            by_type[t] = by_type.get(t, 0) + v

    approvers = sorted({e.get("decided_by") for e in entries
                        if e.get("event") == "write_approved" and e.get("decided_by")})
    ev = rep["events_by_type"]
    blocked = sum(ev.get(k, 0) for k in ("rejected", "db_refused"))

    agents = []
    for name, a in sorted(cfg.agents.items()):
        agents.append({
            "name": name,
            "role": a.role,
            "row_scope": f"clinics {a.clinic_ids}" if a.clinic_ids else "all rows (no row scoping)",
            "reads": {t: ("all columns" if c in ("*", ["*"]) else ", ".join(c))
                      for t, c in a.read.items()},
            "writes": {t: ", ".join(ops) for t, ops in a.write.items()},
            "max_rows": a.max_rows,
        })

    return {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "database": cfg.db_name,
        "chain_verified": chain_ok,
        "entry_count": rep["entry_count"],
        "summary": {
            "agents": len(cfg.agents),
            "reads": ev.get("read", 0),
            "blocked": blocked,
            "writes_proposed": ev.get("write_proposed", 0),
            "writes_approved": ev.get("write_approved", 0),
            "writes_denied": ev.get("write_denied", 0),
            "writes_executed": ev.get("write_executed", 0),
            "masked_total": sum(by_type.values()),
        },
        "agents": agents,
        "masking": {"columns": cfg.masking_columns, "detectors": cfg.masking_detectors, "by_type": by_type},
        "change_control": {"approvers": approvers,
                           "proposed": ev.get("write_proposed", 0),
                           "approved": ev.get("write_approved", 0),
                           "denied": ev.get("write_denied", 0),
                           "executed": ev.get("write_executed", 0)},
        "access_denials": {"blocked": blocked,
                           "identity_denials": rep["identity_denials"],
                           "top_reasons": [{"reason": r, "count": c} for r, c in rep["blocked_reasons"]]},
    }


def _table(rows: list[list[str]], headers: list[str]) -> str:
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for r in rows:
        out.append("| " + " | ".join(r) + " |")
    return "\n".join(out)


def format_markdown(ev: dict) -> str:
    s = ev["summary"]
    chain = "verified" if ev["chain_verified"] else "BROKEN — see `lokra verify-ledger`"
    L = [
        f"# Lokra evidence pack — {ev['database']}",
        "",
        f"Generated {ev['generated']} from the signed audit ledger. "
        f"Covers {ev['entry_count']} entries. Audit chain: **{chain}**.",
        "",
        "## Summary",
        "",
        f"- Agents under policy: **{s['agents']}**",
        f"- Statements processed: **{s['reads']}** reads, **{s['blocked']}** blocked",
        f"- Changes: {s['writes_proposed']} proposed, {s['writes_approved']} approved, "
        f"{s['writes_denied']} denied, {s['writes_executed']} executed",
        f"- Sensitive identifiers masked: **{s['masked_total']}**",
        "",
        "## Controls",
        "",
        "### 1. Least-privilege access per agent",
        "",
        "Each agent authenticates as its own Postgres role with only the tables and "
        "columns its policy allows. Agents receive short-lived signed tokens, never a "
        "shared database password.",
        "",
        _table([[a["name"], f"`{a['role']}`",
                 "; ".join(f"{t} ({c})" for t, c in a["reads"].items()) or "none",
                 "; ".join(f"{t} ({o})" for t, o in a["writes"].items()) or "none",
                 a["row_scope"]] for a in ev["agents"]],
               ["Agent", "Database role", "Reads", "Writes", "Row scope"]),
        "",
        "### 2. Masking of sensitive values",
        "",
        "Configured identifiers are masked in query results and inside free-text fields, "
        "by validated value rather than column name, so an alias or a note cannot leak them.",
        "",
    ]
    cols = ev["masking"]["columns"]
    if cols:
        L.append(_table([[c, strat] for c, strat in sorted(cols.items())], ["Column", "Strategy"]))
    L.append("")
    L.append(f"Value detectors: {', '.join(ev['masking']['detectors']) or 'none'}.")
    bt = ev["masking"]["by_type"]
    if bt:
        L.append("")
        L.append("Masked in this period: " + ", ".join(f"{k} {v}" for k, v in sorted(bt.items())) + ".")
    cc = ev["change_control"]
    L += [
        "",
        "### 3. Human approval for changes",
        "",
        "INSERT, UPDATE and DELETE are dry-run, shown with the number of rows they would "
        "change, and run only after a person approves them.",
        "",
        f"This period: {cc['proposed']} proposed, {cc['approved']} approved, "
        f"{cc['denied']} denied, {cc['executed']} executed. "
        f"Approvers: {', '.join(cc['approvers']) or 'none recorded'}.",
        "",
        "### 4. Tamper-evident audit log",
        "",
        "Every request is written to a hash-chained, HMAC-signed log. Independent "
        f"verification of this ledger: **{chain}** across {ev['entry_count']} entries.",
        "",
        "### 5. Blocked access attempts",
        "",
        f"{ev['access_denials']['blocked']} statement(s) blocked; "
        f"{ev['access_denials']['identity_denials']} attempt(s) by an unknown or expired agent.",
        "",
    ]
    reasons = ev["access_denials"]["top_reasons"]
    if reasons:
        L.append(_table([[r["reason"], str(r["count"])] for r in reasons], ["Reason", "Count"]))
    L += [
        "",
        "## Framework mapping",
        "",
        "Each control above maps to the themes vendor security and AI questionnaires ask "
        "about (for example the US HSCC health-sector materials) and to the Australian "
        "Privacy Principles. Frameworks are referenced by name; see each for its own text.",
        "",
        _table([[c, theme, app] for c, theme, app in MAPPING],
               ["Lokra control", "Review theme", "Australian Privacy Principles"]),
        "",
        "---",
        "",
        "Generated by Lokra. Evidence is derived from the signed audit ledger and the "
        "active policy; re-run `lokra verify-ledger` to confirm the log is intact.",
    ]
    return "\n".join(L)
