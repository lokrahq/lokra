from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import psycopg

from lokra.config import LAB_ADMIN_DSN

from .adapters import LokraAdapter, NaiveProxyAdapter
from .probes import CANARY, PROBES


def reset_lab(admin_dsn: str) -> None:
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(f"DROP TABLE IF EXISTS {CANARY}")
        conn.execute(f"CREATE TABLE {CANARY} (id int)")
        conn.execute(f"INSERT INTO {CANARY} VALUES (1), (2), (3)")
        conn.execute("DROP TABLE IF EXISTS conformance_tmp")


def score(adapter, admin_dsn: str) -> list[dict]:
    results = []
    for probe in PROBES:
        reset_lab(admin_dsn)
        outcome = adapter.read(probe.payload)
        passed, detail = probe.check(outcome)
        results.append({"key": probe.key, "guarantee": probe.guarantee,
                        "cwe": probe.cwe, "passed": passed, "detail": detail})
    return results


def print_card(name: str, results: list[dict]) -> None:
    passed = sum(1 for r in results if r["passed"])
    print(f"\n  {name}: {passed}/{len(results)} guarantees upheld")
    for r in results:
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"    [{mark}] {r['key']:<20} {r['detail']}")


def markdown(all_results: dict[str, list[dict]]) -> str:
    names = list(all_results)
    lines = ["# Database access conformance", "",
             "Each row is a safety guarantee a scoped or read-only database access "
             "layer should uphold. A tick means the layer refused or neutralised the "
             "payload; a cross means the payload executed.", "",
             "| Guarantee | " + " | ".join(names) + " |",
             "|---|" + "|".join(["---"] * len(names)) + "|"]
    for i, probe in enumerate(PROBES):
        cells = []
        for n in names:
            r = all_results[n][i]
            cells.append("✅" if r["passed"] else "❌")
        g = all_results[names[0]][i]["guarantee"]
        cwe = all_results[names[0]][i]["cwe"]
        label = f"{g} ({cwe})" if cwe else g
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines.append("")
    for n in names:
        p = sum(1 for r in all_results[n] if r["passed"])
        lines.append(f"- **{n}**: {p}/{len(PROBES)} upheld")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="conformance",
                                 description="Score a database access layer against safety guarantees (lab only).")
    ap.add_argument("--admin-dsn", default=LAB_ADMIN_DSN, help="lab admin connection (default: local lab)")
    ap.add_argument("--json", action="store_true", help="print JSON instead of a scorecard")
    ap.add_argument("--markdown", metavar="PATH", help="also write a Markdown scorecard to PATH")
    args = ap.parse_args(argv)

    adapters = [LokraAdapter(), NaiveProxyAdapter(args.admin_dsn)]
    all_results = {}
    try:
        for adapter in adapters:
            all_results[adapter.name] = score(adapter, args.admin_dsn)
            adapter.close()
    finally:
        with psycopg.connect(args.admin_dsn, autocommit=True) as conn:
            conn.execute(f"DROP TABLE IF EXISTS {CANARY}")
            conn.execute("DROP TABLE IF EXISTS conformance_tmp")

    if args.json:
        print(json.dumps(all_results, indent=2))
    else:
        print("Database access conformance — lab run")
        for name, results in all_results.items():
            print_card(name, results)
    if args.markdown:
        Path(args.markdown).write_text(markdown(all_results))
        print(f"\nwrote {args.markdown}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
