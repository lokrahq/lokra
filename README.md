<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="brand/lokra-logo-white.svg">
    <img src="brand/lokra-logo.svg" alt="Lokra" height="56">
  </picture>
</p>

<p align="center"><b>Lock rows for AI agents.</b> · <a href="https://lokra.dev">lokra.dev</a></p>

<p align="center"><img src="docs/demo.svg" alt="lokra demo: two AI agents meet a production-like Postgres. Row locks, masking, blocked prompt injection, an approved write, a rate limit and pause, and the signed ledger." width="820"></p>

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
2. **Masking.** Emails, phone numbers, payment cards, IBANs, US Social Security numbers and
   Australian Medicare and IHI numbers are masked in results, including inside free-text notes,
   JSON and arrays. A masked column can only be read as itself, so a query cannot slip it out
   through a function, an alias or `row_to_json`. Cards, IBANs and health identifiers are
   checksum-validated, so ordinary numbers are left alone.
3. **Human approval for writes.** INSERT, UPDATE and DELETE are dry-run, shown with the
   number of rows they would change, and only run after a person approves them. Rules can let
   small writes through on their own or require two people, and a write that would change more
   rows than were approved is rolled back.
4. **Tamper-evident audit ledger, with reporting.** Every request is written to a hash-chained,
   signed log, and `lokra report` turns it into a per-agent activity and risk summary.
5. **Purpose of use.** An agent's token can carry a declared purpose. The policy can require one
   (`require_purpose: true`) and limit which purposes an agent may use, and every request records it.

## See it in a minute

```bash
pipx install lokra
lokra demo
```

The demo starts a throwaway Postgres in Docker, loads a small fake SaaS database, and walks two
AI agents through row locks, masking, refused injections, an approved write, a rate limit, a
pause and the signed ledger, then removes everything. No Docker? Point it at any Postgres where
you can create roles: `lokra demo --dsn "postgresql://…"`; it works in its own schema and
cleans up after itself.

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

Install it with `pipx install lokra` (or `uv tool install lokra`), then point it at any
Postgres, including a Supabase database. `lokra init` connects
**read-only**, introspects the schema, flags likely PII columns (email, phone,
health and government identifiers) and writes a starter policy for you to review:

```bash
lokra init "postgresql://user:pass@host:5432/dbname"    # read-only, writes policies.yaml
lokra check --dsn "postgresql://user:pass@host:5432/dbname"   # validate it against the schema
# review and tighten policies.yaml, then provision the per-agent roles:
lokra provision --admin-dsn "postgresql://admin:pass@host:5432/dbname"
lokra connect                                             # add the agent to Claude Code
```

`lokra check` reads the database and flags grants on tables or columns that do
not exist, any sensitive-looking column an agent can read with nothing masking
it, and writes without a matching read, so you catch policy mistakes before an
agent ever connects. In CI, `lokra check --strict` also fails on warnings, so a new
`phone` column that nothing masks cannot slip into production unnoticed.

For **Supabase**, use the connection string from Project Settings → Database (the
direct connection, not the pooler). `init` only reads; `provision` needs a role that
can create roles, such as `postgres`. It does not need a superuser, so the RDS master user
works too. Try it on a development project first.

`policies.example.yaml` is a non-healthcare starting point you can copy instead.

Tables outside the `public` schema are named `schema.table` in the policy, for example
`billing.invoices: "*"`. Lokra grants the agent only those schemas, and takes them away
again when they leave the policy. To scaffold from several schemas at once, run
`lokra init "<dsn>" --schemas public,billing`.

### Lock rows

Give an agent a `rows` rule and it only sees, inserts and updates rows whose column holds
one of the listed values, on every table it can reach that has that column:

```yaml
agents:
  support-bot:
    rows: {column: account_id, values: [42]}
    read: {customers: "*", orders: "*", products: "*"}
```

`lokra provision` turns this into Postgres row-level security policies for that agent's
role, so the database enforces it. Other roles and your app keep the access they had:
if a table had row-level security off, Lokra turns it on with a pass-through policy for
everyone except Lokra's agents, and turns it off again when no agent's rows are locked
there. Where a table names the scope differently, map it with
`tables: {accounts: id}`. `lokra check` warns about tables the agent can read that have
no column to lock by.

A view normally runs with its owner's rights, which would read past the lock, so an agent with
a row lock can only be given views that run as the agent (`security_invoker`, Postgres 15+).
The lock then holds on the tables underneath. `lokra provision` and `lokra check` refuse other
views and materialized views for that agent.

## Connect your agent

```bash
lokra connect                           # Claude Code: writes .mcp.json in this folder
lokra connect --client claude-desktop   # or cursor, vscode, windsurf
```

`lokra connect` logs in as the agent's own database role to check it works, then adds a
`lokra-<agent>` server to the client's MCP config with absolute paths, so desktop apps that
do not see your shell's PATH can start it. It keeps the other servers in that file. Name the
agent when the policy has more than one: `lokra connect scheduling-bot`. Open the client
(restart Claude Desktop), allow the server, and ask something like "which patients have
appointments on Monday?".

The server issues the agent an 8-hour token each time the client starts it (`serve
--dev-agent`), which suits an agent on your own machine. Anywhere else, a human or the control
plane issues the token and passes it in `LOKRA_TOKEN`:

```bash
claude mcp add clinic-db \
  -e LOKRA_TOKEN="$(lokra mint-token scheduling-bot --ttl 28800)" \
  -- "$(command -v lokra)" --config "$PWD/policies.yaml" serve
```

For any other MCP client, `lokra connect --print` prints the entry to paste.

Tools the agent sees: `whoami`, `list_tables`, `query`, `propose_write`, `write_status`, `execute_write`.

## Approval rules

By default every write waits for one person. An agent's `approval` block changes that:

```yaml
agents:
  ops-bot:
    write: {orders: [update, delete]}
    approval:
      auto_max_rows: 5          # writes the dry run shows changing 5 rows or fewer run without a person
      auto_statements: [update] # which statements may skip a person (default: insert and update)
      two_people: [delete]      # these need two different people, and are never automatic
```

A write the policy approves comes back from `propose_write` already approved, and the ledger
records it as approved by `policy (up to 5 rows)`. A write that needs two people stays pending
after the first approval, from any channel, until a different person approves it too.

Whoever approved it, a write runs only if it changes no more rows than its dry run showed. If the
data changed in the meantime and it would now touch more, it is rolled back and the agent is told
to propose it again.

## Stop an agent

Always-on agents need brakes that work in seconds, not a policy rewrite:

```bash
lokra pause support-bot --reason "deleting too much"   # refuse its requests, block its DB login, end its sessions
lokra resume support-bot
lokra revoke-token <token or token id>                 # kill one leaked token, keep the agent's others
```

Set `LOKRA_ADMIN_DSN` (or pass `--admin-dsn`) so a pause also blocks the agent's database login
and ends its sessions. Without it, Lokra still refuses every request and says the login is
unchanged.

The dashboard lists every agent with a Pause and Resume button, and `lokra slack` adds a
Pause button to each approval message and posts a notice with a Resume button whenever an agent
is paused, so you can stop an agent from your phone. A paused agent stays paused through
`lokra provision`. To stop a stuck retry loop from
hammering the database, cap its rate in the policy with `rate_limit: 60` (requests a minute);
an agent that keeps hitting its cap is flagged as looping in `lokra report` and the dashboard.
Every pause, resume, revocation and stopped request is written to the ledger.

## Approve writes in Slack

`lokra slack` posts each pending write to a Slack channel, with the agent, the person it acts
for, its reason, the masked SQL and the dry-run row count, plus Approve and Deny buttons. Only
the people you list can decide, and every decision lands in the signed ledger. It connects out
to Slack over Socket Mode, so Lokra needs no public URL.

1. At [api.slack.com/apps](https://api.slack.com/apps), create an app from this manifest and
   install it to your workspace. Copy the bot token (`xoxb-…`).

   ```yaml
   display_information: {name: Lokra}
   features: {bot_user: {display_name: Lokra}}
   oauth_config: {scopes: {bot: [chat:write]}}
   settings: {interactivity: {is_enabled: true}, socket_mode_enabled: true}
   ```

2. Under Basic Information, create an app-level token with the `connections:write` scope
   (`xapp-…`), and invite the bot to your approvals channel.
3. Add the channel ID and the Slack member IDs of your approvers to `policies.yaml`:

   ```yaml
   slack: {channel: C0123ABCD, approvers: [U0123ABCD, U0456EFGH]}
   ```

4. Install the extra and run it next to your policy:

   ```bash
   pipx install 'lokra[slack]'
   LOKRA_SLACK_BOT_TOKEN=xoxb-… LOKRA_SLACK_APP_TOKEN=xapp-… lokra slack --admin-dsn "<admin dsn>"
   ```

   With `--admin-dsn`, pausing from Slack also blocks the agent's database login.

Writes approved or denied in the dashboard or the terminal close their Slack message too.

## Lokra Cloud

[Lokra Cloud](https://cloud.lokra.dev) is one inbox for every database running Lokra: approve
or deny writes, pause and resume agents, and read the activity of every gate, from a laptop or a
phone. Sign in with GitHub, create a gate token under **Gates**, then run this next to the
gate's `policies.yaml`:

```bash
LOKRA_CLOUD_TOKEN=lkg_… lokra cloud
```

`lokra cloud` connects out to Lokra Cloud, so the gate needs no public address. It sends the
agents, pending and recently decided writes, and ledger entries; the SQL in them is already
masked, and query results never leave your network. Decisions made in the Cloud come back to the
gate, which applies them and records who made them, such as `dana (cloud)`. Add
`--admin-dsn "<admin dsn>"` so a pause from the Cloud also blocks the agent's database login.

## Agents on other machines

Always-on and hosted agents can reach Lokra over HTTP. Each request carries the agent's own
short-lived token, so one Lokra serves many agents and every call is checked:

```bash
lokra serve --http --host 0.0.0.0 --port 8787     # put TLS in front of this
lokra mint-token support-bot --ttl 28800          # hand this token to the agent (max 8 hours)
```

```bash
claude mcp add --transport http lokra https://lokra.internal.example/mcp \
  --header "Authorization: Bearer <token>"
```

A request without a valid token is refused and recorded in the ledger.

### In a container

Each release is also published as an image for amd64 and arm64. It runs `lokra serve --http` on
port 8787 with the `policies.yaml` in `/work`, and keeps its state (signing key, agent passwords,
pending writes, ledger) in `/work/.lokra`, so mount a directory that persists:

```bash
docker run -d --name lokra -p 8787:8787 --user "$(id -u):$(id -g)" -v "$PWD:/work" ghcr.io/lokrahq/lokra:0.8.0
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/work" ghcr.io/lokrahq/lokra:0.8.0 mint-token support-bot
```

The database host in the policy has to be reachable from the container, so not `127.0.0.1`. Any
other command works the same way, such as `provision`, `report` or `cloud`.

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
dashboard, an approvals queue where you approve or deny pending writes after
seeing the statement and its dry-run row count, a full audit log, and a policy
editor that rewrites `policies.yaml` and reprovisions the database roles for you.

<p align="center"><img src="docs/dashboard.png" alt="Lokra dashboard: agents, statement outcomes and risk flags" width="820"></p>

The audit log shows every statement, what was masked, why requests were blocked,
and each write from proposal through approval:

<p align="center"><img src="docs/audit.png" alt="Lokra audit log: masking breakdown, blocked reasons and activity feed" width="820"></p>

Flags are rules over the ledger, so each one traces back to its entries. This is
the evidence an auditor asks for, generated from what already happened.

For a review or a vendor questionnaire, `lokra evidence` turns the policy and the
ledger into a control evidence pack: least-privilege grants per agent, masking
rules and what they caught, the write-approval trail, the ledger's integrity
check, and blocked access attempts, mapped to the Australian Privacy Principles,
ISO/IEC 27001:2022 and the ACSC Essential Eight. When the policy handles health
identifiers it adds a section for the Healthcare Identifiers Act, the My Health
Records Act and the Notifiable Data Breaches scheme.

```bash
lokra evidence                       # Markdown control pack to stdout
lokra evidence --out evidence.md     # or write it to a file
lokra evidence --json                # structured, for your own templating
```

## Conformance

`conformance/` is a benchmark of twelve safety guarantees a database access layer
should uphold against a hostile agent (reject stacked statements, no OS command
execution, no cross-tenant reads, identifiers masked, and more). It scores Lokra
and a deliberately broken reference. Lokra upholds all twelve.

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
| Row lock | Rows outside the agent's scope, reads and writes | Postgres RLS policies per agent role |
| Read-only transaction | Writes hidden in CTEs or functions | Postgres |
| Statement timeout | Runaway queries | Postgres role setting |
| Masking | Identifiers in results, free text and JSON; masked columns leaving under another name | Proxy: SQL check and checksum-validated detectors |
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
  provision.py         creates per-agent Postgres roles, grants and row locks
  masking.py           column rules and Medicare/IHI/email/phone detectors
  ledger.py            hash-chained, signed audit log
  gateway.py           the request pipeline and write approvals
  server.py            MCP server
  cli.py               `lokra` command
tests/
```

## Roadmap

- [x] Works against any Postgres, including Supabase (`lokra init` scaffolds the policy)
- [x] Approve, deny and pause from Slack (`lokra slack`)
- [x] Conformance suite: twelve safety guarantees, scored in `conformance/`
- [x] Activity and risk reports from the ledger (`lokra report`, `lokra dashboard`)
- [x] Control evidence pack (`lokra evidence`) mapped to the APPs, ISO/IEC 27001:2022, the Essential Eight, and — for health data — the Healthcare Identifiers Act, My Health Records Act and NDB scheme
- [x] Purpose-of-use on every request: a declared, policy-enforced purpose recorded in the ledger (`require_purpose`, `--purpose`)
- [ ] Deeper mapping to the US HSCC AI vendor questionnaire, question by question
- [x] Hosted control plane: one inbox across gates, pause from a phone, team roles ([Lokra Cloud](https://cloud.lokra.dev))
- [ ] Lokra Cloud: SSO and long ledger retention
- [ ] DuckDB and Snowflake adapters

## License

Apache 2.0. See [LICENSE](LICENSE).
