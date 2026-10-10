from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time

from . import tokens
from .config import ConfigError, LAB_ADMIN_DSN, load_config
from .gateway import Gateway, GatewayError, decide, list_pending
from .ledger import Ledger


def _cfg(args):
    return load_config(args.config, args.home)


def cmd_init(args):
    from pathlib import Path

    from .config import dump_policy
    from .introspect import build_policy, introspect

    out = Path(args.out or "policies.yaml")
    if out.exists() and not args.force:
        print(f"error: {out} already exists (use --out PATH or --force to overwrite)", file=sys.stderr)
        return 1
    tables = introspect(args.dsn)
    if not tables:
        print("no base tables found in schema 'public'", file=sys.stderr)
        return 1
    doc = build_policy(args.dsn, tables)
    out.write_text(dump_policy(doc))
    cols = doc["masking"]["columns"]
    n_cols = sum(len(c) for c in tables.values())
    print(f"introspected {len(tables)} table(s), {n_cols} column(s) (read-only)")
    print(f"flagged {len(cols)} column(s) to mask: {', '.join(sorted(cols)) or '(none)'}")
    print(f"wrote {out}")
    print("\nnext steps:")
    print("  1. review the policy and tighten the agent's read grants")
    print('  2. lokra provision --admin-dsn "<admin connection string>"')
    print("  3. lokra dashboard")
    return 0


def cmd_provision(args):
    from .provision import provision
    cfg = _cfg(args)
    dsn = args.admin_dsn or os.environ.get("LOKRA_ADMIN_DSN") or LAB_ADMIN_DSN
    for line in provision(cfg, dsn):
        print(line)
    print(f"secrets saved to {cfg.secrets_path}")


def cmd_mint(args):
    cfg = _cfg(args)
    cfg.agent(args.agent)
    print(tokens.mint(cfg.signing_key(), args.agent, args.on_behalf_of or getpass.getuser(), args.ttl))


def cmd_try(args):
    cfg = _cfg(args)
    token = tokens.mint(cfg.signing_key(), args.agent, getpass.getuser(), 300)
    gw = Gateway(cfg, token)
    try:
        if args.write:
            out = gw.propose_write(args.sql, args.reason or "manual test")
        else:
            out = gw.query(args.sql)
    except GatewayError as e:
        print(f"REFUSED: {e}")
        return 1
    print(json.dumps(out, indent=2, default=str))


def cmd_pending(args):
    cfg = _cfg(args)
    rows = [r for r in list_pending(cfg) if args.all or r["status"] == "pending"]
    if not rows:
        print("no pending writes")
    for r in rows:
        age = int(time.time() - r["created_at"])
        print(f"{r['id']}  [{r['status']}]  agent={r['agent']}  for={r.get('on_behalf_of')}  "
              f"rows={r['would_affect_rows']}  {age}s ago")
        print(f"    reason: {r['reason']}")
        print(f"    sql:    {r['sql']}")
        for w in r.get("warnings", []):
            print(f"    WARNING: {w}")


def _decide(args, approve: bool):
    cfg = _cfg(args)
    by = args.by or getpass.getuser()
    from .gateway import _load_pending
    r = _load_pending(cfg, args.write_id)
    print(f"agent:  {r['agent']} (for {r.get('on_behalf_of')})\nreason: {r['reason']}\nsql:    {r['sql']}\n"
          f"would affect {r['would_affect_rows']} row(s)")
    for w in r.get("warnings", []):
        print(f"WARNING: {w}")
    if approve and not args.yes:
        if input("approve? [y/N] ").strip().lower() != "y":
            print("not approved")
            return 1
    decide(cfg, args.write_id, approve, by)
    print(("approved" if approve else "denied") + f" by {by}")


def cmd_evidence(args):
    from . import evidence as ev_mod
    cfg = _cfg(args)
    led = Ledger(cfg.ledger_path, cfg.signing_key())
    ev = ev_mod.build(cfg, led.entries(), led.verify()[0])
    if args.json:
        print(json.dumps(ev, indent=2, default=str))
        return 0
    text = ev_mod.format_markdown(ev)
    if args.out:
        from pathlib import Path
        Path(args.out).write_text(text)
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


def cmd_verify(args):
    cfg = _cfg(args)
    ok, n, msg = Ledger(cfg.ledger_path, cfg.signing_key()).verify()
    print(msg)
    return 0 if ok else 2


def cmd_log(args):
    cfg = _cfg(args)
    for e in Ledger(cfg.ledger_path, cfg.signing_key()).entries()[-args.n:]:
        detail = e.get("sql") or e.get("reason") or e.get("write_id") or ""
        print(f"{e['seq']:>5} {e['ts'][:19]} {e.get('agent', '-'):<18} {e['event']:<15} {str(detail)[:90]}")


def cmd_report(args):
    import json as _json
    from .report import build_report, format_text, to_dict
    cfg = _cfg(args)
    led = Ledger(cfg.ledger_path, cfg.signing_key())
    chain_ok = led.verify()[0]
    entries = led.entries()
    if args.agent:
        entries = [e for e in entries if e.get("agent") == args.agent or e.get("event") == "denied"]
    report = build_report(entries)
    if args.json:
        print(_json.dumps(to_dict(report, chain_ok), indent=2, default=str))
    else:
        print(format_text(report, chain_ok))
    return 0 if chain_ok else 2


def cmd_dashboard(args):
    from .dashboard import serve
    dsn = args.admin_dsn or os.environ.get("LOKRA_ADMIN_DSN") or LAB_ADMIN_DSN
    serve(_cfg(args), args.port, dsn)
    return 0


def cmd_serve(args):
    if args.config:
        os.environ["LOKRA_CONFIG"] = args.config
    if args.home:
        os.environ["LOKRA_HOME"] = args.home
    if args.dev_agent and not os.environ.get("LOKRA_TOKEN"):
        cfg = _cfg(args)
        cfg.agent(args.dev_agent)
        os.environ["LOKRA_TOKEN"] = tokens.mint(cfg.signing_key(), args.dev_agent,
                                              args.on_behalf_of or getpass.getuser(), 8 * 3600)
        print(f"lokra: dev mode, issued an 8h token for {args.dev_agent}", file=sys.stderr)
    from .server import main
    main()


def main(argv=None):
    p = argparse.ArgumentParser(prog="lokra", description="Policy proxy between AI agents and your database")
    p.add_argument("--config", help="path to policies.yaml (default: ./policies.yaml or $LOKRA_CONFIG)")
    p.add_argument("--home", help="state directory (default: .lokra next to the config)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="introspect a database (read-only) and scaffold a starter policies.yaml")
    s.add_argument("dsn", help="connection string to the target database, e.g. a Supabase or local Postgres")
    s.add_argument("--out", help="where to write the policy (default: policies.yaml)")
    s.add_argument("--force", action="store_true", help="overwrite the file if it already exists")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("provision", help="create or update one DB role per agent")
    s.add_argument("--admin-dsn", help="admin connection string (default: lab database)")
    s.set_defaults(fn=cmd_provision)

    s = sub.add_parser("mint-token", help="issue a short-lived token for an agent")
    s.add_argument("agent")
    s.add_argument("--on-behalf-of", help="the human launching the agent (default: your username)")
    s.add_argument("--ttl", type=int, default=3600, help="seconds, max 8 hours")
    s.set_defaults(fn=cmd_mint)

    s = sub.add_parser("try", help="run SQL as an agent from the terminal")
    s.add_argument("agent")
    s.add_argument("sql")
    s.add_argument("--write", action="store_true", help="propose a write instead of a read")
    s.add_argument("--reason")
    s.set_defaults(fn=cmd_try)

    s = sub.add_parser("pending", help="list writes waiting for approval")
    s.add_argument("--all", action="store_true")
    s.set_defaults(fn=cmd_pending)

    for name, approve in (("approve", True), ("deny", False)):
        s = sub.add_parser(name, help=f"{name} a proposed write")
        s.add_argument("write_id")
        s.add_argument("--by", help="your name for the audit log")
        if approve:
            s.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
        s.set_defaults(fn=lambda a, approve=approve: _decide(a, approve))

    s = sub.add_parser("verify-ledger", help="check the audit ledger has not been tampered with")
    s.set_defaults(fn=cmd_verify)

    s = sub.add_parser("log", help="show recent audit entries")
    s.add_argument("-n", type=int, default=20)
    s.set_defaults(fn=cmd_log)

    s = sub.add_parser("report", help="activity and risk report from the audit ledger")
    s.add_argument("--agent", help="limit to one agent")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_report)

    s = sub.add_parser("evidence", help="generate a control evidence pack from the policy and ledger")
    s.add_argument("--out", help="write the Markdown pack to this file instead of stdout")
    s.add_argument("--json", action="store_true", help="emit structured JSON instead of Markdown")
    s.set_defaults(fn=cmd_evidence)

    s = sub.add_parser("dashboard", help="serve a local web dashboard of the ledger and policy")
    s.add_argument("--port", type=int, default=8900)
    s.add_argument("--admin-dsn", help="admin connection string used when applying policy (default: lab database)")
    s.set_defaults(fn=cmd_dashboard)

    s = sub.add_parser("serve", help="run the MCP server (used by Claude Code)")
    s.add_argument("--dev-agent", help="LOCAL DEV ONLY: issue an 8h token for this agent at startup "
                                       "if LOKRA_TOKEN is not set")
    s.add_argument("--on-behalf-of", help="human recorded in the audit log (default: your username)")
    s.set_defaults(fn=cmd_serve)

    args = p.parse_args(argv)
    try:
        return args.fn(args) or 0
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        print("hint: run this from your project folder (where policies.yaml lives), "
              "or pass --config /path/to/policies.yaml", file=sys.stderr)
        return 1
    except Exception as e:
        if os.environ.get("LOKRA_DEBUG"):
            raise
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
