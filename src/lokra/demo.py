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
import textwrap
import time
from pathlib import Path

import psycopg
from psycopg import conninfo

from . import controls, tokens
from .config import load_config
from .gateway import Gateway, GatewayError, decide
from .ledger import Ledger
from .provision import AGENT_GROUP, provision
from .report import BLOCKED_EVENTS, build_report
from .term import Style

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


def _start_postgres(say, st: Style) -> tuple[str, str]:
    try:
        subprocess.run(["docker", "info"], capture_output=True, check=True, timeout=20)
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise DemoError("Docker isn't running. Start Docker, or point the demo at a Postgres where you can "
                        "create roles: lokra demo --dsn postgresql://postgres:PASSWORD@localhost:5432/postgres") from None
    port, name = _free_port(), f"lokra-demo-{int(time.time())}"
    dsn = f"postgresql://postgres:lokra-demo@127.0.0.1:{port}/postgres"
    with st.spinner(f"Starting a throwaway Postgres in Docker ({IMAGE}; the first run downloads it)", say):
        subprocess.run(["docker", "run", "-d", "--rm", "--name", name, "-e", "POSTGRES_PASSWORD=lokra-demo",
                        "-p", f"127.0.0.1:{port}:5432", IMAGE], capture_output=True, check=True, timeout=600)
        deadline = time.time() + 60
        while True:
            try:
                psycopg.connect(dsn, connect_timeout=2).close()
                break
            except psycopg.OperationalError:
                if time.time() > deadline:
                    raise DemoError("Postgres in Docker did not start within a minute") from None
                time.sleep(0.5)
    say("  " + st.ok(f"Postgres is running in Docker ({IMAGE})"))
    return dsn, name


def _cleanup(admin_dsn: str, roles: list[str], drop_group: bool) -> None:
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        for role in roles + ([AGENT_GROUP] if drop_group else []):
            if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
                conn.execute(f'DROP OWNED BY "{role}"')
                conn.execute(f'DROP ROLE "{role}"')


def _shown(sql: str) -> str:
    return sql.replace(f"{SCHEMA}.", "")


def _n(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _glyph(st: Style, event: str) -> str:
    if event in ("read", "listed_tables"):
        return st.green("●")
    if event.startswith("write_"):
        return st.yellow("◆")
    if event in BLOCKED_EVENTS or event in ("stopped", "denied"):
        return st.red("✗")
    return st.dim("○")


def _story(cfg, say, st: Style) -> None:
    key = cfg.signing_key()
    support = Gateway(cfg, tokens.mint(key, "demo-support", "you", 600))
    ops = Gateway(cfg, tokens.mint(key, "demo-ops", "you", 600))

    def scene(n: int, icon: str, title: str) -> None:
        st.pause(0.9)
        say("", f"  {icon} {st.bold(title)} {st.dim('─' * (58 - len(title)) + f' {n}/6')}", "")
        st.pause(0.3)

    def ask(agent: str, sql: str, after: str = "") -> None:
        st.typed(f"  {st.bold(agent)} {st.dim('❯')} ", _shown(sql), say, after)
        st.pause(0.3)

    def attempt(gw: Gateway, sql: str) -> tuple[dict | None, str | None]:
        try:
            return gw.query(sql), None
        except GatewayError as e:
            return None, str(e)

    def blocked(err: str, expected: str, short: str, label: str = "✗ blocked") -> str:
        return f"{st.red(label)}  {st.dim(short if expected in err else err)}"

    scene(1, "🔒", "Row locks")
    sql = f"SELECT account_id, name, email, card FROM {SCHEMA}.customers ORDER BY id"
    ask("demo-support", sql)
    out = support.query(sql)
    table = [[str(c) for c in out["columns"]], *[[str(v) for v in row] for row in out["rows"]]]
    widths = [max(len(r[i]) for r in table) for i in range(len(table[0]))]
    say("     " + st.dim("  ".join(c.ljust(w) for c, w in zip(table[0], widths))))
    for row in table[1:]:
        say("     " + "  ".join(st.masked(c.ljust(w)) for c, w in zip(row, widths)))
        st.pause(0.12)
    say("     " + st.dim(f"🔒 {_n(6 - out['row_count'], 'more row')} from accounts 7 and 13 never left Postgres"))
    st.pause(0.3)
    say("", "  " + st.ok(f"{out['row_count']} of 6 customers. Postgres enforces it, not the prompt."))

    scene(2, "🎭", "Masking")
    sql = f"SELECT body FROM {SCHEMA}.notes"
    ask("demo-support", sql)
    for row in support.query(sql)["rows"]:
        for line in textwrap.wrap(str(row[0]), 62):
            say(f"     {st.dim('│')} {st.masked(line)}")
            st.pause(0.12)
    st.pause(0.3)
    say("", "  " + st.ok("Card, email and phone masked, even inside free text."))

    scene(3, "💉", "Prompt injection")
    say(st.dim("  A poisoned prompt makes demo-support try:"), "")
    for sql, expected, short in (
            (f"SELECT * FROM {SCHEMA}.customers WHERE id = 4", "", "row lock"),
            (f"SELECT 1; DROP TABLE {SCHEMA}.orders", "exactly one SQL statement", "one statement per call"),
            ("SELECT pg_read_file('/etc/passwd')", "pg_read_file() is not allowed", "pg_read_file() not allowed"),
            (f"UPDATE {SCHEMA}.orders SET total = 0", "propose_write", "writes wait for a person")):
        out, err = attempt(support, sql)
        if err:
            verdict = blocked(err, expected, short)
        elif out["row_count"] == 0:
            verdict = f"{st.red('✗ hidden')}   {st.dim(short)}"
        else:
            verdict = st.yellow(f"! returned {_n(out['row_count'], 'row')}")
        st.typed(f"  {st.dim('❯')} ", _shown(sql), say, " " * (36 - len(_shown(sql))) + "  " + verdict)
        st.pause(0.2)

    scene(4, "✋", "Approval")
    sql = f"UPDATE {SCHEMA}.orders SET status = 'refunded' WHERE status = 'late'"
    ask("demo-ops", sql)
    proposed = ops.propose_write(sql, "customer asked for refunds on late orders")
    say(f"     ⏳ held: {_n(proposed['would_affect_rows'], 'row')} would change, all in account 42")
    with st.spinner("waiting for a person", say, indent="     "):
        st.pause(1.4)
    decide(cfg, proposed["write_id"], True, "you (demo)")
    done = ops.execute_write(proposed["write_id"])
    say("     " + st.ok(f"approved by you, {_n(done['affected_rows'], 'row')} updated"))

    scene(5, "🛑", "Brakes")
    limit = cfg.agent("demo-ops").rate_limit
    say(st.dim(f"  demo-ops is stuck in a retry loop. Its limit is {limit} requests a minute."), "")
    sql = f"SELECT count(*) FROM {SCHEMA}.orders"
    ask("demo-ops", sql)
    results = [attempt(ops, sql) for _ in range(7)]
    ran = sum(err is None for _, err in results)
    stop = next(err for _, err in results if err)
    pieces = [st.dim("■ ")] * (limit - ran) + [st.green("■ ") if err is None else st.red("✗ ") for _, err in results]
    st.build("     ", pieces, say, " " + blocked(stop, f"rate limit of {limit} requests a minute",
                                                 f"rate limit, {limit} a minute", "stopped"), step=0.15)
    st.pause(0.6)
    controls.pause_agent(cfg, cfg.agent("demo-support"), "you (demo)", "incident review")
    say("", f"  {st.bold('You pause demo-support.')}")
    sql = f"SELECT name FROM {SCHEMA}.customers"
    _, err = attempt(support, sql)
    ask("demo-support", sql, "   " + blocked(err or "", "paused by you (demo)", "paused by you (demo)"))

    scene(6, "🧾", "Signed ledger")
    ledger = Ledger(cfg.ledger_path, key)
    ok = ledger.verify()[0]
    entries = ledger.entries()
    report = build_report(entries)
    links = [_glyph(st, e.get("event", "")) + (st.dim("─") if i < len(entries) - 1 else "")
             for i, e in enumerate(entries)]
    st.build("     ", links, say, step=0.05)
    say(f"     {st.green('●')} {st.dim('read')}  {st.yellow('◆')} {st.dim('write')}  "
        f"{st.red('✗')} {st.dim('blocked')}  {st.dim('○ control')}")
    st.pause(0.3)
    say("", "  " + (st.ok(f"{len(entries)} entries, hash chain verified") if ok
                    else st.red(f"{len(entries)} entries, hash chain BROKEN")))
    flagged = [(a.agent, d) for a in report["agents"].values() for _, d in a.flags]
    width = max((len(agent) for agent, _ in flagged), default=0)
    for agent, description in flagged:
        st.pause(0.2)
        say(f"  🚩 {st.bold(agent.ljust(width))}  {description}")


def run_demo(dsn: str | None = None, keep: bool = False, out=print, style: Style | None = None) -> None:
    st = style or Style()

    def say(*lines: str) -> None:
        for line in lines:
            out(line)

    say(*st.banner())
    say(f"  {st.bold('The demo:')} two AI agents meet a production-like Postgres.", "")
    container = None
    if dsn is None:
        dsn, container = _start_postgres(say, st)
    else:
        say("  " + st.ok(f"Using your database. Everything goes in schema {SCHEMA} and is removed at the end."))
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
            _story(cfg, say, st)
    finally:
        if container and not keep:
            subprocess.run(["docker", "stop", container], capture_output=True, timeout=60)
        elif not keep:
            _cleanup(dsn, roles, drop_group=not had_group)
    st.pause(0.9)
    say("", f"  ✨ {st.bold('Your turn')}", "",
        "     pipx install lokra",
        '     lokra init "postgresql://…"',
        '     lokra provision --admin-dsn "…"', "")
    if keep:
        say(st.dim(f"  Kept the demo database: {dsn} (schema {SCHEMA})"))
    elif container:
        say(st.dim("  The throwaway database has been removed."))
    else:
        say(st.dim(f"  Removed schema {SCHEMA} and the demo roles from that database."))


def main(dsn: str | None, keep: bool) -> int:
    st = Style.detect()
    try:
        with st.no_cursor():
            run_demo(dsn, keep, style=st)
    except DemoError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0
