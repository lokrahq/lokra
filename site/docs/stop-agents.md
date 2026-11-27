---
title: Stop an agent
description: Pause and resume an agent, revoke a single token, and cap an agent's request rate, from the terminal, the dashboard, Slack or Lokra Cloud.
---
An always-on agent needs brakes that work in seconds, without rewriting the policy. Lokra has three: pause an agent, revoke one token, and cap how many requests an agent may make a minute. Each takes effect on the agent's next request, and each is written to the [audit ledger](/docs/audit).

| You want to | Use | Effect |
|---|---|---|
| Stop an agent now | `lokra pause <agent>` | Refuses every request from the agent; with an admin connection, also blocks its database login and ends its sessions |
| Let it run again | `lokra resume <agent>` | Lifts the pause; with an admin connection, allows its login again |
| Kill one leaked token | `lokra revoke-token <token or id>` | Refuses that token only; the agent's other tokens keep working |
| Stop a retry loop hammering the database | `rate_limit` in the policy | Refuses requests over the cap for the rest of the minute |

## Pause an agent

```bash
lokra pause support-bot --reason "deleting too much" --admin-dsn "postgresql://admin:secret@db.internal:5432/app"
```

```text
support-bot is paused: Lokra refuses its requests from now on; database login disabled, 2 open session(s) ended
```

From that moment, every tool call from that agent fails, whichever token it uses. The agent sees the person who paused it and the reason:

```text
stopped: support-bot is paused by dana: deleting too much
```

| Flag | What it does |
|---|---|
| `agent` | The agent from the policy. |
| `--reason` | Recorded in the ledger and shown to the agent. |
| `--admin-dsn` | Admin connection string. Default: `$LOKRA_ADMIN_DSN`. |

### With and without an admin connection

A pause always works inside Lokra. The admin connection adds a second layer in the database itself.

| | Without an admin connection | With `--admin-dsn` or `LOKRA_ADMIN_DSN` |
|---|---|---|
| Lokra refuses the agent's requests | Yes | Yes |
| The agent's role can log in to Postgres | Yes, unchanged | No: Lokra runs `ALTER ROLE ... NOLOGIN` |
| Open sessions as the agent's role | Left alone | Ended with `pg_terminate_backend`, so a long query stops too |

Without an admin connection, Lokra says so:

```text
support-bot is paused: Lokra refuses its requests from now on; database login unchanged (no admin connection; pass --admin-dsn to change it too)
```

Blocking the login matters if anything else could use the agent's role: a copy of `.lokra/secrets.json`, or a Lokra process that does not share your state directory. Set `LOKRA_ADMIN_DSN` wherever you run `pause`, the dashboard, `lokra slack` or `lokra cloud`, so every pause blocks the login too. The admin connection must be to the database the policy names.

> **Tip:** Lokra checks pauses on every request from a file in its state directory, so a pause reaches every Lokra process that shares that directory at once, with no restart. See [where state lives](/docs/production#where-state-lives).

### What a pause does not do

- It does not cancel writes waiting for approval. People can still approve or deny them, but the agent cannot run `execute_write` until it is resumed, and an approval expires after `approval_ttl_seconds` (one hour by default).
- It does not change the policy. The agent keeps its grants, masking and row lock.

## Resume an agent

```bash
lokra resume support-bot --admin-dsn "postgresql://admin:secret@db.internal:5432/app"
```

```text
support-bot is running again; database login enabled
```

If the agent was not paused, Lokra says `support-bot was not paused`, and with an admin connection it still makes sure the login is enabled. Every resume is written to the ledger.

## Pauses survive provisioning

`lokra provision` keeps a paused agent paused. It creates or updates the role with `NOLOGIN` and marks it in its output:

```text
altered role lokra_ops_bot: rows where account_id in [42] on ['orders'], PAUSED, reads ['orders'], writes ['orders']
```

So you can fix the policy while the agent is stopped, provision, check the result, and only then resume it.

## Revoke one token

When one token leaks, revoke it and leave the agent's other tokens working. Pass the token itself or its id:

```bash
lokra revoke-token lokra1.eyJhZ2VudCI6InN1cHBvcnQtYm90Ii...
lokra revoke-token fe6ac14c196d581b
```

```text
token fe6ac14c196d581b revoked
```

When you pass the whole token, Lokra checks its signature first and refuses one this Lokra did not issue. An expired token can still be revoked. The token id is the `token_id` field on every request in the ledger, so you can find it from what the token did:

```bash
grep '"agent": "support-bot"' .lokra/ledger.jsonl | tail -n 3
```

A request with a revoked token fails with:

```text
stopped: this token was revoked; ask for a new one
```

A revocation is permanent, and needs no admin connection: it does not touch the database role. To stop every token for an agent, pause the agent instead.

## Cap the request rate

A stuck agent can retry the same call hundreds of times a minute. Cap it in the policy:

```yaml
agents:
  ops-bot:
    read: {orders: "*"}
    write: {orders: [update]}
    rate_limit: 60
```

`rate_limit` is the number of requests the agent may make in any 60-second window. It counts every tool call, including `whoami`, `list_tables` and `write_status`, across all of the agent's tokens and every Lokra process that shares the state directory. Leave it out, or set it to `0`, for no limit.

A call over the cap is refused, and does not count against the next window:

```text
stopped: rate limit of 60 requests a minute reached; wait before retrying
```

Run `lokra provision` and restart `lokra serve --http` after changing it; the server reads the policy at start-up. See [change the policy](/docs/production#change-the-policy).

### Stopped events and looping flags

Every refused request, whether paused, revoked or over its rate limit, is written to the ledger as a `stopped` event with a `control` of `paused`, `revoked` or `rate_limit`:

```text
   68 2026-11-27T09:18:58 ops-bot            stopped         rate limit of 60 requests a minute reached; wait before retrying
```

`lokra report` and the dashboard flag an agent that hit its rate limit three or more times in the ledger as looping:

```text
ops-bot (for dana)
  reads 18 · rows 20 · masked 0
  writes 6 proposed, 5 approved, 1 denied, 1 executed (3 rows)
  blocked 2 · tables orders
  ⚠ hit its rate limit 20 times, a sign of a retry loop
```

A looping flag is a good reason to pause the agent and look at what it was trying to do. See [audit ledger and reports](/docs/audit#risk-flags) for the other flags.

## Pause from the dashboard

`lokra dashboard` lists every agent with its state and a **Pause** or **Resume** button. Pause asks for an optional reason. The ledger records the decision as your username followed by `(dashboard)`. Start the dashboard with `--admin-dsn` (or set `LOKRA_ADMIN_DSN`) so its pauses also block the database login:

```bash
lokra dashboard --admin-dsn "postgresql://admin:secret@db.internal:5432/app"
```

See [audit ledger and reports](/docs/audit#the-dashboard) for the rest of the dashboard.

## Pause from Slack

With `lokra slack` running, every approval message has a **Pause** button for the agent that proposed the write, and Lokra posts a notice with a **Resume** button whenever an agent is paused, from anywhere. Only the approvers listed in the policy can press them. Start `lokra slack` with `--admin-dsn` so a pause from Slack also blocks the login. See [approve in Slack](/docs/slack#pause-and-resume-from-slack).

## Pause from Lokra Cloud

With `lokra cloud` connected, you can pause and resume agents from [Lokra Cloud](https://cloud.lokra.dev), from a laptop or a phone. The gate applies the pause and records who made it. Start `lokra cloud` with `--admin-dsn` so a pause from the Cloud also blocks the database login:

```bash
LOKRA_CLOUD_TOKEN=lkg_... lokra cloud --admin-dsn "postgresql://admin:secret@db.internal:5432/app"
```

See [Lokra Cloud](/docs/cloud) for setting up the connection.

## What lands in the ledger

| Action | Ledger event | Recorded |
|---|---|---|
| Pause | `agent_paused` | Agent, who decided, reason |
| Resume | `agent_resumed` | Agent, who decided |
| Revoke a token | `token_revoked` | Token id, who decided |
| Refused request | `stopped` | Agent, token id, tool, which control, reason |

`lokra log` shows them alongside every other request, and `lokra verify-ledger` checks none were removed.
