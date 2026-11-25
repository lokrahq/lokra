from __future__ import annotations

import secrets

import psycopg
from psycopg import sql

from . import controls
from .config import AgentPolicy, Config, split_table, table_key

WRITE_OPS = {"insert": "INSERT", "update": "UPDATE", "delete": "DELETE"}
AGENT_GROUP = "lokra_agents"
PASSTHROUGH = "lokra_passthrough"
SYSTEM_SCHEMAS = ("pg_catalog", "information_schema", "pg_toast")


def _ident(table: str) -> sql.Identifier:
    return sql.Identifier(*split_table(table))


def _grant_statements(agent: AgentPolicy, db_name: str, prior_schemas: set[str]) -> list[sql.Composed]:
    r = sql.Identifier(agent.role)
    stmts = []
    for schema in sorted(set(agent.schemas) | prior_schemas):
        s = sql.Identifier(schema)
        stmts.append(sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA {} FROM {}").format(s, r))
        stmts.append(sql.SQL("REVOKE ALL ON ALL SEQUENCES IN SCHEMA {} FROM {}").format(s, r))
        if schema not in agent.schemas:
            stmts.append(sql.SQL("REVOKE USAGE ON SCHEMA {} FROM {}").format(s, r))
    stmts.append(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(db_name), r))
    for schema in agent.schemas:
        stmts.append(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(schema), r))
    for table, cols in agent.read.items():
        t = _ident(table)
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
        stmts.append(sql.SQL("GRANT {} ON {} TO {}").format(privs, _ident(table), r))
    for schema in sorted({split_table(t)[0] for t in agent.write}):
        stmts.append(sql.SQL("GRANT USAGE ON ALL SEQUENCES IN SCHEMA {} TO {}").format(sql.Identifier(schema), r))
    return stmts


def _prior_schemas(conn: psycopg.Connection, role: str) -> set[str]:
    rows = conn.execute("SELECT DISTINCT table_schema FROM information_schema.role_table_grants WHERE grantee = %s "
                        "UNION SELECT nspname FROM pg_namespace WHERE has_schema_privilege(%s, oid, 'USAGE') "
                        "AND nspname <> ALL(%s) AND nspname NOT LIKE 'pg\\_%%'",
                        (role, role, list(SYSTEM_SCHEMAS))).fetchall()
    return {s for (s,) in rows}


def _columns(conn: psycopg.Connection, schemas: list[str]) -> dict[str, set[str]]:
    rows = conn.execute("SELECT table_schema, table_name, column_name FROM information_schema.columns "
                        "WHERE table_schema = ANY(%s)", (schemas,)).fetchall()
    out: dict[str, set[str]] = {}
    for schema, table, column in rows:
        out.setdefault(table_key(schema, table), set()).add(column)
    return out


def _rls_enabled(conn: psycopg.Connection, table: str) -> bool:
    row = conn.execute("SELECT c.relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                       "WHERE n.nspname = %s AND c.relname = %s", split_table(table)).fetchone()
    return bool(row and row[0])


def _locked_tables(agent: AgentPolicy, columns: dict[str, set[str]]) -> dict[str, str]:
    if not agent.rows:
        return {}
    out = {}
    for table in agent.tables:
        column = agent.rows.column_for(table)
        if column in columns.get(table, set()):
            out[table] = column
    return out


def _enable_rls(conn: psycopg.Connection, table: str) -> None:
    """Turn on row-level security without changing what non-agent roles can see."""
    t = _ident(table)
    not_agent = sql.SQL("NOT pg_has_role(session_user, {}, 'MEMBER')").format(sql.Literal(AGENT_GROUP))
    conn.execute(sql.SQL("ALTER TABLE {} ENABLE ROW LEVEL SECURITY").format(t))
    conn.execute(sql.SQL("CREATE POLICY {} ON {} AS PERMISSIVE FOR ALL TO PUBLIC USING ({}) WITH CHECK ({})")
                 .format(sql.Identifier(PASSTHROUGH), t, not_agent, not_agent))


def _drop_agent_policies(conn: psycopg.Connection, agent: AgentPolicy) -> None:
    names = [f"{agent.role}_{suffix}" for suffix in ("rows", "lock", "all")]
    for schema, table, name in conn.execute("SELECT schemaname, tablename, policyname FROM pg_policies "
                                            "WHERE policyname = ANY(%s)", (names,)).fetchall():
        conn.execute(sql.SQL("DROP POLICY {} ON {}").format(sql.Identifier(name), sql.Identifier(schema, table)))


def _apply_row_policies(conn: psycopg.Connection, agent: AgentPolicy, locked: dict[str, str]) -> None:
    r = sql.Identifier(agent.role)
    for table in agent.tables:
        t = _ident(table)
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
    tables = conn.execute("SELECT schemaname, tablename FROM pg_policies WHERE policyname = %s",
                          (PASSTHROUGH,)).fetchall()
    for schema, table in tables:
        restrictive = conn.execute("SELECT count(*) FROM pg_policies WHERE schemaname = %s AND tablename = %s "
                                   "AND permissive = 'RESTRICTIVE' AND policyname LIKE 'lokra\\_%%'",
                                   (schema, table)).fetchone()[0]
        if restrictive:
            continue
        t = sql.Identifier(schema, table)
        for (name,) in conn.execute("SELECT policyname FROM pg_policies WHERE schemaname = %s AND tablename = %s "
                                    "AND policyname LIKE 'lokra\\_%%'", (schema, table)).fetchall():
            conn.execute(sql.SQL("DROP POLICY {} ON {}").format(sql.Identifier(name), t))
        conn.execute(sql.SQL("ALTER TABLE {} DISABLE ROW LEVEL SECURITY").format(t))


def set_login(cfg: Config, admin_dsn: str, agent: AgentPolicy, allowed: bool) -> int:
    """Allow or block the agent's database login. Blocking also ends its open sessions."""
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (agent.role,)).fetchone():
            return 0
        conn.execute(sql.SQL("ALTER ROLE {} " + ("LOGIN" if allowed else "NOLOGIN")).format(sql.Identifier(agent.role)))
        if allowed:
            return 0
        return len(conn.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = %s",
                                (agent.role,)).fetchall())


def provision(cfg: Config, admin_dsn: str) -> list[str]:
    log: list[str] = []
    saved = cfg.db_secrets()
    paused = controls.snapshot(cfg)["paused"]
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (AGENT_GROUP,)).fetchone():
            conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(AGENT_GROUP)))
        columns = _columns(conn, sorted({s for a in cfg.agents.values() for s in a.schemas}))
        missing = sorted({t for a in cfg.agents.values() for t in a.tables} - set(columns))
        if missing:
            raise ValueError(f"tables in the policy that do not exist: {', '.join(missing)}")
        locks = {name: _locked_tables(agent, columns) for name, agent in cfg.agents.items()}
        with conn.transaction():
            for table in sorted({t for tables in locks.values() for t in tables}):
                if not _rls_enabled(conn, table):
                    _enable_rls(conn, table)
        for agent in cfg.agents.values():
            r = sql.Identifier(agent.role)
            exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (agent.role,)).fetchone()
            prior = _prior_schemas(conn, agent.role) if exists else set()
            password = saved.get(agent.role) or secrets.token_urlsafe(24)
            verb, past = ("ALTER", "altered") if exists else ("CREATE", "created")
            login = "NOLOGIN" if agent.name in paused else "LOGIN"
            conn.execute(sql.SQL(
                verb + " ROLE {} WITH " + login + " NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT "
                "NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 10 PASSWORD {}"
            ).format(r, sql.Literal(password)))
            for setting, value in (("statement_timeout", "5s"),
                                   ("idle_in_transaction_session_timeout", "10s"),
                                   ("search_path", "public")):
                conn.execute(sql.SQL("ALTER ROLE {} SET {} = {}").format(
                    r, sql.Identifier(setting), sql.Literal(value)))
            conn.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(AGENT_GROUP), r))
            with conn.transaction():
                for stmt in _grant_statements(agent, cfg.db_name, prior):
                    conn.execute(stmt)
                _drop_agent_policies(conn, agent)
                _apply_row_policies(conn, agent, locks[agent.name])
            saved[agent.role] = password
            locked = locks[agent.name]
            if locked:
                scope = f"{agent.rows.describe()} on {sorted(locked)}"
                unlocked = sorted(set(agent.tables) - set(locked))
                if unlocked:
                    scope += f"; all rows on {unlocked}"
            else:
                scope = "all rows (no row lock)"
            if agent.name in paused:
                scope += ", PAUSED"
            log.append(f"{past} role {agent.role}: {scope}, "
                       f"reads {sorted(agent.read)}, writes {sorted(agent.write) or 'none'}")
        with conn.transaction():
            _release_unlocked_tables(conn)
    cfg.save_db_secrets(saved)
    return log
