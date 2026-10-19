"""Turn the policy and audit ledger into a control evidence pack.

`lokra report` answers "what happened"; this answers "show me the controls and
the proof they ran", in the shape a security or privacy reviewer asks for.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .config import Config
from .report import build_report

# Each Lokra control, mapped to the frameworks an Australian health org answers
# to. Referenced by name/number only; see each framework for the authoritative
# text. Essential Eight is listed only where a mitigation genuinely applies.
CONTROLS = [
    {"control": "Least-privilege access per agent",
     "app": "APP 11", "iso": "A.8.2, A.8.3", "e8": "Restrict administrative privileges"},
    {"control": "Masking of sensitive values",
     "app": "APP 3, APP 11", "iso": "A.8.11 Data masking", "e8": "—"},
    {"control": "Human approval for changes",
     "app": "APP 11", "iso": "A.5.15", "e8": "—"},
    {"control": "Tamper-evident audit log",
     "app": "APP 1, APP 11", "iso": "A.8.15, A.8.16", "e8": "—"},
    {"control": "Blocked access attempts",
     "app": "APP 11", "iso": "A.5.24–A.5.26, A.8.16", "e8": "—"},
]

# Shown only when the policy handles Australian health identifiers (IHI / Medicare).
HEALTH_NOTES = [
    "**Healthcare Identifiers Act 2010 / My Health Records Act 2012** — the "
    "Individual Healthcare Identifier (IHI) and Medicare number are masked by "
    "validated value, in results and in free-text notes.",
    "**My Health Records Act 2012** — every access is written to a signed, "
    "verifiable audit log, so who-saw-what is reconstructable.",
    "**Notifiable Data Breaches scheme (Privacy Act 1988, Part IIIC)** — blocked "
    "attempts and risk flags give you the evidence to assess a suspected breach "
    "within the required timeframe.",
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
    purposes = sorted({e.get("purpose") for e in entries if e.get("purpose")})
    ev = rep["events_by_type"]
    blocked = sum(ev.get(k, 0) for k in ("rejected", "db_refused"))

    health_terms = {"ihi", "medicare"}
    health = bool(health_terms & set(cfg.masking_detectors)) or any(
        any(t in col for t in health_terms) for col in cfg.masking_columns)

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
        "health": health,
        "purposes": purposes,
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
        "Deployment: Lokra is self-hosted. The database, the audit ledger and the signing "
        "key stay within your infrastructure; Lokra sends data to no external service.",
        "",
        "## Summary",
        "",
        f"- Agents under policy: **{s['agents']}**",
        f"- Statements processed: **{s['reads']}** reads, **{s['blocked']}** blocked",
        f"- Changes: {s['writes_proposed']} proposed, {s['writes_approved']} approved, "
        f"{s['writes_denied']} denied, {s['writes_executed']} executed",
        f"- Sensitive identifiers masked: **{s['masked_total']}**",
    ]
    if ev["purposes"]:
        L.append(f"- Purposes declared on requests: {', '.join(ev['purposes'])}")
    L += [
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
        "Each control maps to the Australian Privacy Principles (Privacy Act 1988), "
        "ISO/IEC 27001:2022 Annex A, and, where it applies, the ACSC Essential Eight. "
        "Frameworks are referenced by name or control number; see each for its own text.",
        "",
        _table([[c["control"], c["app"], c["iso"], c["e8"]] for c in CONTROLS] +
               ([["Purpose-of-use enforcement", "APP 6", "A.5.15", "—"]] if ev["purposes"] else []),
               ["Lokra control", "Privacy Act (APP)", "ISO/IEC 27001:2022", "Essential Eight"]),
        "",
    ]
    if ev.get("health"):
        L.append("### Health-specific")
        L.append("")
        for note in HEALTH_NOTES:
            L.append(f"- {note}")
        L.append("")
    L += [
        "> This pack is evidence to support a privacy and security assessment, not a "
        "certification. Lokra addresses the controls listed above; the remaining parts of "
        "each framework (the other Essential Eight mitigations, the ISO clauses about your "
        "organisation, your hosting and backups) are your environment's responsibility.",
        "",
        "---",
        "",
        "Generated by Lokra. Evidence is derived from the signed audit ledger and the "
        "active policy; re-run `lokra verify-ledger` to confirm the log is intact.",
    ]
    return "\n".join(L)
