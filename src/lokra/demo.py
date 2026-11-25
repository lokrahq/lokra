"""`lokra demo`: watch Lokra handle an AI agent on a real Postgres in about a minute.

It starts a throwaway Postgres in Docker (or uses --dsn), loads a small fake SaaS database into
its own schema, runs two scripted agents through the same gateway the MCP server uses, and
removes everything it created at the end.
"""
from __future__ import annotations

import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psycopg
from psycopg import conninfo

from . import controls, tokens
from .config import load_config
from .gateway import Gateway, GatewayError, decide
from .ledger import Ledger
from .provision import AGENT_GROUP, provision
from .report import build_report

SCHEMA = "lokra_demo"
IMAGE = "postgres:16-alpine"

SEED = f"""
CREATE SCHEMA {SCHEMA};
CREATE TABLE {SCHEMA}.customers (
    id int PRIMARY KEY, account_id int NOT NULL, name text NOT NULL,
    email text NOT NULL, phone text NOT NULL, card text NOT NULL);
CREATE TABLE {SCHEMA}.orders (
    id int PRIMARY KEY, account_id int NOT NULL, customer_id int NOT NULL,
    total numeric(10,2) NOT NULL, status text NOT NULL);
CREATE TABLE {SCHEMA}.notes (
    id int PRIMARY KEY, account_id int NOT NULL, body text NOT NULL);
INSERT INTO {SCHEMA}.customers VALUES
    (1, 42, 'Ann Lee',    'ann@acme.example',   '+61 412 702 667', '4111 1111 1111 1111'),
    (2, 42, 'Ben Okafor', 'ben@acme.example',   '+1 415 555 0132', '5555 5555 5555 4444'),
    (3, 42, 'Cara Diaz',  'cara@acme.example',  '+44 20 7946 0958', '3782 822463 10005'),
    (4, 7,  'Dev Patel',  'dev@globex.example', '+61 423 555 019', '4012 8888 8888 1881'),
    (5, 7,  'Eve Novak',  'eve@globex.example', '+1 212 555 0188', '6011 1111 1111 1117'),
    (6, 13, 'Fay Wong',   'fay@initech.example', '+65 6123 4567', '3530 1113 3330 0000');
INSERT INTO {SCHEMA}.orders VALUES
    (101, 42, 1, 120.00, 'shipped'), (102, 42, 2, 89.50, 'late'), (103, 42, 2, 45.00, 'late'),
    (104, 42, 3, 300.00, 'delivered'), (105, 7, 4, 999.00, 'late'), (106, 13, 6, 15.00, 'late');
INSERT INTO {SCHEMA}.notes VALUES
    (1, 42, 'Ben called about two late orders. Asked us to refund to card 5555 5555 5555 4444, '
            'or email ben@acme.example. Call back on +1 415 555 0132.'),
    (2, 7,  'Globex wants a refund on order 105. Card 4012 8888 8888 1881.');
"""

POLICY = f"""
database: {{host: "{{host}}", port: {{port}}, dbname: "{{dbname}}"}}
agents:
  demo-support:
    description: Answers customer questions for account 42 only. Read only.
    rows: {{column: account_id, values: [42]}}
    read: {{{SCHEMA}.customers: "*", {SCHEMA}.orders: "*", {SCHEMA}.notes: "*"}}
    max_rows: 50
  demo-ops:
    description: Fixes order status for account 42. Writes wait for a person.
    rows: {{column: account_id, values: [42]}}
    read: {{{SCHEMA}.orders: "*"}}
    write: {{{SCHEMA}.orders: [update]}}
    rate_limit: 5
masking:
  columns: {{email: redact, card: last4, phone: last3}}
  detectors: [card, email, au_phone, phone]
"""


class DemoError(Exception):
    pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_postgres(say) -> tuple[str, str]:
    try:
        subprocess.run(["docker", "info"], capture_output=True, check=True, timeout=20)
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise DemoError("Docker isn't running. Start Docker, or point the demo at a Postgres where you can "
                        "create roles: lokra demo --dsn postgresql://postgres:PASSWORD@localhost:5432/postgres") from None
    port, name = _free_port(), f"lokra-demo-{int(time.time())}"
    say(f"Starting a throwaway Postgres in Docker ({IMAGE}; the first run downloads it)…")
    subprocess.run(["docker", "run", "-d", "--rm", "--name", name, "-e", "POSTGRES_PASSWORD=lokra-demo",
                    "-p", f"127.0.0.1:{port}:5432", IMAGE], capture_output=True, check=True, timeout=600)
    dsn = f"postgresql://postgres:lokra-demo@127.0.0.1:{port}/postgres"
    deadline = time.time() + 60
    while True:
        try:
            psycopg.connect(dsn, connect_timeout=2).close()
            return dsn, name
        except psycopg.OperationalError:
            if time.time() > deadline:
                raise DemoError("Postgres in Docker did not start within a minute") from None
            time.sleep(0.5)


def _cleanup(admin_dsn: str, roles: list[str], drop_group: bool) -> None:
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        for role in roles + ([AGENT_GROUP] if drop_group else []):
            if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
                conn.execute(f'DROP OWNED BY "{role}"')
                conn.execute(f'DROP ROLE "{role}"')


def _rows(result: dict, limit: int = 4) -> list[str]:
    return ["    " + " | ".join(str(v) for v in row) for row in result["rows"][:limit]]


def _story(cfg, say) -> None:
    key = cfg.signing_key()
    support = Gateway(cfg, tokens.mint(key, "demo-support", "you", 600))
    ops = Gateway(cfg, tokens.mint(key, "demo-ops", "you", 600))

    def attempt(gw: Gateway, sql: str) -> str:
        try:
            out = gw.query(sql)
            return f"returned {out['row_count']} row(s)"
        except GatewayError as e:
            return f"REFUSED: {e}"

    say("\n1. Lock rows. demo-support may only see account 42. It asks for every customer:")
    out = support.query(f"SELECT account_id, name, email, phone, card FROM {SCHEMA}.customers ORDER BY id")
    say(*_rows(out))
    say(f"   {out['row_count']} of 6 customers come back, all account 42. Postgres hid the rest, not a prompt.")

    say("\n2. Mask sensitive values, even in free text. It reads the support notes:")
    out = support.query(f"SELECT body FROM {SCHEMA}.notes")
    say(*_rows(out))
    say("   Card, email and phone are masked inside the note; Globex's note (account 7) never appears.")

    say("\n3. Shrug off injected instructions. A poisoned prompt makes the agent try:")
    for sql in (f"SELECT * FROM {SCHEMA}.customers WHERE account_id = 7",
                f"SELECT 1; DROP TABLE {SCHEMA}.orders",
                "SELECT pg_read_file('/etc/passwd')",
                f"UPDATE {SCHEMA}.orders SET total = 0"):
        say(f"   {sql[:58]:<58} -> {attempt(support, sql)}")

    say("\n4. Writes wait for a person. demo-ops wants to refund the late orders:")
    proposed = ops.propose_write(f"UPDATE {SCHEMA}.orders SET status = 'refunded' WHERE status = 'late'",
                                 "customer asked for refunds on late orders")
    say(f"   held for approval: {proposed['would_affect_rows']} row(s) would change "
        f"(only account 42's late orders; 105 and 106 belong to other accounts)")
    decide(cfg, proposed["write_id"], True, "you (demo)")
    done = ops.execute_write(proposed["write_id"])
    say(f"   you approve it, and it runs: {done['affected_rows']} row(s) updated")

    say("\n5. Brakes. demo-ops gets stuck in a retry loop (limit: 5 requests a minute):")
    results = [attempt(ops, f"SELECT count(*) FROM {SCHEMA}.orders") for _ in range(7)]
    say(f"   counting its earlier calls, {sum(r.startswith('returned') for r in results)} more ran, "
        f"then: {results[-1]}")
    controls.pause_agent(cfg, cfg.agent("demo-support"), "you (demo)", "incident review")
    say(f"   you pause demo-support:  {attempt(support, f'SELECT name FROM {SCHEMA}.customers')}")

    say("\n6. Every step is evidence. The signed ledger:")
    ledger = Ledger(cfg.ledger_path, key)
    ok = ledger.verify()[0]
    entries = ledger.entries()
    report = build_report(entries)
    flags = [f"{a.agent}: {d}" for a in report["agents"].values() for _, d in a.flags]
    say(f"   {len(entries)} entries, hash chain {'verified' if ok else 'BROKEN'}")
    for flag in flags:
        say(f"   flag  {flag}")


def run_demo(dsn: str | None = None, keep: bool = False, out=print) -> None:
    def say(*lines: str) -> None:
        for line in lines:
            out(line)

    say("Lokra demo: two AI agents meet a production-like Postgres.")
    container = None
    if dsn is None:
        dsn, container = _start_postgres(say)
    else:
        say(f"Using the database you gave; everything goes in schema {SCHEMA} and is removed at the end.")
    with psycopg.connect(dsn, autocommit=True) as conn:
        if conn.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (SCHEMA,)).fetchone():
            raise DemoError(f"schema {SCHEMA} already exists in that database; drop it first")
        had_group = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (AGENT_GROUP,)).fetchone()
        conn.execute(SEED)
    info = conninfo.conninfo_to_dict(dsn)
    roles: list[str] = []
    try:
        with tempfile.TemporaryDirectory() as tmp:
            policy = Path(tmp) / "policies.yaml"
            policy.write_text(POLICY.replace("{host}", info.get("host") or "127.0.0.1")
                              .replace("{port}", str(info.get("port") or 5432))
                              .replace("{dbname}", info.get("dbname") or "postgres"))
            cfg = load_config(policy, Path(tmp) / "home")
            roles = [a.role for a in cfg.agents.values()]
            provision(cfg, dsn)
            _story(cfg, say)
    finally:
        if container and not keep:
            subprocess.run(["docker", "stop", container], capture_output=True, timeout=60)
        elif not keep:
            _cleanup(dsn, roles, drop_group=not had_group)
    say("\nTry it on your own database:")
    say('  pipx install lokra && lokra init "postgresql://…" && lokra provision --admin-dsn "…"')
    if keep:
        say(f"\nKept the demo database: {dsn} (schema {SCHEMA})")
    elif container:
        say("\nThe throwaway database has been removed.")
    else:
        say(f"\nRemoved schema {SCHEMA} and the demo roles from that database.")


def main(dsn: str | None, keep: bool) -> int:
    try:
        run_demo(dsn, keep)
    except DemoError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0
