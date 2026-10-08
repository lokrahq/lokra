from __future__ import annotations

import secrets

import psycopg
from psycopg import sql

from .config import AgentPolicy, Config

WRITE_OPS = {"insert": "INSERT", "update": "UPDATE", "delete": "DELETE"}


def _grant_statements(agent: AgentPolicy, db_name: str) -> list[sql.Composed]:
    r = sql.Identifier(agent.role)
    stmts = [
        sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}").format(r),
        sql.SQL("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {}").format(r),
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(db_name), r),
        sql.SQL("GRANT USAGE ON SCHEMA public, lokra TO {}").format(r),
        sql.SQL("GRANT EXECUTE ON FUNCTION lokra.agent_clinic_ids() TO {}").format(r),
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


def provision(cfg: Config, admin_dsn: str) -> list[str]:
    log: list[str] = []
    saved = cfg.db_secrets()
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        for agent in cfg.agents.values():
            r = sql.Identifier(agent.role)
            password = saved.get(agent.role) or secrets.token_urlsafe(24)
            exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (agent.role,)).fetchone()
            verb = "ALTER" if exists else "CREATE"
            conn.execute(sql.SQL(
                verb + " ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT "
                "NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 10 PASSWORD {}"
            ).format(r, sql.Literal(password)))
            for setting, value in (("statement_timeout", "5s"),
                                   ("idle_in_transaction_session_timeout", "10s"),
                                   ("search_path", "public")):
                conn.execute(sql.SQL("ALTER ROLE {} SET {} = {}").format(
                    r, sql.Identifier(setting), sql.Literal(value)))
            with conn.transaction():
                for stmt in _grant_statements(agent, cfg.db_name):
                    conn.execute(stmt)
                conn.execute("DELETE FROM lokra.agent_scopes WHERE role_name = %s", (agent.role,))
                for cid in agent.clinic_ids:
                    conn.execute("INSERT INTO lokra.agent_scopes (role_name, clinic_id) VALUES (%s, %s)",
                                 (agent.role, cid))
            saved[agent.role] = password
            log.append(f"{verb.lower()}d role {agent.role}: clinics {agent.clinic_ids}, "
                       f"reads {sorted(agent.read)}, writes {sorted(agent.write) or 'none'}")
    cfg.save_db_secrets(saved)
    return log
