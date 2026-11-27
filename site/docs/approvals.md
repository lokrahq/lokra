---
title: Approve writes
description: How a write moves from propose_write through a dry run and approval to execute_write, where people approve it, and the rules that let small writes through or require two people.
---
Agents never change data in one step. Every `INSERT`, `UPDATE` and `DELETE` is proposed, dry-run to count the rows it would change, approved by a person (or by a rule you wrote), and only then executed. This page follows a write through that flow and covers where to approve, approval rules, two-person approval and the checks that run at the end.

## How a write flows

1. **Propose.** The agent calls `propose_write` with one statement and a reason. Lokra accepts a single `INSERT`, `UPDATE` or `DELETE` and refuses anything else.
2. **Dry run.** Lokra runs the statement as the agent's own role inside a transaction, records how many rows it changed, and rolls it back. Grants and [row locks](/docs/row-locks) apply, so if Postgres refuses the statement, nothing is proposed.
3. **Wait.** Lokra stores the write under an ID such as `w_377e8285`. It waits for a person, unless an [approval rule](#approval-rules) approves it straight away.
4. **Decide.** Someone approves or denies it in the terminal, the dashboard, Slack or Lokra Cloud.
5. **Execute.** The agent calls `execute_write` with the ID. Lokra runs the statement again as the agent and commits it, unless it would now change more rows than were approved.

The agent can call `write_status` at any time to see where a write is. This is what `propose_write` returns for a delete that needs two people:

```json
{
  "write_id": "w_377e8285",
  "status": "pending",
  "would_affect_rows": 1,
  "approvals_needed": 2,
  "warnings": [],
  "next_step": "Two different people must approve this, for example with `lokra approve w_377e8285`. Then call execute_write with this write_id."
}
```

A write is always in one of these states:

| Status | Meaning |
|---|---|
| `pending` | Waiting for a person, or for a second person. |
| `approved` | Approved by people or by policy. The agent can execute it until the approval expires. |
| `denied` | Someone denied it. It can never run. |
| `executed` | It ran and was committed. `affected_rows` says how many rows it changed. |
| `failed` | It was rolled back because it would have changed more rows than were approved, or the database refused it. |
| `expired` | The approval ran out before the agent executed it. |

### What the dry run tells you

`would_affect_rows` is the number of rows Postgres reports the statement changed during the dry run. That is the number people approve, and the number the final check holds the write to.

The dry run really executes the statement, then rolls it back. Triggers fire and their changes are rolled back too. Sequence values are not: Postgres never rolls back a sequence, so an insert's dry run uses up an identity or serial value, and the real insert gets the next one.

### Warnings

Lokra adds warnings to a write so the person approving it notices. They do not block it:

| Warning | When |
|---|---|
| `no WHERE clause: this changes every row the agent can see` | An `UPDATE` or `DELETE` with no `WHERE`. |
| `changes no rows: they may not exist, or be outside this agent's scope` | The dry run changed 0 rows. The rows may not exist, or a row lock hides them. |

Warnings show in `lokra pending`, the dashboard, Slack and Lokra Cloud. A write that changes no rows still counts as within any `auto_max_rows`, so a policy can approve it.

## Where to approve

Every channel reads and writes the same pending writes, kept in the Lokra home folder (`.lokra/pending` next to your policy). Run them with the same policy and home as the MCP server. A write decided in one channel is closed everywhere else, and every decision is written to the [audit ledger](/docs/audit) with the name of the person who made it.

### In the terminal

List writes waiting for a decision:

```bash
lokra pending
```

```text
w_377e8285  [pending]  agent=ops-bot  for=dana  rows=1  12s ago
    needs two people
    reason: duplicate order
    sql:    DELETE FROM orders WHERE id = 4
```

Add `--all` to include writes that were already decided. Approve one:

```bash
lokra approve w_377e8285
```

```text
agent:  ops-bot (for dana)
reason: duplicate order
sql:    DELETE FROM orders WHERE id = 4
would affect 1 row(s)
approve? [y/N] y
approved by alice; it needs one more person to approve
```

`approve` asks for confirmation; `--yes` skips it. `deny` does not ask:

```bash
lokra deny w_377e8285 --by alice
```

Both record your operating system user name unless you pass `--by`.

### In the dashboard

```bash
lokra dashboard
```

This serves a local page at `http://127.0.0.1:8900` (`--port` changes it) with an approvals queue. Each write shows its agent, the person it acts for, the reason, the masked SQL, the dry-run row count and any warnings, with Approve and Deny buttons. Decisions are recorded as your user name followed by `(dashboard)`.

### In Slack

`lokra slack` posts each pending write to a channel with Approve, Deny and Pause buttons. Only the Slack members listed in the policy's `slack.approvers` can decide, and decisions are recorded as their Slack name followed by `(slack)`. Writes approved by policy never wait, so they are not posted. [Approve in Slack](/docs/slack) covers setup.

### In Lokra Cloud

`lokra cloud` connects the gate to [Lokra Cloud](/docs/cloud), so your team can approve or deny from a browser or phone. Decisions come back to the gate, which applies them and records who made them, such as `dana (cloud)`.

## Approval rules

By default every write waits for one person. An agent's `approval` block changes that:

```yaml
agents:
  ops-bot:
    write:
      orders: [update, delete]
    approval:
      auto_max_rows: 5
      auto_statements: [update]
      two_people: [delete]
```

| Key | Type | Default | Meaning |
|---|---|---|---|
| `auto_max_rows` | whole number, 0 or more | not set | Writes whose dry run changes this many rows or fewer are approved by policy. Not set means every write needs a person. |
| `auto_statements` | list of `insert`, `update`, `delete` | `[insert, update]` | The statements `auto_max_rows` applies to. |
| `two_people` | list of `insert`, `update`, `delete` | `[]` | Statements that need two different people. They are never approved by policy. |

A write is approved by policy only when all of these hold:

- `auto_max_rows` is set,
- its statement is in `auto_statements` and not in `two_people`,
- its dry run changed no more than `auto_max_rows` rows.

With the rules above, an update of 2 orders is approved at once, an update of 40 waits for one person, and any delete waits for two. A policy approval comes back from `propose_write` with `"status": "approved"` and `"approvals_needed": 0`, and the ledger records it as approved by `policy (up to 5 rows)`. The agent can call `execute_write` straight away.

Note that `delete` is not in the default `auto_statements`. A rule like `approval: {auto_max_rows: 1}` lets small inserts and updates through, but every delete still waits for a person.

### Two-person approval

A statement listed in `two_people` stays `pending` after the first approval, from any channel, until a different person approves it too:

```text
approved by alice; it needs one more person to approve
```

The same person approving again is refused:

```text
error: alice has already approved w_377e8285; it needs a different person
```

When the second person approves, the write becomes `approved`, with both names in `approved_by` (`alice, bob`). The two approvals can come from different channels, for example one in Slack and one in the terminal. If the agent tries `execute_write` after only one approval, it gets `write w_377e8285 is 'pending', not approved`.

Lokra compares the names it records, ignoring case and a `(slack)` or `(cloud)` suffix. So `dana` approving in the terminal and `dana (cloud)` approving in Lokra Cloud count as one person, and the second approval is refused.

> **Warning:** Lokra tells approvers apart by name. It ignores where the approval came from, so `dana`, `dana (dashboard)`, `dana (slack)` and `dana (cloud)` all count as one person. But in the terminal `--by` accepts any name, and a terminal username need not match a Slack or GitHub username, so anyone with a shell on the machine running Lokra could approve twice under two names. Slack checks each click against the approver list, and Lokra Cloud records the signed-in user. For a real two-person control, approve in Slack or Lokra Cloud and limit who can log in to the gate's machine.

## The row-count check at execute

Whoever approved a write, including a rule, it runs only if it changes no more rows than its dry run showed. When the agent calls `execute_write`, Lokra runs the statement again inside a transaction and compares the rows it changed with the approved count:

- **The same or fewer rows:** Lokra commits and the write is `executed`.
- **More rows:** Lokra rolls it back, marks the write `failed`, writes a `write_rolled_back` entry to the ledger, and tells the agent:

```text
it would now change 3 rows, more than the 2 that were approved, so nothing was changed. Propose it again for a fresh approval.
```

This catches data that changed between the dry run and the execute. For example, an `UPDATE orders SET status = 'shipped' WHERE status = 'pending'` approved for 2 rows is rolled back if a third pending order arrives in the meantime. The check compares counts, not which rows, so a statement that matches the same number of different rows still runs. Write precise `WHERE` clauses, such as a list of IDs, when it matters which rows change.

## Approval expiry

An approval lasts `approval_ttl_seconds` (default 3600, one hour), counted from the moment the write was finally approved. If the agent calls `execute_write` after that, it is refused and the write is marked `expired`:

```text
approval for w_377e8285 expired; propose it again
```

A shorter window keeps an approval from being used long after the data it was based on has changed. Set it at the top of the policy:

```yaml
approval_ttl_seconds: 900
```

Pending writes do not expire. They wait until someone approves or denies them.

## Errors the agent sees

| Message from `execute_write` | Meaning |
|---|---|
| `write w_377e8285 is 'pending', not approved` | It still needs a decision, or a second person. The status in quotes can also be `denied`, `executed`, `failed` or `expired`. |
| `approval for w_377e8285 expired; propose it again` | The approval ran out. |
| `it would now change 3 rows, more than the 2 that were approved, so nothing was changed. Propose it again for a fresh approval.` | The row-count check rolled it back. |
| `unknown write_id` | No such write, or it belongs to another agent. An agent can only see and run its own writes. |

[Troubleshooting](/docs/troubleshooting#writes-and-approvals) covers these and the errors people hit when deciding.
