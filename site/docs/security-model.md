---
title: Security model
description: Each layer between an agent and your data, what it stops, what enforces it, and what Lokra does not protect against.
---
The SQL parser is not the security boundary. Several "read-only" database MCP servers were bypassed in 2025 and 2026 because they relied on parsing SQL text. In Lokra the database itself enforces access, and each layer still holds if the one before it fails.

## Layers

| Layer | Stops | Enforced by |
|---|---|---|
| Signed short-lived token | Unknown, forged, expired or revoked agents | Lokra (HMAC-SHA256) |
| SQL check | Writes in the read tool, stacked statements, DDL, `SET ROLE`, file, network and session functions | Lokra (sqlglot), fails closed |
| Prepared statements only | `COMMIT; DROP ...` style stacking | Postgres protocol |
| Per-agent login role | Tables and columns outside the policy | Postgres grants |
| Row lock | Rows outside the agent's scope, for reads and writes | Postgres row-level security policies on the agent's role |
| Read-only transaction | Writes hidden in CTEs or functions | Postgres |
| Statement timeout | Runaway queries | Postgres role setting |
| Masking | Identifiers in results, free text, JSON and arrays; masked columns leaving under another name | Lokra: SQL check and checksum-validated detectors |
| Write approval | Changes nobody agreed to, and approved writes that grow before they run | Lokra: dry run, approval, row-count check at execution |
| Ledger | Silent edits or deletions of history | SHA-256 hash chain with an HMAC signature per entry |

The integration tests turn the SQL check off on purpose and confirm the database still blocks the attacks.

## Identity

An agent never holds a database password. It holds a token signed with a key in `.lokra/signing.key`, naming the agent, the person it acts for and, if the policy requires one, its declared purpose. Tokens last at most 8 hours. `lokra revoke-token` kills one token; `lokra pause` stops every request from an agent at once. See [stop an agent](/docs/stop-agents).

## The agent's database role

`lokra provision` creates one login role per agent, named `lokra_<agent>`. Each role:

- can log in and nothing else: it is created with `NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS`, and Lokra refuses to run an agent whose role has gained any of those powers;
- has at most 10 connections, a 5-second `statement_timeout` and a 10-second `idle_in_transaction_session_timeout`;
- is granted only the tables, columns and write operations in its policy, and loses grants that leave the policy;
- belongs to the `lokra_agents` group, which Lokra uses to tell agent sessions apart from your app's.

Row locks are permissive and restrictive row-level security policies on the agent's role. When a table had row-level security off, Lokra turns it on with a pass-through policy for every role that is not an agent, so your app sees no change. See [row locks](/docs/row-locks).

## Reads

A read must be one `SELECT`. It runs as a prepared statement in a read-only transaction, so it cannot end the transaction or write through a function. Lokra refuses row locks (`FOR UPDATE`), `SELECT INTO`, and functions that read files, run programs, reach other servers, change session settings, write sequences or run a query given as text. Results are capped at the agent's `max_rows`.

## Masking

Masking runs in Lokra, not in the database, so Lokra also stops queries that would carry a masked column out under another name: through a function, an alias, a `UNION`, a subquery result, a whole-row reference or `row_to_json`. Filtering by a masked column in `WHERE`, `JOIN`, `GROUP BY` or `ORDER BY` still works. Value detectors check card numbers, IBANs and Australian health identifiers with their checksums, so ordinary numbers are left alone. See [masking](/docs/masking).

## Writes

`propose_write` dry-runs the statement in a transaction that is rolled back, records how many rows it would change, and holds it. It runs only after approval: by a person, by two different people for statements that require it, or by an approval rule for small writes. When the agent calls `execute_write`, Lokra rolls the write back if it would now change more rows than were approved. Approvals expire after `approval_ttl_seconds`, one hour by default. See [approve writes](/docs/approvals).

## The ledger

Every request, refusal, approval, pause and execution is appended to `.lokra/ledger.jsonl`. Each entry includes the hash of the previous one and an HMAC signature, so editing or deleting history breaks the chain. SQL is masked before it is written. `lokra verify-ledger` checks the whole chain. See [audit ledger and reports](/docs/audit).

## Local state

The `.lokra` folder holds the signing key, the agent passwords, pending writes and the ledger. Lokra creates it readable only by your user. Treat it like any credential store: add `.lokra/` to your `.gitignore`, back it up, and restrict who can read it on servers.

## Lokra Cloud

A gate connects out to Lokra Cloud over HTTPS, so it needs no public address. Cloud receives agents, pending and recent writes with masked SQL and row counts, and ledger entries without their signatures. It never receives query results. Gate tokens are stored hashed, and phone alerts are encrypted end to end. See [Lokra Cloud](/docs/cloud).

## What Lokra does not protect against

- **Misuse of data an agent may read.** Lokra narrows what that is, masks what it can and records everything, but an agent can still summarise or repeat what it was allowed to see.
- **A compromised gate host.** Anyone who can read `.lokra` can mint tokens and log in as agent roles. Protect the host and the folder.
- **Columns masking does not know about.** Masking matches column names in your policy and the detectors you turn on. `lokra check` warns about sensitive-looking columns with no masking, but it cannot recognise everything.
- **Access outside Lokra.** If an agent also has your app's database password, or a script with its own connection string, Lokra does not see those requests.

## Reporting a vulnerability

Please report security issues privately, as described in [SECURITY.md](https://github.com/lokrahq/lokra/blob/main/SECURITY.md).
