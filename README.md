<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="brand/lokra-logo-white.svg">
    <img src="brand/lokra-logo.svg" alt="Lokra" height="56">
  </picture>
</p>

<p align="center"><b>Lock rows for AI agents.</b> · <a href="https://lokra.dev">lokra.dev</a></p>

<p align="center">
  <a href="https://github.com/lokrahq/lokra/actions/workflows/ci.yml"><img src="https://github.com/lokrahq/lokra/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <img src="https://img.shields.io/badge/license-Apache--2.0-blue" alt="License: Apache-2.0">
  <img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="Python 3.11+">
</p>

Lokra sits between AI agents (Claude Code, Cursor, OpenClaw, custom agents) and your
Postgres database, and lets the database itself decide what each agent may see and change.

Agents today usually get a full database password. Lokra gives each agent its own
locked-down identity instead, and puts these controls on every request:

1. **Scoped identity.** Each agent logs in as its own Postgres role with only the tables,
   columns and rows its policy allows. Agents receive a short-lived signed token, never a password.
2. **Masking.** Medicare numbers, IHIs, phone numbers and emails are masked in results,
   including inside free-text notes and renamed columns.
3. **Human approval for writes.** INSERT, UPDATE and DELETE are dry-run, shown with the
   number of rows they would change, and only run after a person approves them.
4. **Tamper-evident audit ledger, with reporting.** Every request is written to a hash-chained,
   signed log, and `lokra report` turns it into a per-agent activity and risk summary.

> v0.1, local prototype. The lab uses **synthetic data only**.

## Quick start

Needs Docker and Python 3.11+.

```bash
docker compose up -d                      # lab Postgres on 127.0.0.1:55432 with fake clinic data
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/lokra provision                   # creates one DB role per agent in policies.yaml
.venv/bin/python -m pytest -q             # runs the test suite, including attack attempts
```

Try it from the terminal as an agent:

```bash
.venv/bin/lokra try scheduling-bot "SELECT full_name, medicare_number FROM patients LIMIT 3"
.venv/bin/lokra try scheduling-bot "SELECT note FROM clinical_notes"          # refused: no access
.venv/bin/lokra try scheduling-bot "UPDATE appointments SET status='cancelled' WHERE id=1" --write --reason "pt cancelled"
.venv/bin/lokra pending
.venv/bin/lokra approve <write_id>
.venv/bin/lokra log
.venv/bin/lokra verify-ledger
```

## Use your own database

Point Lokra at any Postgres, including a Supabase database. `lokra init` connects
**read-only**, introspects the schema, flags likely PII columns (email, phone,
health and government identifiers) and writes a starter policy for you to review:

```bash
lokra init "postgresql://user:pass@host:5432/dbname"   # read-only, writes policies.yaml
# review and tighten policies.yaml, then provision the per-agent roles:
lokra provision --admin-dsn "postgresql://admin:pass@host:5432/dbname"
lokra dashboard
```

For **Supabase**, use the connection string from Project Settings → Database (the
direct connection, not the pooler). `init` only reads; `provision` needs a role that
can create roles, such as `postgres`. Try it on a development project first.

`policies.example.yaml` is a non-healthcare starting point you can copy instead.
Per-row tenant isolation (row-level security) is optional and set up separately; the
clinic lab in this repo is a worked example of it.

## Connect Claude Code

**Desktop app or CLI:** put this `.mcp.json` in the folder where the agent works, then open a
new Claude Code session in that folder and allow the `clinic-db` server when asked.

```json
{
  "mcpServers": {
    "clinic-db": {
      "command": "/absolute/path/to/lokra/.venv/bin/lokra",
      "args": ["--config", "/absolute/path/to/lokra/policies.yaml",
               "serve", "--dev-agent", "scheduling-bot"]
    }
  }
}
```

`--dev-agent` issues an 8-hour token when the server starts. It is for local development
only; in production the token is issued by a human or the control plane and passed in
`LOKRA_TOKEN`.

**CLI alternative**, with an explicit token:

```bash
claude mcp add clinic-db \
  -e LOKRA_TOKEN="$(.venv/bin/lokra mint-token scheduling-bot --ttl 28800)" \
  -- "$PWD/.venv/bin/lokra" --config "$PWD/policies.yaml" serve
```

Then ask something like "which patients have appointments on Monday?".

Tools the agent sees: `whoami`, `list_tables`, `query`, `propose_write`, `write_status`, `execute_write`.

## Website

The lokra.dev site lives in `site/`. Preview it locally with clean URLs:

```bash
python3 scripts/serve_site.py
```

## Reports

The audit ledger is not just a log. `lokra report` reads it back into an activity
and risk summary: what each agent did, how many sensitive identifiers it touched,
and plain, explained flags such as repeated blocked statements (a sign of probing
or prompt injection) or an unusually large read.

```bash
lokra report            # per-agent activity and risk flags
lokra report --json     # machine-readable, for a SIEM or an evidence pack
```

For a live view, `lokra dashboard` serves a local web page (127.0.0.1) with a
dashboard, a full audit log, and a policy editor that rewrites `policies.yaml`
and reprovisions the database roles for you.

<p align="center"><img src="docs/dashboard.png" alt="Lokra dashboard: agents, statement outcomes and risk flags" width="820"></p>

The audit log shows every statement, what was masked, why requests were blocked,
and each write from proposal through approval:

<p align="center"><img src="docs/audit.png" alt="Lokra audit log: masking breakdown, blocked reasons and activity feed" width="820"></p>

Flags are rules over the ledger, so each one traces back to its entries. This is
the evidence an auditor asks for, generated from what already happened.

## Conformance

`conformance/` is a benchmark of nine safety guarantees a database access layer
should uphold against a hostile agent (reject stacked statements, no OS command
execution, no cross-tenant reads, identifiers masked, and more). It scores Lokra
and a deliberately broken reference. Lokra upholds all nine.

```bash
python -m conformance.run
```

Run it only against layers you operate, in the disposable lab. See
[conformance/README.md](conformance/README.md) for the safety and disclosure rules.

## Security model

The SQL parser is **not** the security boundary. Several "read-only" database MCP servers
were bypassed in 2025-26 because they relied on parsing SQL text. Here the database itself
enforces access, and each layer still holds if the one before it fails:

| Layer | Stops | Enforced by |
|---|---|---|
| Signed short-lived token | Unknown, forged or expired agents | Proxy (HMAC) |
| SQL check | Writes in the read tool, stacked statements, DDL, `SET ROLE` | Proxy (sqlglot), fails closed |
| Prepared statements only | `COMMIT; DROP ...` style stacking | Postgres protocol |
| Per-agent login role | Tables and columns outside the policy | Postgres grants |
| Row-level security | Rows from other clinics | Postgres RLS keyed on `session_user` |
| Read-only transaction | Writes hidden in CTEs or functions | Postgres |
| Statement timeout | Runaway queries | Postgres role setting |
| Masking | Identifiers in results, aliases, free text | Proxy, checksum-validated detectors |
| Ledger | Silent edits or deletions of history | SHA-256 chain + HMAC |

The tests in `tests/test_integration.py` disable the SQL check on purpose and confirm the
database still blocks the attack.

## Layout

```
policies.yaml          who may do what
lab/db/init/           lab schema (with RLS) and generated synthetic seed data
scripts/generate_seed.py
src/lokra/
  config.py            loads policies.yaml, local state in .lokra/ (gitignored)
  tokens.py            short-lived signed agent tokens
  sqlcheck.py          read/write classification (usability layer)
  provision.py         creates per-agent Postgres roles, grants and clinic scope
  masking.py           column rules and Medicare/IHI/email/phone detectors
  ledger.py            hash-chained, signed audit log
  gateway.py           the request pipeline and write approvals
  server.py            MCP server
  cli.py               `lokra` command
tests/
```

## Roadmap

- [x] Works against any Postgres, including Supabase (`lokra init` scaffolds the policy)
- [ ] Slack approve/deny buttons instead of the CLI
- [x] Conformance suite: twelve safety guarantees, scored in `conformance/`
- [x] Activity and risk reports from the ledger (`lokra report`, `lokra dashboard`)
- [ ] Evidence export mapped to the US HSCC AI vendor questionnaire and AU privacy obligations
- [ ] Hosted control plane: policy editor, approvals UI, long ledger retention, SSO
- [ ] DuckDB and Snowflake adapters

## License

Apache 2.0. See [LICENSE](LICENSE).
