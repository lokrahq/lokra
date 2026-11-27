---
title: Write a policy
description: Build a policies.yaml step by step, from the database block to row locks, masking, approval rules and Slack.
---
A policy is one YAML file, `policies.yaml`, that says which agents exist and what each one may read, change and see unmasked. This page builds one from scratch for a typical SaaS database, one step at a time, and ends with the complete file. For every key, type and default in one place, see the [policy file reference](/docs/policy-reference).

## The example database

The example is a multi-tenant app. Every customer-facing table has an `account_id` column that says which customer account a row belongs to:

| Table | Columns |
|---|---|
| `accounts` | `id`, `name`, `plan`, `stripe_customer` |
| `customers` | `id`, `account_id`, `name`, `email`, `phone`, `created_at` |
| `orders` | `id`, `account_id`, `customer_id`, `total`, `status`, `card_number`, `created_at` |
| `notes` | `id`, `account_id`, `customer_id`, `body`, `created_at` |
| `products` | `id`, `name`, `price` (shared by every account) |

You will write two agents:

- **`support-bot`** answers questions for one account, Acme (`account_id` 42), and can add notes.
- **`ops-bot`** updates order status across all accounts. Deleting an order needs two people.

> **Tip:** You do not have to start from an empty file. `lokra init` scaffolds a policy from your schema, and `policies.example.yaml` in the repository is a non-healthcare starting point. See [connect your database](/docs/connect-database#scaffold-a-policy-with-lokra-init).

## 1. Point at the database

```yaml
database:
  host: db.example.com
  port: 5432
  dbname: app
```

This is where agents log in, each as its own Postgres role. There is no user name or password here: agent passwords are created by `lokra provision` and kept in `.lokra/secrets.json`, and the admin login is passed separately with `--admin-dsn`.

Always set all three keys. The defaults (`127.0.0.1`, `55432`, `clinic`) are Lokra's development lab, not your database.

## 2. Add an agent

```yaml
agents:
  support-bot:
    description: Answers customer questions for the Acme account. Can add notes.
```

The key under `agents` is the agent's name. You use it with `lokra connect`, `lokra mint-token` and `lokra pause`, and it decides the database role: `support-bot` becomes `lokra_support_bot`. The description shows in the agent's `whoami` tool, the dashboard and Lokra Cloud, so write it for the people who will approve its writes.

## 3. Choose what it can read

`read` maps each table to the columns the agent may select. Use `"*"` for every column, or a list:

```yaml
    read:
      accounts: [id, name, plan]
      customers: [id, account_id, name, email, phone, created_at]
      orders: "*"
      notes: "*"
      products: "*"
```

`lokra provision` turns this into Postgres grants, so the database enforces it. Columns you leave out do not exist as far as the agent is concerned. Here `support-bot` cannot see `accounts.stripe_customer`.

Two things to know about column lists:

- `SELECT *` fails on a table where the agent only has some columns, with `permission denied for table customers`. Agents call `list_tables` first and name the columns they can read.
- `"*"` grants the whole table, including columns added later. A column list stays fixed until you change it.

Tables outside the `public` schema are written `schema.table`, for example `billing.invoices: "*"`.

## 4. Let it change data

`write` maps each table to the statements the agent may propose: `insert`, `update` and `delete`.

```yaml
    write:
      notes: [insert]
```

Writes never run straight away. Each one is dry-run and waits for approval unless an [approval rule](#9-decide-which-writes-need-a-person) lets it through. See [approve writes](/docs/approvals).

Give the agent read access to any table it writes, too. Postgres needs `SELECT` on the columns an `UPDATE` or `DELETE` filters on, and the agent needs to read rows to know what to change. `lokra check` warns about a table an agent can write but not read.

## 5. Lock it to one account's rows

```yaml
    rows:
      column: account_id
      values: [42]
      tables: {accounts: id}
```

With a `rows` rule, the agent only sees, inserts and updates rows whose `account_id` is 42, on every table it can reach that has an `account_id` column. The `accounts` table names the same thing `id`, so `tables` maps it. `products` has no `account_id`, so the agent sees all of it, which is what you want for a shared catalogue; `lokra check` warns about it so you notice.

The database enforces this with row-level security policies on the agent's role. [Row locks](/docs/row-locks) explains how, and what happens with views.

## 6. Cap how much it reads at once

```yaml
    max_rows: 200
```

A query returns at most `max_rows` rows; the rest are cut off and the result says `"truncated": true`. The default is 200. This keeps a careless `SELECT` from pulling a whole table into the agent's context.

## 7. Limit how fast it can go

```yaml
    rate_limit: 60
```

`rate_limit` caps the agent at that many requests a minute, across all of its tokens. Every tool call counts, including `list_tables` and `write_status`. When the agent hits the cap, Lokra refuses with `stopped: rate limit of 60 requests a minute reached; wait before retrying`. Leave it out, or set `0`, for no limit.

An agent that keeps hitting its cap is flagged as looping in `lokra report` and the dashboard. See [stop agents](/docs/stop-agents).

## 8. Require a purpose

A token can carry a declared purpose of use, and every request records it in the ledger. Two keys control it:

```yaml
require_purpose: true

agents:
  support-bot:
    purposes: [customer-support]
```

- `require_purpose` (top level) refuses any request whose token has no purpose.
- `purposes` (per agent) lists the purposes that agent may declare. A token with any other purpose is refused. With no list, any purpose is accepted.

The purpose goes on the token when it is issued:

```bash
lokra connect support-bot --purpose customer-support
lokra mint-token support-bot --purpose customer-support --ttl 3600
```

## 9. Decide which writes need a person

By default every write waits for one person to approve it. The `approval` block changes that per agent:

```yaml
    approval:
      auto_max_rows: 1
      auto_statements: [insert]
```

Now a note that the dry run shows adding one row is approved by the policy, and the agent can run it at once. Anything bigger, and any `update`, still waits for a person.

| Key | Default | Meaning |
|---|---|---|
| `auto_max_rows` | not set | Writes whose dry run changes this many rows or fewer are approved by policy. Not set means every write waits for a person. |
| `auto_statements` | `[insert, update]` | Which statements `auto_max_rows` applies to. |
| `two_people` | `[]` | Statements that need two different people, and are never approved by policy. |

[Approve writes](/docs/approvals#approval-rules) covers how the rules combine and how two-person approval works.

## 10. Set how long an approval lasts

```yaml
approval_ttl_seconds: 900
```

Once a write is approved, the agent has this many seconds to call `execute_write`. After that the approval expires and the agent has to propose it again. The default is 3600 (one hour). A shorter window means an approval cannot be used long after the data it was based on has changed.

## 11. Mask sensitive values

`masking` sits at the top level and applies to every agent:

```yaml
masking:
  columns:
    email: redact
    phone: last3
    card_number: last4
  detectors: [card, email, phone, au_phone]
```

- `columns` masks any result column with that name, in any table, case-insensitively. `email` becomes `[REDACTED]`, `phone` keeps its last three digits.
- `detectors` scan every text value in every result, including free-text notes, JSON and arrays, and replace what they find, such as `[CARD]` or `[EMAIL]`.

A masked column can only be selected as itself, so an agent cannot slip it out through a function, an alias or `row_to_json`. [Masking](/docs/masking) lists every strategy and detector, and what gets refused.

## 12. Send approvals to Slack

```yaml
slack:
  channel: C0123ABCD
  approvers: [U0123ABCD, U0456EFGH]
```

`lokra slack` posts each pending write to this channel with Approve and Deny buttons. Only the listed Slack member IDs can decide. [Approve in Slack](/docs/slack) walks through creating the Slack app.

## The complete policy

Here is the whole file, with the second agent added. `ops-bot` has no row lock, reads only the order columns it needs, can update up to five orders without a person, and needs two people for any delete.

```yaml
database:
  host: db.example.com
  port: 5432
  dbname: app

require_purpose: true

agents:
  support-bot:
    description: Answers customer questions for the Acme account. Can add notes.
    rows:
      column: account_id
      values: [42]
      tables: {accounts: id}
    read:
      accounts: [id, name, plan]
      customers: [id, account_id, name, email, phone, created_at]
      orders: "*"
      notes: "*"
      products: "*"
    write:
      notes: [insert]
    max_rows: 200
    rate_limit: 60
    purposes: [customer-support]
    approval:
      auto_max_rows: 1
      auto_statements: [insert]

  ops-bot:
    description: Updates order status across all accounts. Deletes need two people.
    read:
      orders: [id, account_id, status, total, created_at]
    write:
      orders: [update, delete]
    max_rows: 500
    rate_limit: 120
    purposes: [order-fulfilment]
    approval:
      auto_max_rows: 5
      auto_statements: [update]
      two_people: [delete]

masking:
  columns:
    email: redact
    phone: last3
    card_number: last4
  detectors: [card, email, phone, au_phone]

approval_ttl_seconds: 900

slack:
  channel: C0123ABCD
  approvers: [U0123ABCD, U0456EFGH]
```

## Apply it

Check the policy against the database:

```bash
lokra check --dsn "$LOKRA_ADMIN_DSN"
```

```text
  WARN   support-bot: sees every row of 'products', which has no 'account_id' column to lock rows by

0 error(s), 1 warning(s)
```

The warning is expected here, because `products` is shared. Then provision the roles:

```bash
lokra provision --admin-dsn "$LOKRA_ADMIN_DSN"
```

Run `provision` again after every change to the policy; it updates grants and row locks in place. See [connect your database](/docs/connect-database#provision-the-agent-roles) for what it creates.

Then try each agent from the terminal before you connect a client:

```bash
lokra try support-bot "SELECT id, name, email, phone FROM customers" --purpose customer-support
```

```text
{
  "columns": ["id", "name", "email", "phone"],
  "rows": [
    [1, "Ana Lima", "[REDACTED]", "********678"],
    [2, "Ben Okafor", "[REDACTED]", "*******222"]
  ],
  "row_count": 2,
  "truncated": false,
  "masked": {"column:redact": 2, "column:last3": 2}
}
```

Only Acme's two customers come back, with emails and phone numbers masked. When it looks right, [connect an AI client](/docs/connect-clients).
