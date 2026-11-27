---
title: Policy file reference
description: Every key in policies.yaml, with its type, default and meaning, and the validation errors Lokra reports.
---
This page lists every key Lokra reads from `policies.yaml`. For a guided walkthrough that builds a policy step by step, see [write a policy](/docs/policies).

## Where the file lives

Lokra looks for the policy in this order:

1. `--config /path/to/policies.yaml` (a global option, so it goes before the command: `lokra --config ./policies.yaml check`),
2. the `LOKRA_CONFIG` environment variable,
3. `policies.yaml` in the current folder.

Lokra keeps its state in a folder next to the policy called `.lokra`, unless you set `--home` or `LOKRA_HOME`. It holds the signing key for agent tokens (`signing.key`), agent database passwords (`secrets.json`), pending writes (`pending/`), the audit ledger (`ledger.jsonl`) and pause and rate-limit state (`controls.json`). Keep it private and keep it with the policy: every Lokra process that serves the same agents must use the same home. See [environment variables](/docs/environment).

## Top-level keys

| Key | Type | Default | Meaning |
|---|---|---|---|
| `database` | mapping | see [database](#database) | Where agents log in. |
| `agents` | mapping of agent name to [agent settings](#agent-keys) | none | The agents Lokra serves. |
| `masking` | mapping | no masking | Column rules and value detectors, applied to every agent. See [masking](#masking). |
| `approval_ttl_seconds` | whole number | `3600` | Seconds an approved write stays executable, counted from its final approval. After that it expires. |
| `require_purpose` | true or false | `false` | When true, every request's token must carry a declared purpose of use, or it is refused. |
| `slack` | mapping | none | Channel and approvers for `lokra slack`. See [slack](#slack). |

An agent setting Lokra does not recognise, such as a typo like `max_row`, is an error, and so is a masking strategy it does not know. Unknown top-level keys are ignored. See [validation errors](#validation-errors).

## database

```yaml
database:
  host: db.example.com
  port: 5432
  dbname: app
```

| Key | Type | Default | Meaning |
|---|---|---|---|
| `host` | string | `127.0.0.1` | Host agents connect to. It must be reachable from where Lokra runs. |
| `port` | whole number | `55432` | Port agents connect to. |
| `dbname` | string | `clinic` | Database agents connect to. The admin connection for `provision`, `check` and `pause` must be to this same database. |

> **Warning:** The defaults point at Lokra's development lab (`127.0.0.1:55432/clinic`), not at a standard Postgres. Always set all three. When the policy does point at the lab, commands that need an admin connection fall back to the lab's admin login.

There is no user name or password here. Agent passwords are created by `lokra provision` and stored in `.lokra/secrets.json`; the admin login is passed with `--admin-dsn` or `LOKRA_ADMIN_DSN`. TLS follows libpq's settings, such as `PGSSLMODE`. See [encrypted connections](/docs/connect-database#encrypted-connections).

## Agent keys

Each key under `agents` is an agent's name. The agent's Postgres role is `lokra_` followed by the name in lower case, with every run of characters other than letters and digits replaced by `_`. `support-bot` becomes `lokra_support_bot`.

> **Note:** Names that differ only in case or punctuation, such as `support-bot` and `Support_Bot`, map to the same role. Give each agent a distinct name.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `description` | string | empty | What the agent is for. Shown by its `whoami` tool, in the dashboard and in Lokra Cloud. |
| `read` | mapping of table to `"*"` or a list of columns | none | Tables and columns the agent may `SELECT`. `"*"` grants the whole table, including columns added later. |
| `write` | mapping of table to a list of `insert`, `update`, `delete` | none | Statements the agent may propose on each table. Every write goes through [approval](/docs/approvals). |
| `rows` | mapping | none (all rows) | A [row lock](#rows). |
| `max_rows` | whole number | `200` | Most rows one query returns. Extra rows are cut off and the result says `"truncated": true`. |
| `rate_limit` | whole number | `0` (no limit) | Most requests the agent may make in any 60 seconds, across all its tokens and every Lokra process sharing the same home. Every tool call counts. |
| `purposes` | list of strings | none (any purpose) | Purposes of use the agent's tokens may declare. A token with any other purpose is refused. Matching is exact. |
| `approval` | mapping | every write waits for one person | [Approval rules](#approval). |

### Table names

Write a table in the `public` schema by its name (`customers`) and any other table as `schema.table` (`billing.invoices`). `public.customers` is the same as `customers`. The same form is used in `read`, `write` and `rows.tables`.

Columns in `read` must be a YAML list, such as `[id, name, email]`. A comma-separated string such as `"id, name"` is not split into columns. Column names are matched exactly as Postgres stores them, so a camelCase column is written `phoneNumber`.

### rows

```yaml
    rows:
      column: account_id
      values: [42]
      tables: {accounts: id}
```

| Key | Type | Default | Meaning |
|---|---|---|---|
| `column` | string | required | The column that holds each row's scope. |
| `values` | list of numbers or strings, or a single value | required, at least one | The agent sees only rows whose column holds one of these. |
| `tables` | mapping of table to column | none | Tables that name the scope column differently. |

The lock applies to every table in the agent's `read` or `write` that has the column. See [row locks](/docs/row-locks).

### approval

```yaml
    approval:
      auto_max_rows: 5
      auto_statements: [update]
      two_people: [delete]
```

| Key | Type | Default | Meaning |
|---|---|---|---|
| `auto_max_rows` | whole number, 0 or more | not set | Writes whose dry run changes this many rows or fewer are approved by policy. Not set means every write needs a person. |
| `auto_statements` | list of `insert`, `update`, `delete`, or a single one | `[insert, update]` | Statements `auto_max_rows` applies to. |
| `two_people` | list of `insert`, `update`, `delete`, or a single one | none | Statements that need two different people. They are never approved by policy. |

Statement names are not case-sensitive. See [approval rules](/docs/approvals#approval-rules).

## masking

```yaml
masking:
  columns:
    email: redact
    phone: last3
  detectors: [card, email, phone, au_phone]
```

| Key | Type | Default | Meaning |
|---|---|---|---|
| `columns` | mapping of column name to strategy | none | Masks any result column with that name, in any table, and any JSON key with that name. Names are not case-sensitive. |
| `detectors` | list of detector names | none | Detectors that scan every text value in every result. |

| Strategies | Detectors |
|---|---|
| `redact`, `last4`, `last3`, `last2` (any `lastN` with N of 1 or more), `year_only`, `hash`, `none` | `ihi`, `medicare`, `card`, `iban`, `us_ssn`, `email`, `au_phone`, `phone` |

An unknown strategy name stops the policy loading with an error, and so does `last0`, which would show the whole value. An unknown detector name is an error when an agent next makes a request. [Masking](/docs/masking) explains each one.

## slack

```yaml
slack:
  channel: C0123ABCD
  approvers: [U0123ABCD, U0456EFGH]
```

| Key | Type | Default | Meaning |
|---|---|---|---|
| `channel` | string | none | Slack channel ID that `lokra slack` posts pending writes to. |
| `approvers` | list of strings | none | Slack member IDs allowed to approve, deny, pause and resume. |

Both are required by `lokra slack` and ignored by every other command. See [approve in Slack](/docs/slack).

## A complete example

```yaml
database:
  host: db.example.com
  port: 5432
  dbname: app

require_purpose: true

agents:
  support-bot:
    description: Answers customer questions for the Acme account. Can add notes.
    rows: {column: account_id, values: [42], tables: {accounts: id}}
    read:
      accounts: [id, name, plan]
      customers: [id, account_id, name, email, phone, created_at]
      orders: "*"
      notes: "*"
    write:
      notes: [insert]
    max_rows: 200
    rate_limit: 60
    purposes: [customer-support]
    approval: {auto_max_rows: 1, auto_statements: [insert]}

masking:
  columns: {email: redact, phone: last3, card_number: last4}
  detectors: [card, email, phone, au_phone]

approval_ttl_seconds: 900

slack: {channel: C0123ABCD, approvers: [U0123ABCD]}
```

> **Note:** The dashboard's policy editor rewrites `policies.yaml` when you save. It keeps settings it does not show, such as `approval` and `rate_limit`, but not YAML comments.

## Validation errors

Lokra checks the file each time a command loads it, and prints the first problem it finds after `error:`.

| Error | Cause |
|---|---|
| `config not found: /path/to/policies.yaml` | No policy at that path. Run the command from the folder that holds it, or pass `--config`. |
| `table name 'a.b.c' should be 'table' or 'schema.table'` | A table name with more than one dot, or an empty part. |
| `support-bot: rows needs a column and at least one value, e.g. rows: {column: org_id, values: [42]}` | `rows` is missing `column` or `values`, has an empty list, or is not a mapping. |
| `support-bot: row values must be numbers or strings` | A value in `rows.values` is `true`, `false`, a decimal or a nested list. |
| `support-bot: rows.tables maps a table to the column that holds its scope` | `rows.tables` is a list instead of a mapping. |
| `support-bot: clinic_ids was replaced by rows, e.g. rows: {column: clinic_id, values: [1, 2], tables: {clinics: id}}` | A policy from an early version of Lokra. Replace `clinic_ids` with the `rows` rule shown. |
| `support-bot: unknown setting max_row; an agent can have description, rows, read, write, max_rows, purposes, rate_limit, approval` | A key under an agent that Lokra does not know, usually a typo. |
| `support-bot: an agent should be a set of settings, e.g. read: {orders: '*'}` | The agent's value is a list or a string instead of a mapping. |
| `support-bot: read orders should be "*" or a list of columns, e.g. [id, name]` | A `read` value that is a comma-separated string, an empty list or something else. Write `[id, name]`, not `"id, name"`. |
| `support-bot: write orders should list operations from insert, update and delete, e.g. [update]` | A `write` value that is a single word, an empty list or an unknown operation. |
| `support-bot: read and write should map tables to columns or operations, e.g. read: {orders: '*'}` | `read` or `write` is a list instead of a mapping. |
| `support-bot: max_rows should be a whole number, 1 or more` | `max_rows` is 0, negative, a decimal or text. `rate_limit` has the same check, with 0 meaning no limit. |
| `masking rule for card: unknown strategy 'last0'; use redact, last4 (any last1 or more), year_only, hash or none` | A masking strategy Lokra does not know. `last0` is refused because it would show the whole value. |
| `agents ops-bot and ops_bot would share the database role lokra_ops_bot; rename one` | Two agent names that differ only in case or punctuation. |
| `support-bot: approval should look like approval: {auto_max_rows: 5, auto_statements: [insert, update], two_people: [delete]}` | `approval` is not a mapping. |
| `support-bot: unknown approval setting auto; it should look like approval: {…}` | A key under `approval` other than the three above. |
| `support-bot: approval auto_max_rows should be a whole number of rows, 0 or more` | `auto_max_rows` is negative, a decimal, text or `true`. |
| `support-bot: approval two_people can only list insert, update and delete` | Another statement name in `auto_statements` or `two_people`. The message names the key. |
| `slack should look like: slack: {channel: C0123ABCD, approvers: [U0123ABCD]}` | `slack` is not a mapping. |
| `invalid literal for int() with base 10: 'lots'` | `port` or `approval_ttl_seconds` holds text. |

Some problems only show up when a command uses that part of the policy:

| Error | When |
|---|---|
| `unknown agent 'support-bot'` | A command or token names an agent that is not in the policy. |
| `tables in the policy that do not exist: billing.invoice` | `lokra provision`, before it changes anything. `lokra check` reports each one separately. |
| `unknown detectors: ['phone_au']` | Any agent request, and `lokra slack` and `lokra cloud`, when `masking.detectors` has a name that does not exist. |
| `add the channel to policies.yaml: slack: {channel: C0123ABCD, approvers: [U0123ABCD]}` | `lokra slack` with no `slack.channel`. |
| `list who may approve: slack: {approvers: [U0123ABCD]} (Slack member IDs)` | `lokra slack` with no `slack.approvers`. |

Views that would read past a row lock are refused by `provision` and reported by `check`; see [row locks](/docs/row-locks#views). For errors from the database itself, see [troubleshooting](/docs/troubleshooting).
