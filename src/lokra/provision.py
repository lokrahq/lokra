from __future__ import annotations

import secrets

import psycopg
from psycopg import sql

from .config import AgentPolicy, Config

WRITE_OPS = {"insert": "INSERT", "update": "UPDATE", "delete": "DELETE"}
AGENT_GROUP = "lokra_agents"
PASSTHROUGH = "lokra_passthrough"


def _grant_statements(agent: AgentPolicy, db_name: str) -> list[sql.Composed]:
    r = sql.Identifier(agent.role)
    stmts = [
        sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}").format(r),
        sql.SQL("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {}").format(r),
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(db_name), r),
        sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(r),
    ]
    for table, cols in agent.read.items():
        t = sql.Identifier(table)
        if cols == "*" or cols == ["*"]:
            stmts.append(sql.SQL("GRANT SELECT ON {} TO {}").format(t, r))
        else:
            stmts.append(sql.SQL("GRANT SELECT ({}) ON {} TO {}").format(
                sql.SQL(", ").join(map(sql.Identifier, cols)), t, r))
    for table, ops in agent.write.items():
        unknown = set(ops) - set(WRITE_OPS)
        if unknown:
            raise ValueError(f"{agent.name}: unknown write ops {sorted(unknown)} on {table}")
        privs = sql.SQL(", ").join(sql.SQL(WRITE_OPS[o]) for o in ops)
        stmts.append(sql.SQL("GRANT {} ON {} TO {}").format(privs, sql.Identifier(table), r))
    if agent.write:
        stmts.append(sql.SQL("GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {}").format(r))
    return stmts


def _public_columns(conn: psycopg.Connection) -> dict[str, set[str]]:
    rows = conn.execute("SELECT table_name, column_name FROM information_schema.columns "
                        "WHERE table_schema = 'public'").fetchall()
    out: dict[str, set[str]] = {}
    for table, column in rows:
        out.setdefault(table, set()).add(column)
    return out


def _rls_enabled(conn: psycopg.Connection, table: str) -> bool:
    row = conn.execute("SELECT c.relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                       "WHERE n.nspname = 'public' AND c.relname = %s", (table,)).fetchone()
    return bool(row and row[0])


def _locked_tables(agent: AgentPolicy, columns: dict[str, set[str]]) -> dict[str, str]:
    if not agent.rows:
        return {}
    out = {}
    for table in sorted(set(agent.read) | set(agent.write)):
        column = agent.rows.column_for(table)
        if column in columns.get(table, set()):
            out[table] = column
    return out


def _enable_rls(conn: psycopg.Connection, table: str) -> None:
    """Turn on row-level security without changing what non-agent roles can see."""
    t = sql.Identifier(table)
    not_agent = sql.SQL("NOT pg_has_role(session_user, {}, 'MEMBER')").format(sql.Literal(AGENT_GROUP))
    conn.execute(sql.SQL("ALTER TABLE {} ENABLE ROW LEVEL SECURITY").format(t))
    conn.execute(sql.SQL("CREATE POLICY {} ON {} AS PERMISSIVE FOR ALL TO PUBLIC USING ({}) WITH CHECK ({})")
                 .format(sql.Identifier(PASSTHROUGH), t, not_agent, not_agent))


def _drop_agent_policies(conn: psycopg.Connection, agent: AgentPolicy) -> None:
    names = [f"{agent.role}_{suffix}" for suffix in ("rows", "lock", "all")]
    for table, name in conn.execute("SELECT tablename, policyname FROM pg_policies "
                                    "WHERE schemaname = 'public' AND policyname = ANY(%s)", (names,)).fetchall():
        conn.execute(sql.SQL("DROP POLICY {} ON {}").format(sql.Identifier(name), sql.Identifier(table)))


def _apply_row_policies(conn: psycopg.Connection, agent: AgentPolicy, locked: dict[str, str]) -> None:
    r = sql.Identifier(agent.role)
    for table in sorted(set(agent.read) | set(agent.write)):
        t = sql.Identifier(table)
        if table in locked:
            cond = sql.SQL("{} IN ({})").format(
                sql.Identifier(locked[table]), sql.SQL(", ").join(map(sql.Literal, agent.rows.values)))
            for name, kind in ((f"{agent.role}_rows", "PERMISSIVE"), (f"{agent.role}_lock", "RESTRICTIVE")):
                conn.execute(sql.SQL("CREATE POLICY {} ON {} AS " + kind + " FOR ALL TO {} USING ({}) WITH CHECK ({})")
                             .format(sql.Identifier(name), t, r, cond, cond))
        elif _rls_enabled(conn, table):
            conn.execute(sql.SQL("CREATE POLICY {} ON {} AS PERMISSIVE FOR ALL TO {} USING (true) WITH CHECK (true)")
                         .format(sql.Identifier(f"{agent.role}_all"), t, r))


def _release_unlocked_tables(conn: psycopg.Connection) -> None:
    """Undo row-level security that Lokra turned on, once no agent's rows are locked on that table."""
    tables = [t for (t,) in conn.execute("SELECT tablename FROM pg_policies WHERE schemaname = 'public' "
                                         "AND policyname = %s", (PASSTHROUGH,)).fetchall()]
    for table in tables:
        restrictive = conn.execute("SELECT count(*) FROM pg_policies WHERE schemaname = 'public' AND tablename = %s "
                                   "AND permissive = 'RESTRICTIVE' AND policyname LIKE 'lokra\\_%%'",
                                   (table,)).fetchone()[0]
        if restrictive:
            continue
        t = sql.Identifier(table)
        for (name,) in conn.execute("SELECT policyname FROM pg_policies WHERE schemaname = 'public' "
                                    "AND tablename = %s AND policyname LIKE 'lokra\\_%%'", (table,)).fetchall():
            conn.execute(sql.SQL("DROP POLICY {} ON {}").format(sql.Identifier(name), t))
        conn.execute(sql.SQL("ALTER TABLE {} DISABLE ROW LEVEL SECURITY").format(t))


def provision(cfg: Config, admin_dsn: str) -> list[str]:
    log: list[str] = []
    saved = cfg.db_secrets()
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (AGENT_GROUP,)).fetchone():
            conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(AGENT_GROUP)))
        columns = _public_columns(conn)
        locks = {name: _locked_tables(agent, columns) for name, agent in cfg.agents.items()}
        with conn.transaction():
            for table in sorted({t for tables in locks.values() for t in tables}):
                if not _rls_enabled(conn, table):
                    _enable_rls(conn, table)
        for agent in cfg.agents.values():
            r = sql.Identifier(agent.role)
            password = saved.get(agent.role) or secrets.token_urlsafe(24)
            exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (agent.role,)).fetchone()
            verb, past = ("ALTER", "altered") if exists else ("CREATE", "created")
            conn.execute(sql.SQL(
                verb + " ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT "
                "NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 10 PASSWORD {}"
            ).format(r, sql.Literal(password)))
            for setting, value in (("statement_timeout", "5s"),
                                   ("idle_in_transaction_session_timeout", "10s"),
                                   ("search_path", "public")):
                conn.execute(sql.SQL("ALTER ROLE {} SET {} = {}").format(
                    r, sql.Identifier(setting), sql.Literal(value)))
            conn.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(AGENT_GROUP), r))
            with conn.transaction():
                for stmt in _grant_statements(agent, cfg.db_name):
                    conn.execute(stmt)
                _drop_agent_policies(conn, agent)
                _apply_row_policies(conn, agent, locks[agent.name])
            saved[agent.role] = password
            locked = locks[agent.name]
            if locked:
                scope = f"{agent.rows.describe()} on {sorted(locked)}"
                unlocked = sorted((set(agent.read) | set(agent.write)) - set(locked))
                if unlocked:
                    scope += f"; all rows on {unlocked}"
            else:
                scope = "all rows (no row lock)"
            log.append(f"{past} role {agent.role}: {scope}, "
                       f"reads {sorted(agent.read)}, writes {sorted(agent.write) or 'none'}")
        with conn.transaction():
            _release_unlocked_tables(conn)
    cfg.save_db_secrets(saved)
    return log
