---
title: Audit ledger and reports
description: How Lokra's hash-chained, signed ledger works, and how to read it with lokra log, verify-ledger, report, dashboard and evidence.
---
Every request an agent makes, and every decision a person makes about it, is written to an append-only ledger. Each entry is chained to the one before it by a SHA-256 hash and signed with an HMAC, so a changed or deleted entry is detected. Lokra then reads the ledger back into a per-agent report, a live dashboard and a control evidence pack for reviewers.

| Command | What it gives you |
|---|---|
| `lokra log` | The most recent entries, one per line |
| `lokra verify-ledger` | A check that no entry was changed or removed |
| `lokra report` | Per-agent activity and risk flags, as text or JSON |
| `lokra dashboard` | A local web page with the report, an approvals queue, the full log and a policy editor |
| `lokra evidence` | A control evidence pack in Markdown or JSON, mapped to privacy and security frameworks |

## What the ledger records

The ledger is `.lokra/ledger.jsonl` in Lokra's state directory: one JSON object per line. Lokra writes an entry for:

- every query and `list_tables` call, and every statement refused by Lokra's SQL check or by Postgres,
- every request refused because of its token or purpose, a pause, a revoked token or the rate limit, whatever the tool,
- every write proposed, approved, partly approved, denied, executed or rolled back,
- every pause, resume and token revocation.

Successful `whoami` and `write_status` calls are not recorded, because they only read Lokra's own state. An `execute_write` that Lokra turns down because the write is not approved, or its approval has expired, is recorded as `write_refused`.

### Fields on every entry

| Field | What it holds |
|---|---|
| `seq` | Position in the ledger, starting at 1 |
| `ts` | UTC time, to the millisecond |
| `event` | What happened; see the table below |
| `prev` | The `hash` of the previous entry (64 zeros for the first) |
| `hash` | SHA-256 of this entry's contents, including `prev` |
| `sig` | HMAC-SHA256 of `hash`, keyed with `.lokra/signing.key` |

Entries about an agent's request also carry `agent`, `role` (its database role), `on_behalf_of` (the person on its token), `token_id`, `tool` and, if the token declared one, `purpose`.

### Events

| Event | Written when | Extra fields |
|---|---|---|
| `read` | A query returned rows | `sql`, `tables`, `rows`, `truncated`, `masked`, `ms` |
| `listed_tables` | The agent called `list_tables` | `tables` |
| `rejected` | Lokra's SQL check refused a statement | `sql`, `reason` |
| `db_refused` | Postgres refused a statement, such as a missing grant | `sql`, `error` |
| `denied` | No token, a bad or expired token, a token for an unknown agent, or a missing or unpermitted purpose | `tool`, `reason` |
| `stopped` | The agent was paused, the token revoked, or the rate limit hit | `control`, `reason` |
| `write_proposed` | The agent proposed a write and Lokra dry-ran it | `write_id`, `sql`, `reason`, `would_affect_rows`, `warnings` |
| `write_approved` | A person, or the policy, approved a write | `write_id`, `decided_by` |
| `write_partly_approved` | The first of two required approvals | `write_id`, `decided_by`, `approvals`, `needed` |
| `write_denied` | A person denied a write | `write_id`, `decided_by` |
| `write_executed` | An approved write ran and committed | `write_id`, `approved_by`, `affected_rows` |
| `write_rolled_back` | An approved write would have changed more rows than approved | `write_id`, `approved_rows`, `would_change` |
| `write_refused` | The agent tried to run a write that is not approved, or whose approval expired | `write_id`, `reason` |
| `agent_paused`, `agent_resumed` | Someone paused or resumed an agent | `decided_by`, `reason` |
| `token_revoked` | Someone revoked a token | `token_id`, `decided_by` |

`decided_by` shows where a decision came from: a terminal username such as `dana`, `dana (dashboard)`, `dana (slack)`, a name from Lokra Cloud, or `policy (up to 5 rows)` for a write the [approval rules](/docs/approvals) let through.

Here is a `read` entry:

```json
{"agent": "support-bot", "event": "read", "hash": "ccc879d924437a763ba98d1be2de729204da8f61b1d6bd377dc695316dc12d6d", "masked": {"column:last3": 3, "column:redact": 3}, "ms": 20, "on_behalf_of": "dana", "prev": "0000000000000000000000000000000000000000000000000000000000000000", "role": "lokra_support_bot", "rows": 3, "seq": 1, "sig": "151baf8fcf3c24fb9be021087f38521c5a8e49fefce809c43016b05ac808723e", "sql": "SELECT id, name, email, phone FROM customers ORDER BY id LIMIT 3", "tables": ["customers"], "token_id": "b4476ff04ead5a3a", "tool": "query", "truncated": false, "ts": "2026-11-27T09:17:15.422+00:00"}
```

The ledger never holds query results. `masked` counts what was masked, by rule: `column:<strategy>` for a [column rule](/docs/masking), or the detector's name, such as `email` or `au_phone`, for a value found by a detector.

### SQL is masked before it is written

Agents sometimes put identifiers in the SQL itself, for example in a `WHERE` clause. Before writing a statement to the ledger, Lokra runs the policy's value detectors over it, so this query:

```sql
SELECT id, name FROM customers WHERE email = 'jo@example.com'
```

is recorded as:

```text Ledger
SELECT id, name FROM customers WHERE email = '[EMAIL]'
```

Only values a configured detector recognises are replaced. If the policy has no `detectors`, SQL is recorded as written. The same masked SQL is what Slack, the dashboard and Lokra Cloud show.

### What the chain protects

`lokra verify-ledger` recomputes every hash and signature in order. It detects an entry whose contents were changed, an entry removed or inserted in the middle, and an entry signed with a different key.

It cannot detect two things on its own:

- **Entries removed from the end.** A shortened ledger is still a valid chain. Keep a copy elsewhere as entries happen, for example by shipping the file to your log platform or connecting [Lokra Cloud](/docs/cloud), which receives each entry as it is written.
- **Someone holding the signing key.** With `.lokra/signing.key`, a person could rebuild and re-sign the whole chain. Keep the key readable only by the user that runs Lokra. See [where state lives](/docs/production#where-state-lives).

## lokra log

Show the most recent entries:

```bash
lokra log -n 5
```

```text
   77 2026-11-27T09:18:58 support-bot        rejected        SELECT pg_read_file('/etc/passwd')
   78 2026-11-27T09:18:59 support-bot        rejected        SELECT * FROM orders; DROP TABLE orders
   79 2026-11-27T09:18:59 support-bot        rejected        SET ROLE postgres
   80 2026-11-27T09:19:49 support-bot        read            SELECT id, name FROM customers WHERE email = '[EMAIL]'
   81 2026-11-27T09:20:02 ops-bot            write_approved  w_4de7597e
```

Each line shows the sequence number, the UTC time, the agent, the event, and the SQL, reason or write id, cut to 90 characters. `-n` sets how many entries to show; the default is 20. For every field, read `.lokra/ledger.jsonl` directly, for example with `jq`.

## lokra verify-ledger

```bash
lokra verify-ledger
```

```text
ok: 82 entries verified
```

If anything is wrong, it names the first bad entry and exits with status 2:

| Message | Meaning |
|---|---|
| `entry 5: contents were modified` | The entry's contents no longer match its hash |
| `entry 3: chain broken (an earlier entry was removed or changed)` | The entry's `prev` does not match the entry before it |
| `entry 1: signature invalid` | The entry was signed with a different key, or the signature was changed |

Run it on a schedule, and before you hand a report or evidence pack to anyone.

## lokra report

`lokra report` turns the ledger into a per-agent activity and risk summary:

```bash
lokra report
```

```text
Lokra activity report — 79 ledger entries, chain verified
  5 access attempt(s) by an unknown or expired agent

ops-bot (for dana)
  reads 18 · rows 20 · masked 0
  writes 6 proposed, 5 approved, 1 denied, 1 executed (3 rows)
  blocked 2 · tables orders
  ⚠ hit its rate limit 20 times, a sign of a retry loop

support-bot (for dana)
  reads 3 · rows 5 · masked 8 (au_phone 2, last3 3, redact 3)
  writes 0 proposed, 0 approved, 0 denied, 0 executed (0 rows)
  blocked 5 · tables customers, orders
  ⚠ 5 blocked statements, a sign of probing or prompt injection

2 agent(s) · 2 risk flag(s)
```

For each agent it shows who it acted for, how many reads and rows, what was masked, its writes, how many statements were blocked (by the SQL check or by Postgres), and the tables it touched. The first lines show whether the chain verified, and how many requests were denied for their token or purpose.

| Flag | What it does |
|---|---|
| `--agent` | Limit the report to one agent. Denied requests are still counted, since most name no agent. |
| `--json` | Print the report as JSON. |

It exits with status 2 if the chain does not verify, so a scheduled job can alert on it.

### Risk flags

Flags are plain rules over the ledger, not a model, so each one traces back to the entries that raised it. They cover the whole ledger, or the one agent with `--agent`.

| Flag | Raised when | Why it matters |
|---|---|---|
| `looping` | The agent hit its [rate limit](/docs/stop-agents#cap-the-request-rate) 3 or more times | A stuck retry loop |
| `probing` | 3 or more of its statements were blocked | Probing, or a prompt injection trying its luck |
| `broad_read` | A single read returned 500 or more rows | An unusually large read |
| `phi_volume` | It saw 50 or more masked identifiers | A lot of sensitive data passed through, even if masked |
| `large_write` | It proposed a write that would change 100 or more rows | A broad change |
| `unscoped_write` | It proposed an `UPDATE` or `DELETE` with no `WHERE` clause | A change to every row the agent can see |

### JSON output

```bash
lokra report --json
```

The top level holds `chain_verified`, `entry_count`, `identity_denials`, `risk_flags` (the total), `events_by_type` (a count per event), `blocked_reasons` (the six most common, each with `reason` and `count`), and `agents`. Each agent has `agent`, `on_behalf_of`, `reads`, `rows_read`, `max_read_rows`, `masked`, `masked_total`, `tables`, `writes_proposed`, `writes_approved`, `writes_denied`, `writes_executed`, `rows_written`, `blocked`, `stopped`, and `flags`, a list of objects with `kind` and `detail`. Use it to feed a SIEM or your own reporting.

## The dashboard

`lokra dashboard` serves a local web page with the same report, refreshed every four seconds, and lets you act on it:

```bash
lokra dashboard --admin-dsn "postgresql://admin:secret@db.internal:5432/app"
```

```text
Lokra dashboard on http://127.0.0.1:8900  (Ctrl+C to stop)
```

| Flag | Default | What it does |
|---|---|---|
| `--port` | `8900` | Port on `127.0.0.1` |
| `--admin-dsn` | `$LOKRA_ADMIN_DSN` | Admin connection string. Needed to apply policy changes, and to block the database login when you pause an agent. |

It has four views:

- **Dashboard**: counts of agents, statements, masked identifiers and risk flags; statement outcomes; the risk flags; every agent with a **Pause** or **Resume** button; and recent activity.
- **Approvals**: each pending write with its agent, the person it acts for, the reason, the masked SQL, the dry-run row count and any warnings, with **Approve** and **Deny** buttons; and recent decisions. Decisions are recorded as your username followed by `(dashboard)`.
- **Audit**: what was masked, the top blocked reasons, and an activity feed, with a link to the full log.
- **Policy**: an editor for agents, read grants, write operations, row locks, maximum rows, purposes, masking rules, detectors, the approval window and whether a purpose is required. Saving rewrites `policies.yaml` and reprovisions the database roles. Removing an agent drops its role.

> **Note:** The policy editor does not show approval rules (`approval`) or rate limits (`rate_limit`); it keeps them as they are when you save. Saving does rewrite `policies.yaml` without YAML comments, so keep comments elsewhere if you rely on them.

The dashboard binds to `127.0.0.1` only and has no login of its own: anyone who can reach that port on the machine can approve writes. Do not put it behind a proxy. It refuses cross-origin requests that try to change anything. After saving a policy in it, restart `lokra serve --http` so the server picks up the change.

## lokra evidence

For a security review or a vendor questionnaire, `lokra evidence` turns the policy and the ledger into a control evidence pack:

```bash
lokra evidence                      # Markdown to stdout
lokra evidence --out evidence.md    # write it to a file
lokra evidence --json               # structured, for your own templates
```

| Flag | What it does |
|---|---|
| `--out` | Write the Markdown pack to this file instead of standard output. |
| `--json` | Print structured JSON instead of Markdown. |

The pack covers the whole ledger and has these sections:

1. **Summary**: agents under policy, reads, blocked statements, writes proposed, approved, denied and executed, identifiers masked, and any purposes declared on requests.
2. **Least-privilege access per agent**: a table of each agent's database role, read grants, write grants and row scope.
3. **Masking of sensitive values**: the column rules, the detectors, and what was masked in the period.
4. **Human approval for changes**: write counts, how many the policy approved, and who approved.
5. **Tamper-evident audit log**: the result of verifying the chain.
6. **Blocked access attempts**: blocked statements, requests with no valid token, and the top reasons.
7. **Framework mapping**: each control mapped to frameworks.

| Lokra control | Privacy Act (APP) | ISO/IEC 27001:2022 | Essential Eight |
|---|---|---|---|
| Least-privilege access per agent | APP 11 | A.8.2, A.8.3 | Restrict administrative privileges |
| Masking of sensitive values | APP 3, APP 11 | A.8.11 Data masking | — |
| Human approval for changes | APP 11 | A.5.15 | — |
| Tamper-evident audit log | APP 1, APP 11 | A.8.15, A.8.16 | — |
| Blocked access attempts | APP 11 | A.5.24–A.5.26, A.8.16 | — |

When requests declared a purpose, the mapping adds **Purpose-of-use enforcement** (APP 6, A.5.15). When the policy masks Australian health identifiers (an `ihi` or `medicare` detector, or a masking rule for a column whose name contains `ihi` or `medicare`), the pack adds a health section covering the Healthcare Identifiers Act 2010, the My Health Records Act 2012 and the Notifiable Data Breaches scheme.

The JSON form holds the same data under `generated`, `database`, `chain_verified`, `health`, `purposes`, `entry_count`, `summary`, `agents`, `masking`, `change_control` and `access_denials`.

> **Note:** The pack is evidence to support an assessment, not a certification. It covers the controls Lokra runs; the rest of each framework, such as your hosting and backups, is up to your environment. Run `lokra verify-ledger` before you send it.
