---
title: MCP tools reference
description: Every tool an agent sees through Lokra, with its inputs, what it returns, the errors it can raise, and the instructions the server gives the agent.
---
Lokra gives every agent the same six MCP tools, whichever client it uses and whether it connects over stdio or HTTP. What the agent can do with them is decided by its policy and enforced by the database: the tools are the same, the tables and rows behind them are not.

| Tool | Inputs | What it does |
|---|---|---|
| [`whoami`](#whoami) | none | Shows the agent identity, the person it acts for and when its token expires |
| [`list_tables`](#list_tables) | none | Lists the tables and columns the agent may read or write |
| [`query`](#query) | `sql` | Runs one read-only `SELECT` |
| [`propose_write`](#propose_write) | `sql`, `reason` | Dry-runs one `INSERT`, `UPDATE` or `DELETE` and asks for approval |
| [`write_status`](#write_status) | `write_id` | Shows whether a proposed write is pending, approved, denied or executed |
| [`execute_write`](#execute_write) | `write_id` | Runs an approved write |

## Server instructions

When a client connects, Lokra sends these instructions, which most clients pass to the model:

```text
You are connected to a database through Lokra, a policy proxy. Call list_tables first to see what you may read. Use query for one SELECT at a time. Rows outside your scope are invisible, and sensitive values are masked. To change data, call propose_write. It returns status approved when the policy allows small writes to run without a person; otherwise a human (or two, for some statements) must approve it before execute_write will run. Check write_status, then call execute_write.
```

## Results and errors

Every tool returns a JSON object, sent as the text of the tool result. The field names below are exactly what the agent sees.

When a call fails, the tool result is marked as an error and its text explains why, in a form the agent can act on:

```text
Error executing tool query: send exactly one SQL statement per call
```

Before any tool does its work, Lokra checks the agent's token, its purpose, whether it is paused, whether the token was revoked, and its rate limit. These errors can come from any tool:

| Error | Cause |
|---|---|
| `access denied: no agent token supplied (LOKRA_TOKEN, or an Authorization: Bearer header over HTTP)` | No token |
| `access denied: malformed token` | Not a Lokra token |
| `access denied: token signature is invalid` | Altered, or signed by another Lokra |
| `access denied: token has expired, mint a new one` | Older than its TTL |
| `access denied: unknown agent 'billing-bot'` | The agent is not in this policy |
| `access denied: a declared purpose-of-use is required for this request` | The policy sets `require_purpose` and the token has none |
| `access denied: purpose 'marketing' is not permitted for support-bot` | The purpose is not in the agent's `purposes` |
| `stopped: support-bot is paused by dana: deleting too much` | The agent is [paused](/docs/stop-agents) |
| `stopped: this token was revoked; ask for a new one` | The token was [revoked](/docs/stop-agents#revoke-one-token) |
| `stopped: rate limit of 60 requests a minute reached; wait before retrying` | The agent hit its [rate limit](/docs/stop-agents#cap-the-request-rate) |

Every call counts towards the rate limit, including `whoami` and `write_status`, and every refusal above is written to the [audit ledger](/docs/audit).

## whoami

Shows which identity the agent has and when its access ends. It takes no inputs.

```json
{
  "agent": "support-bot",
  "description": "Answers customer questions for one account. Read only.",
  "on_behalf_of": "dana",
  "purpose": "customer-support",
  "expires_in_seconds": 27841,
  "max_rows": 200
}
```

| Field | Meaning |
|---|---|
| `agent` | The agent name from the policy |
| `description` | The agent's `description` from the policy |
| `on_behalf_of` | The person on the token |
| `purpose` | The declared purpose, or `null` |
| `expires_in_seconds` | Seconds until the token expires |
| `max_rows` | The most rows a single `query` returns |

## list_tables

Lists what the agent may touch. It takes no inputs. The server instructions ask the agent to call it first.

```json
{
  "tables": {
    "customers": {
      "read": ["account_id", "created_at", "email", "id", "name", "phone"],
      "write": []
    },
    "orders": {
      "read": ["account_id", "created_at", "customer_id", "id", "notes", "status", "total"],
      "write": ["account_id", "created_at", "customer_id", "id", "notes", "status", "total"]
    }
  },
  "note": "You only see rows where account_id in [42]. Some values are masked."
}
```

| Field | Meaning |
|---|---|
| `tables` | Each table the agent's database role holds privileges on, keyed by name. Tables outside `public` are named `schema.table`. |
| `tables.<name>.read` | Columns the agent may select, in alphabetical order |
| `tables.<name>.write` | Columns the agent may insert into or update. `DELETE` applies to whole rows, so it does not show here. |
| `note` | The agent's row lock, if it has one, and a reminder that values may be masked |

The list comes from the database's own record of the role's privileges, so it shows what Postgres will actually allow rather than what the policy file says.

## query

Runs one read-only `SELECT` as the agent's own database role.

| Input | Type | Meaning |
|---|---|---|
| `sql` | string | Exactly one `SELECT` statement |

```json
{
  "columns": ["id", "name", "email", "phone"],
  "rows": [
    [2, "Customer 2", "[REDACTED]", "********134"],
    [4, "Customer 4", "[REDACTED]", "********268"]
  ],
  "row_count": 2,
  "truncated": false,
  "masked": {"column:redact": 2, "column:last3": 2}
}
```

| Field | Meaning |
|---|---|
| `columns` | Column names, in order |
| `rows` | One array per row. Dates and times are ISO 8601 strings, `numeric` values are strings, and binary values are `"<binary>"`. |
| `row_count` | How many rows came back |
| `truncated` | `true` if the query matched more rows than the agent's `max_rows`, and the rest were cut off |
| `masked` | How many values were masked, by rule: `column:<strategy>` for a column rule, or a detector name such as `email` or `au_phone` for a value found in free text |

The query runs in a read-only transaction with the row lock and a 5-second statement timeout applied by the database. Rows outside the agent's scope do not appear at all. See [masking](/docs/masking) and [row locks](/docs/row-locks).

### Query errors

Lokra's SQL check refuses these before they reach the database:

| Error | Cause |
|---|---|
| `send exactly one SQL statement per call` | More than one statement, such as `SELECT 1; DROP TABLE orders` |
| `this is a write; use propose_write so a human can approve it` | An `INSERT`, `UPDATE` or `DELETE` |
| `only SELECT queries are allowed here (got COPY)` | Anything else that is not a `SELECT`: `COPY`, `CREATE`, `BEGIN` (`got TRANSACTION`), `SET ROLE` or `SHOW` (`got COMMAND`) |
| `INTO is not allowed in a read query` | `SELECT ... INTO` |
| `LOCK is not allowed in a read query` | `FOR UPDATE` or `FOR SHARE` |
| `data-modifying statements inside a SELECT (for example in a WITH clause) are not allowed` | A write hidden in a CTE |
| `the function pg_read_file() is not allowed` | A function that reads files, runs programs, changes the session or sleeps, such as `pg_read_file`, `set_config` or `pg_sleep` |
| `the masked column email can only be selected as itself, or used in WHERE, JOIN, GROUP BY or ORDER BY; ...` | A masked column inside a function, alias, `UNION` or subquery result |
| `c refers to a whole row, which would carry masked columns; select the columns you need` | A whole row, as in `row_to_json(c)`, when the table has masked columns |
| `empty query`, `could not parse SQL safely: ...` | Nothing to run, or SQL Lokra cannot parse |

Anything else goes to Postgres, which has the final say. Its refusals come back as `database refused the query: <message>`, for example `database refused the query: permission denied for table invoices` for a table outside the policy, or `database refused the query: canceling statement due to statement timeout` for a query that ran longer than 5 seconds.

## propose_write

Proposes one `INSERT`, `UPDATE` or `DELETE`. Lokra runs it as the agent's role inside a transaction, counts the rows it changes, and rolls it back. Nothing changes until the write is approved and executed.

| Input | Type | Meaning |
|---|---|---|
| `sql` | string | Exactly one `INSERT`, `UPDATE` or `DELETE` |
| `reason` | string | Why the agent wants this change, shown to whoever approves it |

```json
{
  "write_id": "w_4de7597e",
  "status": "pending",
  "would_affect_rows": 3,
  "approvals_needed": 1,
  "warnings": [],
  "next_step": "A human must approve this with `lokra approve w_4de7597e`. Then call execute_write with this write_id."
}
```

| Field | Meaning |
|---|---|
| `write_id` | The id to pass to `write_status` and `execute_write` |
| `status` | `pending`, or `approved` if the policy approved it on its own |
| `would_affect_rows` | Rows the dry run changed |
| `approvals_needed` | `0` if already approved, `1` for one person, `2` for two different people |
| `warnings` | Anything worth a second look; see below |
| `next_step` | What the agent should do next, in plain words |

When the agent's [approval rules](/docs/approvals) let a small write through, the same call comes back already approved:

```json
{
  "write_id": "w_0429f71d",
  "status": "approved",
  "would_affect_rows": 1,
  "approvals_needed": 0,
  "warnings": [],
  "next_step": "Approved by policy: it changes 1 row(s), within this agent's limit of 1. Call execute_write with this write_id now."
}
```

A write that needs two people says so in `next_step`: `Two different people must approve this, for example with lokra approve w_3763431e. Then call execute_write with this write_id.`

| Warning | Meaning |
|---|---|
| `no WHERE clause: this changes every row the agent can see` | An `UPDATE` or `DELETE` with no `WHERE` |
| `changes no rows: they may not exist, or be outside this agent's scope` | The dry run changed nothing |

The dry run is a real statement that is rolled back, so triggers fire inside it, and an `INSERT` still advances sequences.

### Proposal errors

| Error | Cause |
|---|---|
| `propose_write accepts a single INSERT, UPDATE or DELETE` | Any other statement, such as `SELECT`, `DROP` or `TRUNCATE` |
| `send exactly one SQL statement per call` | More than one statement |
| `the function pg_sleep() is not allowed` | A blocked function anywhere in the write |
| `database refused the query: permission denied for table orders` | The agent has no grant for this write |
| `database refused the query: new row violates row-level security policy for table "orders"` | The new row would fall outside the agent's row lock |

## write_status

Shows the current state of a write this agent proposed.

| Input | Type | Meaning |
|---|---|---|
| `write_id` | string | The id from `propose_write` |

```json
{
  "id": "w_4de7597e",
  "status": "approved",
  "statement": "update",
  "tables": ["orders"],
  "reason": "customer asked to cancel",
  "on_behalf_of": "dana",
  "approved_by": "sam (slack)",
  "would_affect_rows": 3,
  "warnings": [],
  "approvals_needed": 1,
  "approvals": ["sam (slack)"],
  "sql": "UPDATE orders SET status = 'cancelled' WHERE customer_id = 4"
}
```

| Field | Meaning |
|---|---|
| `id` | The write id |
| `status` | See [write statuses](#write-statuses) |
| `statement` | `insert`, `update` or `delete` |
| `tables` | Tables the statement names |
| `reason` | The reason the agent gave |
| `on_behalf_of` | The person on the token that proposed it |
| `approved_by`, `denied_by` | Who decided, once someone has |
| `would_affect_rows` | Rows the dry run changed |
| `affected_rows` | Rows actually changed, once executed |
| `warnings` | Warnings from the proposal |
| `approvals_needed` | How many people must approve: `1` or `2` |
| `approvals` | Who has approved so far |
| `failure` | Why execution failed, if it did |
| `sql` | The statement, with values the policy's detectors recognise masked |

Fields with no value are left out. An agent asking about another agent's write gets `unknown write_id`, and an id in the wrong format gets `invalid write_id`.

> **Tip:** `write_status` counts towards the agent's rate limit. An agent waiting for a person should check every few seconds, not in a tight loop.

## execute_write

Runs an approved write as the agent's role, and commits it only if it changes no more rows than the dry run showed.

| Input | Type | Meaning |
|---|---|---|
| `write_id` | string | The id of an approved write |

It returns `write_id` followed by the same fields as `write_status`, now with `status` set to `executed` and `affected_rows` filled in:

```json
{
  "write_id": "w_4de7597e",
  "id": "w_4de7597e",
  "status": "executed",
  "statement": "update",
  "tables": ["orders"],
  "approved_by": "sam (slack)",
  "would_affect_rows": 3,
  "affected_rows": 3,
  "sql": "UPDATE orders SET status = 'cancelled' WHERE customer_id = 4"
}
```

This example leaves out `reason`, `on_behalf_of`, `warnings`, `approvals_needed` and `approvals`, which come back as in `write_status`. A write that changes fewer rows than approved still commits. One that would change more is rolled back.

### Execution errors

| Error | Cause |
|---|---|
| `write w_4de7597e is 'pending', not approved` | Not approved yet. The same form is used for `denied`, `executed`, `failed` and `expired`. |
| `approval for w_4de7597e expired; propose it again` | More than `approval_ttl_seconds` (one hour by default) passed since approval |
| `it would now change 3 rows, more than the 0 that were approved, so nothing was changed. Propose it again for a fresh approval.` | The data changed since the dry run |
| `database refused the query: ...` | Postgres refused the write; it is marked `failed` |
| `unknown write_id` | No such write for this agent |

## Write statuses

| Status | Meaning | What the agent can do |
|---|---|---|
| `pending` | Waiting for one or two people | Wait, then check `write_status` |
| `approved` | Approved by a person or by the policy | Call `execute_write` within the approval window |
| `denied` | A person denied it | Nothing; explain to the user |
| `executed` | It ran and committed | Report `affected_rows` |
| `failed` | It would have changed more rows than approved, or Postgres refused it | Propose it again |
| `expired` | It was approved, but `execute_write` came too late | Propose it again |

## Example: a write from start to finish

Here `ops-bot` has `read: {orders: "*"}` and `write: {orders: [update]}`, with no approval rules, so every write waits for a person. The user asks: "The courier picked up order 1042, mark it shipped."

The agent checks the order first, with `query`:

```json
{"sql": "SELECT id, status FROM orders WHERE id = 1042"}
```

```json
{"columns": ["id", "status"], "rows": [[1042, "pending"]], "row_count": 1, "truncated": false, "masked": {}}
```

Then it proposes the change, with `propose_write`:

```json
{"sql": "UPDATE orders SET status = 'shipped' WHERE id = 1042", "reason": "Courier collected order 1042"}
```

```json
{
  "write_id": "w_a4e81f7f",
  "status": "pending",
  "would_affect_rows": 1,
  "approvals_needed": 1,
  "warnings": [],
  "next_step": "A human must approve this with `lokra approve w_a4e81f7f`. Then call execute_write with this write_id."
}
```

The agent tells the user it is waiting for approval. A person sees the write, its reason and the one-row dry run, in the terminal with `lokra pending`, in the dashboard, in [Slack](/docs/slack) or in [Lokra Cloud](/docs/cloud), and approves it. A moment later the agent calls `write_status` with `{"write_id": "w_a4e81f7f"}`:

```json
{"id": "w_a4e81f7f", "status": "approved", "statement": "update", "tables": ["orders"], "reason": "Courier collected order 1042", "on_behalf_of": "dana", "approved_by": "sam (slack)", "would_affect_rows": 1, "warnings": [], "approvals_needed": 1, "approvals": ["sam (slack)"], "sql": "UPDATE orders SET status = 'shipped' WHERE id = 1042"}
```

Finally it calls `execute_write` with the same id. The result has `"status": "executed"` and `"affected_rows": 1`, and the agent confirms to the user that order 1042 is marked shipped.

The ledger now holds the proposal, the approval with who made it, and the execution, each signed and chained to the last. `lokra log` shows them in order; see [audit ledger and reports](/docs/audit).
