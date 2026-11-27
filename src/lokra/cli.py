from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time

from . import __version__, tokens
from .config import ConfigError, lab_admin_dsn, load_config
from .connect import CLIENTS
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
    schemas = [s.strip() for s in args.schemas.split(",") if s.strip()]
    tables = introspect(args.dsn, schemas)
    if not tables:
        print(f"no base tables found in schema(s): {', '.join(schemas)}", file=sys.stderr)
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
    print("  3. lokra connect          (adds the agent to Claude Code; --client cursor, vscode, ...)")
    return 0


def cmd_check(args):
    from .check import check
    cfg = _cfg(args)
    findings = check(cfg, _need_dsn(cfg, args.dsn, "--dsn"))
    errors = sum(1 for lvl, _ in findings if lvl == "error")
    warns = sum(1 for lvl, _ in findings if lvl == "warn")
    label = {"error": "ERROR", "warn": "WARN ", "info": "INFO "}
    for lvl, msg in findings:
        print(f"  {label[lvl]}  {msg}")
    if not findings:
        print("  OK     policy matches the database, nothing sensitive left unmasked")
    print(f"\n{errors} error(s), {warns} warning(s)")
    return 1 if errors else 0


def cmd_provision(args):
    from .provision import provision
    cfg = _cfg(args)
    for line in provision(cfg, _need_dsn(cfg, args.admin_dsn)):
        print(line)
    print(f"secrets saved to {cfg.secrets_path}")


def cmd_mint(args):
    cfg = _cfg(args)
    cfg.agent(args.agent)
    print(tokens.mint(cfg.signing_key(), args.agent, args.on_behalf_of or getpass.getuser(),
                      args.ttl, purpose=args.purpose))


def cmd_try(args):
    cfg = _cfg(args)
    token = tokens.mint(cfg.signing_key(), args.agent, getpass.getuser(), 300, purpose=args.purpose)
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
        if r.get("approvals_needed", 1) > 1 and r["status"] == "pending":
            done = [a["by"] for a in r.get("approvals") or []]
            print(f"    needs two people" + (f"; approved by {', '.join(done)}" if done else ""))
        if r.get("approved_by"):
            print(f"    approved by: {r['approved_by']}")
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
    record = decide(cfg, args.write_id, approve, by)
    if record["status"] == "pending":
        print(f"approved by {by}; it needs one more person to approve")
    else:
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
    cfg = _cfg(args)
    serve(cfg, args.port, _admin_dsn(cfg, args.admin_dsn))
    return 0


def cmd_serve(args):
    if args.config:
        os.environ["LOKRA_CONFIG"] = args.config
    if args.home:
        os.environ["LOKRA_HOME"] = args.home
    if args.http:
        if args.dev_agent:
            print("error: --dev-agent is for stdio; over HTTP each agent sends its own token "
                  "(lokra mint-token <agent>) as Authorization: Bearer", file=sys.stderr)
            return 2
        _cfg(args)
        if args.host not in ("127.0.0.1", "localhost", "::1"):
            print("lokra: listening beyond this machine; put TLS in front, since agent tokens travel "
                  "in the Authorization header", file=sys.stderr)
        from .server import main_http
        print(f"lokra: MCP over HTTP at http://{args.host}:{args.port}/mcp", file=sys.stderr)
        main_http(args.host, args.port)
        return
    if args.dev_agent and not os.environ.get("LOKRA_TOKEN"):
        cfg = _cfg(args)
        cfg.agent(args.dev_agent)
        os.environ["LOKRA_TOKEN"] = tokens.mint(cfg.signing_key(), args.dev_agent,
                                              args.on_behalf_of or getpass.getuser(), 8 * 3600,
                                              purpose=args.purpose)
        print(f"lokra: dev mode, issued an 8h token for {args.dev_agent}", file=sys.stderr)
    from .server import main
    main()


def _admin_dsn(cfg, given: str | None) -> str | None:
    return given or os.environ.get("LOKRA_ADMIN_DSN") or lab_admin_dsn(cfg)


def _need_dsn(cfg, given: str | None, flag: str = "--admin-dsn") -> str:
    dsn = _admin_dsn(cfg, given)
    if not dsn:
        raise ConfigError(f'pass {flag} "postgresql://…", an admin login to {cfg.db_name} on '
                          f"{cfg.db_host}:{cfg.db_port} (or set LOKRA_ADMIN_DSN)")
    return dsn


def cmd_pause(args):
    from . import controls
    cfg = _cfg(args)
    agent = cfg.agent(args.agent)
    db = controls.pause_agent(cfg, agent, getpass.getuser(), args.reason or "", _admin_dsn(cfg, args.admin_dsn))
    print(f"{agent.name} is paused: Lokra refuses its requests from now on; {db}")


def cmd_resume(args):
    from . import controls
    cfg = _cfg(args)
    agent = cfg.agent(args.agent)
    was_paused, db = controls.resume_agent(cfg, agent, getpass.getuser(), _admin_dsn(cfg, args.admin_dsn))
    print(f"{agent.name} is running again; {db}" if was_paused else f"{agent.name} was not paused; {db}")


def cmd_revoke(args):
    from . import controls
    from .ledger import Ledger
    cfg = _cfg(args)
    token_id = args.token
    if token_id.startswith("lokra1."):
        token_id = tokens.verify(cfg.signing_key(), token_id, now=0)["jti"]
    controls.revoke(cfg, token_id, getpass.getuser())
    Ledger(cfg.ledger_path, cfg.signing_key()).append(
        {"event": "token_revoked", "token_id": token_id, "decided_by": getpass.getuser()})
    print(f"token {token_id} revoked")


def cmd_cloud(args):
    from .cloud import run
    url = args.url or os.environ.get("LOKRA_CLOUD_URL") or "https://cloud.lokra.dev"
    run(_cfg(args), url, args.admin_dsn or os.environ.get("LOKRA_ADMIN_DSN"), once=args.once)


def cmd_connect(args):
    from . import connect
    from .term import Style
    cfg = _cfg(args)
    if args.agent:
        agent = cfg.agent(args.agent)
    elif len(cfg.agents) == 1:
        agent = next(iter(cfg.agents.values()))
    else:
        print(f"error: say which agent to connect, one of: {', '.join(cfg.agents) or '(none)'}",
              file=sys.stderr)
        return 2
    if cfg.require_purpose and not args.purpose:
        raise ConfigError("this policy requires a purpose-of-use; pass --purpose")
    if args.purpose and agent.purposes and args.purpose not in agent.purposes:
        raise ConfigError(f"purpose '{args.purpose}' is not permitted for {agent.name} "
                          f"(allowed: {', '.join(agent.purposes)})")
    target = connect.client(args.client)
    name = args.name or f"lokra-{agent.name}"
    server = connect.entry(cfg, agent, args.purpose, typed=target.typed)
    if args.print:
        print(json.dumps({target.key: {name: server}}, indent=2))
        return 0
    st = Style.detect()
    if not args.no_check:
        n = connect.readable_tables(cfg, agent)
        print("  " + st.ok(f"{agent.name} logs in to {cfg.db_name} and can read {n} table{'s' * (n != 1)}"))
    status = connect.install(target, name, server, args.force)
    where = f"{connect.shown(target.path)} for {target.label}"
    done = {"added": f"Added {st.bold(name)} to {where}",
            "updated": f"Updated {st.bold(name)} in {where}",
            "unchanged": f"{st.bold(name)} is already in {where}"}[status]
    print("  " + st.ok(done))
    print(f"\n  Next: {target.next_step.format(server=name)}")
    ask = st.dim('"What tables can you see?"')
    print(f"  Then ask it: {ask}")
    return 0


def cmd_demo(args):
    from .demo import main as demo
    return demo(args.dsn, args.keep)


def cmd_slack(args):
    from .slack import run
    run(_cfg(args), args.admin_dsn or os.environ.get("LOKRA_ADMIN_DSN"))


def main(argv=None):
    p = argparse.ArgumentParser(prog="lokra", description="Policy proxy between AI agents and your database")
    p.add_argument("--config", help="path to policies.yaml (default: ./policies.yaml or $LOKRA_CONFIG)")
    p.add_argument("--home", help="state directory (default: .lokra next to the config)")
    p.add_argument("-V", "--version", action="version", version=f"lokra {__version__}")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("init", help="introspect a database (read-only) and scaffold a starter policies.yaml")
    s.add_argument("dsn", help="connection string to the target database, e.g. a Supabase or local Postgres")
    s.add_argument("--out", help="where to write the policy (default: policies.yaml)")
    s.add_argument("--force", action="store_true", help="overwrite the file if it already exists")
    s.add_argument("--schemas", default="public", help="comma-separated schemas to include (default: public)")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("check", help="validate the policy against the database (read-only)")
    s.add_argument("--dsn", help="connection string to check against (default: $LOKRA_ADMIN_DSN)")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("provision", help="create or update one DB role per agent")
    s.add_argument("--admin-dsn", help="admin connection string (default: $LOKRA_ADMIN_DSN)")
    s.set_defaults(fn=cmd_provision)

    s = sub.add_parser("mint-token", help="issue a short-lived token for an agent")
    s.add_argument("agent")
    s.add_argument("--on-behalf-of", help="the human launching the agent (default: your username)")
    s.add_argument("--ttl", type=int, default=3600, help="seconds, max 8 hours")
    s.add_argument("--purpose", help="declared purpose-of-use, recorded on every request")
    s.set_defaults(fn=cmd_mint)

    s = sub.add_parser("try", help="run SQL as an agent from the terminal")
    s.add_argument("agent")
    s.add_argument("sql")
    s.add_argument("--write", action="store_true", help="propose a write instead of a read")
    s.add_argument("--reason")
    s.add_argument("--purpose", help="declared purpose-of-use for this request")
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
    s.add_argument("--admin-dsn", help="admin connection string, needed to apply policy changes (default: $LOKRA_ADMIN_DSN)")
    s.set_defaults(fn=cmd_dashboard)

    s = sub.add_parser("cloud", help="connect this Lokra to Lokra Cloud (token in $LOKRA_CLOUD_TOKEN)")
    s.add_argument("--url", help="Lokra Cloud address (default: $LOKRA_CLOUD_URL or https://cloud.lokra.dev)")
    s.add_argument("--admin-dsn", help="admin connection string, so pauses from the Cloud also block the "
                                       "database login (default: $LOKRA_ADMIN_DSN)")
    s.add_argument("--once", action="store_true", help="sync once and exit")
    s.set_defaults(fn=cmd_cloud)

    s = sub.add_parser("connect", help="add an agent to Claude Code, Claude Desktop, Cursor, VS Code or Windsurf")
    s.add_argument("agent", nargs="?", help="agent from the policy (optional when there is only one)")
    s.add_argument("--client", choices=CLIENTS, default="claude-code",
                   help="where to add it (default: claude-code, which writes .mcp.json in this folder)")
    s.add_argument("--name", help="server name in the client (default: lokra-<agent>)")
    s.add_argument("--purpose", help="declared purpose-of-use for the agent's session token")
    s.add_argument("--print", action="store_true", help="print the config entry instead of writing it")
    s.add_argument("--no-check", action="store_true", help="skip logging in as the agent to test it")
    s.add_argument("--force", action="store_true", help="replace a server of the same name that is not Lokra")
    s.set_defaults(fn=cmd_connect)

    s = sub.add_parser("demo", help="watch Lokra handle two AI agents on a throwaway Postgres (needs Docker or --dsn)")
    s.add_argument("--dsn", help="use this Postgres instead of Docker; the demo needs to create roles, and removes "
                                 "everything it creates")
    s.add_argument("--keep", action="store_true", help="leave the demo database in place afterwards")
    s.set_defaults(fn=cmd_demo)

    s = sub.add_parser("pause", help="stop an agent now: refuse its requests and block its database login")
    s.add_argument("agent")
    s.add_argument("--reason", help="recorded in the ledger and shown to the agent")
    s.add_argument("--admin-dsn", help="admin connection string (default: $LOKRA_ADMIN_DSN)")
    s.set_defaults(fn=cmd_pause)

    s = sub.add_parser("resume", help="let a paused agent run again")
    s.add_argument("agent")
    s.add_argument("--admin-dsn", help="admin connection string (default: $LOKRA_ADMIN_DSN)")
    s.set_defaults(fn=cmd_resume)

    s = sub.add_parser("revoke-token", help="revoke one token, by the token itself or its id")
    s.add_argument("token")
    s.set_defaults(fn=cmd_revoke)

    s = sub.add_parser("slack", help="post pending writes to Slack and take approvals there")
    s.add_argument("--admin-dsn", help="admin connection string, so pausing from Slack also blocks the "
                                       "database login (default: $LOKRA_ADMIN_DSN)")
    s.set_defaults(fn=cmd_slack)

    s = sub.add_parser("serve", help="run the MCP server, over stdio (default) or HTTP")
    s.add_argument("--http", action="store_true", help="serve MCP over HTTP for agents on other machines; "
                                                       "each request needs an Authorization: Bearer token")
    s.add_argument("--host", default="127.0.0.1", help="HTTP bind address (default: 127.0.0.1)")
    s.add_argument("--port", type=int, default=8787, help="HTTP port (default: 8787)")
    s.add_argument("--dev-agent", help="LOCAL DEV ONLY: issue an 8h token for this agent at startup "
                                       "if LOKRA_TOKEN is not set")
    s.add_argument("--on-behalf-of", help="human recorded in the audit log (default: your username)")
    s.add_argument("--purpose", help="declared purpose-of-use for the --dev-agent token")
    s.set_defaults(fn=cmd_serve)

    args = p.parse_args(argv)
    if args.cmd is None:
        from .term import Style, welcome
        print("\n".join(welcome(Style.detect())))
        return 0
    try:
        return args.fn(args) or 0
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        if str(e).startswith("config not found"):
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
