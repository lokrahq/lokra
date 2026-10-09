# Database access conformance

A benchmark of safety guarantees that a scoped or read-only database access
layer should uphold when an AI agent sends it SQL. Each probe sends one payload
through the layer's read entry point, the way a prompt-injected agent would, and
checks that the layer **refuses or neutralises** it.

It scores Lokra, and a deliberately broken reference ("a single powerful
database connection filtered by a SQL keyword check", the pattern behind the
2025-26 read-only bypass advisories) so the contrast is visible.

## Safety and responsible use

- **Run it only against a database access layer you operate yourself, pointed at
  a disposable lab database.** This repo's lab (`docker compose up -d`) is built
  for exactly that.
- **The payloads are benign in effect.** Destructive ones touch a throwaway
  `conformance_canary` table that is recreated before every probe. The
  command-execution probe runs the no-op `true`. The file-read probe reads
  `/etc/hostname`. Nothing targets real data.
- **If you run this against another project's server and find a failure, report
  it privately to that project's maintainers first** (their `SECURITY.md` or
  security contact), give them time to fix it, and only then publish. Do not run
  it against systems you do not operate.

## Run

```bash
docker compose up -d        # the lab database
pip install -e ".[dev]"
python -m conformance.run                                   # scorecard
python -m conformance.run --markdown results/scorecard.md   # also write Markdown
python -m conformance.run --json                            # machine-readable
```

## The guarantees

| # | Guarantee | Class |
|---|---|---|
| 1 | Reject more than one statement in a single call | CWE-89 |
| 2 | A read must not hide a write in a CTE | CWE-89 |
| 3 | SELECT must not create or write a table | CWE-89 |
| 4 | Must not run operating-system commands (`COPY ... TO PROGRAM`) | CWE-78 |
| 5 | Must not read files from the database host | CWE-73 |
| 6 | Must not switch to another database role | CWE-269 |
| 7 | A read must not take write locks (`FOR UPDATE`) | CWE-667 |
| 8 | An agent must not read another tenant's rows | CWE-639 |
| 9 | Sensitive identifiers must be masked in results | CWE-200 |

## Why these guarantees: shipped servers have failed them

These are not hypothetical. Each class has a public advisory where a released
database MCP server failed it:

- **Command execution / read-only bypass (4):** AWS `postgres-mcp-server`,
  CVE-2026-87911 (CVSS 9.6, 9 Sep 2026): `COPY ... TO PROGRAM` reached OS command
  execution through the read-only path.
  <https://github.com/awslabs/mcp/security/advisories/GHSA-fph8-pg5w-78fv>
- **Stacked / hidden writes (1-2):** the archived reference Postgres MCP server's
  read-only mode was escaped with a `COMMIT; ...` payload (Datadog Security Labs,
  Aug 2025).
- **Cross-tenant read (8):** the Supabase MCP server could be driven, via a
  prompt-injected support ticket, to read another tenant's private table using a
  broad `service_role` key (General Analysis, Jul 2025).

## How Lokra upholds them

Lokra does not rely on the SQL keyword check as its boundary. Each agent connects
as its own Postgres role, so grants, row-level security and a read-only
transaction decide what is possible. The keyword check only routes reads and
writes and gives clear errors; if it were bypassed, the database still refuses.

## Add another layer

`conformance/adapters.py` defines a small `Adapter` with one method,
`read(sql) -> Outcome`. To score another server, write an adapter that sends the
SQL through its read tool (for an MCP server, through an MCP client) and reports
whether it was refused. Keep to the safety rules above.
