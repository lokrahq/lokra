"""Turn the audit ledger into an activity and risk report.

The ledger already records every agent action. This reads it back and answers
two questions a security or compliance reviewer actually asks: what did each
agent do, and is any of it worth a second look. Risk flags are plain, explained
rules over the ledger, not a model, so every flag can be traced to its entries.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

# Thresholds for the risk rules. Deliberately simple and visible.
REFUSAL_FLAG = 3        # repeated blocked statements from one agent
BIG_READ_ROWS = 500     # a single read returning at least this many rows
PHI_FLAG = 50           # masked identifiers seen by one agent in the window
BIG_WRITE_ROWS = 100    # an approved/executed write touching at least this many rows

BLOCKED_EVENTS = {"rejected", "db_refused"}


@dataclass
class AgentActivity:
    agent: str
    on_behalf_of: set = field(default_factory=set)
    reads: int = 0
    rows_read: int = 0
    max_read_rows: int = 0
    masked: dict = field(default_factory=lambda: defaultdict(int))
    tables: set = field(default_factory=set)
    writes_proposed: int = 0
    writes_approved: int = 0
    writes_denied: int = 0
    writes_executed: int = 0
    rows_written: int = 0
    blocked: int = 0
    big_writes: int = 0
    unscoped_writes: int = 0
    flags: list = field(default_factory=list)

    @property
    def masked_total(self) -> int:
        return sum(self.masked.values())


def build_report(entries: list[dict]) -> dict:
    agents: dict[str, AgentActivity] = {}
    identity_denials = 0
    events_by_type: Counter = Counter()
    blocked_reasons: Counter = Counter()

    def act(name: str) -> AgentActivity:
        return agents.setdefault(name, AgentActivity(agent=name))

    for e in entries:
        event = e.get("event")
        events_by_type[event] += 1
        if event in BLOCKED_EVENTS:
            blocked_reasons[(e.get("reason") or e.get("error") or "blocked")[:80]] += 1
        name = e.get("agent")
        if event == "denied":
            identity_denials += 1
            continue
        if not name:
            continue
        a = act(name)
        if e.get("on_behalf_of"):
            a.on_behalf_of.add(e["on_behalf_of"])
        if event == "read":
            a.reads += 1
            rows = int(e.get("rows", 0) or 0)
            a.rows_read += rows
            a.max_read_rows = max(a.max_read_rows, rows)
            for k, v in (e.get("masked") or {}).items():
                a.masked[k] += int(v)
            a.tables.update(e.get("tables") or [])
        elif event == "listed_tables":
            a.tables.update(e.get("tables") or [])
        elif event in BLOCKED_EVENTS:
            a.blocked += 1
        elif event == "write_proposed":
            a.writes_proposed += 1
            if int(e.get("would_affect_rows", 0) or 0) >= BIG_WRITE_ROWS:
                a.big_writes += 1
            if e.get("warnings"):
                a.unscoped_writes += 1
        elif event == "write_approved":
            a.writes_approved += 1
        elif event == "write_denied":
            a.writes_denied += 1
        elif event == "write_executed":
            a.writes_executed += 1
            a.rows_written += int(e.get("affected_rows", 0) or 0)

    for a in agents.values():
        if a.blocked >= REFUSAL_FLAG:
            a.flags.append(("probing", f"{a.blocked} blocked statements, a sign of probing or prompt injection"))
        if a.max_read_rows >= BIG_READ_ROWS:
            a.flags.append(("broad_read", f"a single read returned {a.max_read_rows} rows"))
        if a.masked_total >= PHI_FLAG:
            a.flags.append(("phi_volume", f"{a.masked_total} sensitive identifiers accessed (masked)"))
        if a.big_writes:
            a.flags.append(("large_write", f"{a.big_writes} write(s) proposed touching {BIG_WRITE_ROWS}+ rows"))
        if a.unscoped_writes:
            a.flags.append(("unscoped_write", f"{a.unscoped_writes} write(s) proposed with no WHERE clause"))

    total_flags = sum(len(a.flags) for a in agents.values())
    return {"agents": agents, "identity_denials": identity_denials,
            "entry_count": len(entries), "total_flags": total_flags,
            "events_by_type": dict(events_by_type),
            "blocked_reasons": blocked_reasons.most_common(6)}


def _mask_detail(masked: dict) -> str:
    parts = [f"{k.split(':')[-1]} {v}" for k, v in sorted(masked.items())]
    return f" ({', '.join(parts)})" if parts else ""


def format_text(report: dict, chain_ok: bool) -> str:
    lines = [f"Lokra activity report — {report['entry_count']} ledger entries, "
             f"chain {'verified' if chain_ok else 'BROKEN — see verify-ledger'}"]
    if report["identity_denials"]:
        lines.append(f"  {report['identity_denials']} access attempt(s) by an unknown or expired agent")
    lines.append("")
    for a in sorted(report["agents"].values(), key=lambda x: x.agent):
        who = ", ".join(sorted(a.on_behalf_of)) or "-"
        lines.append(f"{a.agent} (for {who})")
        lines.append(f"  reads {a.reads} · rows {a.rows_read} · masked {a.masked_total}{_mask_detail(a.masked)}")
        lines.append(f"  writes {a.writes_proposed} proposed, {a.writes_approved} approved, "
                     f"{a.writes_denied} denied, {a.writes_executed} executed ({a.rows_written} rows)")
        lines.append(f"  blocked {a.blocked} · tables {', '.join(sorted(a.tables)) or '-'}")
        for _, msg in a.flags:
            lines.append(f"  ⚠ {msg}")
        lines.append("")
    summary = f"{len(report['agents'])} agent(s) · {report['total_flags']} risk flag(s)"
    lines.append(summary)
    return "\n".join(lines)


def to_dict(report: dict, chain_ok: bool) -> dict:
    return {
        "chain_verified": chain_ok,
        "entry_count": report["entry_count"],
        "identity_denials": report["identity_denials"],
        "risk_flags": report["total_flags"],
        "events_by_type": report["events_by_type"],
        "blocked_reasons": [{"reason": r, "count": c} for r, c in report["blocked_reasons"]],
        "agents": [
            {"agent": a.agent, "on_behalf_of": sorted(a.on_behalf_of),
             "reads": a.reads, "rows_read": a.rows_read, "max_read_rows": a.max_read_rows,
             "masked": dict(a.masked), "masked_total": a.masked_total, "tables": sorted(a.tables),
             "writes_proposed": a.writes_proposed, "writes_approved": a.writes_approved,
             "writes_denied": a.writes_denied, "writes_executed": a.writes_executed,
             "rows_written": a.rows_written, "blocked": a.blocked,
             "flags": [{"kind": k, "detail": d} for k, d in a.flags]}
            for a in sorted(report["agents"].values(), key=lambda x: x.agent)
        ],
    }
