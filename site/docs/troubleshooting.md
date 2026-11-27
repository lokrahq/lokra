---
title: Troubleshooting
description: The errors people hit with Lokra, what each one means and how to fix it.
---
Each heading below is the text Lokra prints, with names such as agents, tables and write IDs replaced by examples. Search the page for the part of your message that does not change. Commands print errors after `error:`; agents see them as the tool's error message, and `lokra try` prints them after `REFUSED:`.

For a full Python traceback instead of a one-line error, run the command again with `LOKRA_DEBUG=1`.

## Setup and policy files

### config not found

```text
error: config not found: /home/dana/policies.yaml
hint: run this from your project folder (where policies.yaml lives), or pass --config /path/to/policies.yaml
```

Lokra looks for `policies.yaml` in the current folder unless you say otherwise. Run the command from the folder that holds the policy, or pass `--config` before the command (`lokra --config ./app/policies.yaml check`), or set `LOKRA_CONFIG`.

### pass --admin-dsn "postgresql://…", an admin login to

```text
error: pass --admin-dsn "postgresql://…", an admin login to app on db.example.com:5432 (or set LOKRA_ADMIN_DSN)
```

`provision` (and `check`, which asks for `--dsn`) needs a connection string, and none was given. Pass one, or set `LOKRA_ADMIN_DSN`. See [connect your database](/docs/connect-database#give-provision-the-admin-connection).

### already exists (use --out PATH or --force to overwrite)

```text
error: policies.yaml already exists (use --out PATH or --force to overwrite)
```

`lokra init` will not replace a policy you may have edited. Write the scaffold somewhere else with `--out new.yaml` and compare, or pass `--force`.

### no base tables found in schema(s)

```text
no base tables found in schema(s): public
```

`lokra init` found no tables it could read. Check the database name in the connection string, name the right schemas with `--schemas`, and make sure the login can see the tables. `init` skips views.

### unknown agent

```text
error: unknown agent 'suport-bot'
```

The agent name on the command line, or in the token, is not under `agents` in the policy. Check the spelling. An agent sees this as `access denied: unknown agent 'suport-bot'` when its token was minted for an agent that has since been removed or renamed.

### Errors about the policy file itself

Messages such as `rows needs a column and at least one value` or `approval auto_max_rows should be a whole number of rows, 0 or more` come from checking the policy file. The [policy file reference](/docs/policy-reference#validation-errors) lists each one with its cause.

## Provisioning

### the admin connection is to database

```text
error: the admin connection is to database 'postgres', but the policy is for 'app'
```

The admin connection string points at a different database from the policy's `database.dbname`. Lokra refuses it so it never creates roles and grants in the wrong place. On Supabase and RDS this often happens because the connection string ends in `/postgres`. Fix the database name in whichever one is wrong.

### cannot add agents to the lokra_agents role

```text
error: app_admin cannot add agents to the lokra_agents role, which another role created; as a superuser or that role, run: GRANT lokra_agents TO app_admin WITH ADMIN OPTION
```

Underneath, Postgres said `permission denied to grant role "lokra_agents"`. Every agent role joins the `lokra_agents` group role. On Postgres 16 and later, a non-superuser can only add members to a role it created or holds `ADMIN OPTION` on, and here a different role created `lokra_agents`, usually an earlier `provision` run with another admin login. Run the command from the message as a superuser, or as the role that created `lokra_agents`, then run `lokra provision` again.

### permission denied to alter role

```text
error: permission denied to alter role
DETAIL:  Only roles with the CREATEROLE attribute and the ADMIN option on role "lokra_support_bot" may alter this role.
```

The agent role was created by a different admin login, and on Postgres 16 and later only that login (or a superuser) may change it. Provision with the login that created it, or grant your admin login the right once, as a superuser:

```sql
GRANT lokra_support_bot TO app_admin WITH ADMIN OPTION;
```

If the detail says `Only roles with the SUPERUSER attribute may alter roles with the SUPERUSER attribute`, someone made the agent role a superuser. Remove it as a superuser with `ALTER ROLE lokra_support_bot NOSUPERUSER`.

### has BYPASSRLS; Lokra will not run an agent with that

```text
error: role lokra_support_bot has BYPASSRLS; Lokra will not run an agent with that. Remove it as a superuser: ALTER ROLE lokra_support_bot NOBYPASSRLS
```

The agent's role has gained an attribute that would let it skip row locks or change the database, such as `BYPASSRLS`, `CREATEDB` or `REPLICATION`. A non-superuser admin cannot remove it, so `provision` stops. Run the `ALTER ROLE` from the message as a superuser, then run `provision` again. A superuser admin removes these attributes on every `provision` without stopping.

### must be owner of table

```text
error: must be owner of table customers
```

A row lock needs row-level security on the table, and only the table's owner can turn it on and create policies. Provision with the login that owns the tables, or make your admin login a member of the owning role. On RDS, tables created by a separate migration user belong to that user.

### permission denied for table (when provisioning)

```text
error: permission denied for table products
```

During `provision`, this means the admin login cannot grant access to the table: it neither owns the table nor holds the privilege `WITH GRANT OPTION`. `permission denied for schema billing` is the same problem one level up. Fix it as for [must be owner of table](#must-be-owner-of-table).

### tables in the policy that do not exist

```text
error: tables in the policy that do not exist: billing.invoice
```

A table in `read` or `write` is not in the database, or is in a schema you did not name. Tables outside `public` must be written `schema.table`. `provision` checks this before it changes anything. `lokra check` lists each missing table and column.

### is a view, which runs with its owner's rights

```text
error: support-bot: order_totals is a view, which runs with its owner's rights, so the row lock would not apply through it; grant the tables it reads instead, or make it run as the agent (Postgres 15+): ALTER VIEW order_totals SET (security_invoker = true)
```

An agent with a row lock cannot be given a plain view, because the view would read the tables with its owner's rights and skip the lock. Give the agent the tables instead, or make the view run as the agent with the `ALTER VIEW` from the message. See [views](/docs/row-locks#views).

### is a materialized view, which a row lock cannot reach

```text
error: support-bot: order_counts is a materialized view, which a row lock cannot reach; grant the tables it reads instead
```

A materialized view stores its rows, so no row lock can apply to them. Give an agent with a row lock the underlying tables instead.

## Connecting agents

### is not provisioned yet

```text
error: support-bot is not provisioned yet; run lokra provision --admin-dsn "<admin connection string>" first
```

`lokra connect` found no database password for the agent in `.lokra/secrets.json`. Run `lokra provision` first. If you did, check that `connect` is using the same policy and `--home` as `provision`.

### agent is not provisioned; run lokra provision

```text
agent 'support-bot' is not provisioned; run `lokra provision`
```

The same problem, seen by an agent. The MCP server has no password for the agent's role in its home folder. This happens when the server runs with a different `--home` or `LOKRA_HOME`, or in a container whose `/work/.lokra` is not the folder you provisioned from.

### could not log in to

```text
error: support-bot could not log in to app at db.example.com:5432: connection failed: connection to server at "db.example.com", port 5432 failed: FATAL:  password authentication failed for user "lokra_support_bot" (--no-check writes the config anyway)
```

`lokra connect` tried to log in as the agent's role and Postgres refused. The text after the address says why:

- `password authentication failed`: the password in `.lokra/secrets.json` does not match the role, for example after the folder was copied from another machine or the role was recreated. Run `lokra provision` again; it sets the role's password from `secrets.json`.
- `role "lokra_support_bot" is not permitted to log in`: the agent is [paused](/docs/stop-agents) with its database login blocked. Run `lokra resume support-bot --admin-dsn "…"`.
- `Connection refused` or a timeout: the policy's `host` or `port` is wrong, or a firewall or security group blocks the machine running Lokra.

### this policy requires a purpose-of-use; pass --purpose

```text
error: this policy requires a purpose-of-use; pass --purpose
```

The policy sets `require_purpose: true`, so the agent's token must carry a purpose. Pass one of the agent's `purposes`: `lokra connect support-bot --purpose customer-support`.

### say which agent to connect

```text
error: say which agent to connect, one of: support-bot, ops-bot
```

The policy has more than one agent. Name one: `lokra connect support-bot`.

### already has a server called

```text
error: .mcp.json already has a server called lokra-support-bot that is not Lokra; choose another --name, or --force to replace it
```

The client's config already has a server with that name that Lokra did not write. Pick another name with `--name`, or replace it with `--force`.

## Agent requests

### access denied: no agent token supplied

```text
access denied: no agent token supplied (LOKRA_TOKEN, or an Authorization: Bearer header over HTTP)
```

Over stdio, the server was started without `LOKRA_TOKEN` and without `--dev-agent`. Over HTTP, the request had no `Authorization: Bearer` header. Give the agent a token from `lokra mint-token support-bot`. See [connect AI clients](/docs/connect-clients).

### access denied: token has expired, mint a new one

Tokens last at most 8 hours. Mint a new one with `lokra mint-token`. Servers started by `lokra connect` issue themselves a new 8-hour token each time the client starts them, so restarting the client also works.

### access denied: token signature is invalid

The token was signed with a different key from the one in this Lokra's home folder (`.lokra/signing.key`). This happens when a token minted on one machine or with one `--home` is used with another, or when the home folder was recreated. Mint the token with the same policy and home the server uses.

### access denied: a declared purpose-of-use is required for this request

The policy sets `require_purpose: true` and the token carries no purpose. Mint or connect with `--purpose`.

### access denied: purpose is not permitted

```text
access denied: purpose 'billing' is not permitted for support-bot
```

The token's purpose is not in the agent's `purposes` list. Matching is exact. Use a listed purpose, or add this one to the policy.

### stopped: is paused

```text
stopped: support-bot is paused by dana: deleting too much
```

Someone paused the agent, from the terminal, the dashboard, Slack or Lokra Cloud. Lokra refuses every request until it is resumed. When you are ready, run `lokra resume support-bot`. See [stop agents](/docs/stop-agents).

### stopped: rate limit reached

```text
stopped: rate limit of 60 requests a minute reached; wait before retrying
```

The agent made more requests in the last 60 seconds than its `rate_limit` allows. It can retry once older requests age out. If the agent keeps hitting the cap, it is probably stuck in a retry loop; `lokra report` flags it. Raise `rate_limit` only if the agent really needs more.

### stopped: this token was revoked; ask for a new one

Someone revoked this token with `lokra revoke-token`. The agent's other tokens still work. Mint a new one.

### the masked column can only be selected as itself

```text
the masked column email can only be selected as itself, or used in WHERE, JOIN, GROUP BY or ORDER BY; it cannot go inside a function, alias, UNION or subquery result
```

The query would return a masked column under another name, for example `lower(email)` or `email AS contact`, where the masking rule could not see it. Select the column as itself (`SELECT email FROM customers`) and let Lokra mask it, or use it only to filter, join, group or sort. See [masking](/docs/masking#queries-lokra-refuses).

### refers to a whole row, which would carry masked columns

```text
c refers to a whole row, which would carry masked columns; select the columns you need
```

The query passes a whole row to something, as in `row_to_json(c)` or `SELECT c FROM customers c`. List the columns instead. The same applies to `c.* inside an expression would carry masked columns`.

### database refused the query: permission denied for table

```text
database refused the query: permission denied for table customers
```

Postgres refused because the agent's role has no grant for something in the query. Usually the agent asked for a column it was not given, often through `SELECT *` on a table where its `read` lists only some columns, or an `UPDATE` filters on a column it cannot read. Ask for the listed columns (the agent's `list_tables` tool shows them), or add the column to the policy and run `lokra provision`.

### database refused the query: relation does not exist

```text
database refused the query: relation "invoices" does not exist
```

The table is in another schema, or not granted to this agent at all. Agent roles only search `public`, so tables elsewhere must be written with their schema: `billing.invoices`.

### canceling statement due to statement timeout

```text
database refused the query: canceling statement due to statement timeout
```

Agent roles have a 5-second statement timeout, so one runaway query cannot tie up the database. Narrow the query, filter on indexed columns, or add a `LIMIT`.

### this is a write; use propose_write so a human can approve it

The agent sent an `INSERT`, `UPDATE` or `DELETE` to the `query` tool. Writes go through `propose_write`. See [approve writes](/docs/approvals).

### send exactly one SQL statement per call

The query held more than one statement, such as `SELECT …; DROP TABLE …`. Send one statement per call.

### the function is not allowed

```text
the function pg_sleep() is not allowed
```

Lokra refuses functions that read files, run programs, reach other systems, change session settings, advance sequences, stall the connection or run a query given as text, such as `pg_read_file`, `dblink`, `set_config`, `pg_sleep`, `nextval` and `query_to_xml`. Agents cannot call them in reads or writes.

### only SELECT queries are allowed here

```text
only SELECT queries are allowed here (got COMMAND)
```

The `query` tool only runs a single `SELECT` (including `WITH … SELECT` and `UNION`). Anything else is refused: `SET ROLE`, `SHOW`, `EXPLAIN` and `VACUUM` are reported as `COMMAND`, and `COPY`, `CREATE` and `BEGIN` by name.

### is not allowed in a read query

```text
LOCK is not allowed in a read query
```

The `SELECT` contains a clause that writes or locks: `SELECT … INTO` is reported as `INTO`, and `FOR UPDATE` or `FOR SHARE` as `LOCK`. Remove the clause. A `WITH` clause that changes data is refused with `data-modifying statements inside a SELECT (for example in a WITH clause) are not allowed`.

### propose_write accepts a single INSERT, UPDATE or DELETE

`propose_write` only takes one `INSERT`, `UPDATE` or `DELETE`. `TRUNCATE`, `MERGE`, DDL and plain `SELECT` statements are refused. Instead of `TRUNCATE`, an agent can propose a `DELETE`, which needs `delete` in its policy and goes through approval like any other write.

### could not parse SQL safely

```text
could not parse SQL safely: Invalid expression / Unexpected token. Line 1, Col: 13.
```

Lokra could not parse the statement, so it refused it rather than guess. Fix the SQL syntax. Lokra parses Postgres SQL.

### unknown detectors

```text
unknown detectors: ['phone_au']
```

`masking.detectors` lists a name that does not exist. The valid names are `ihi`, `medicare`, `card`, `iban`, `us_ssn`, `email`, `au_phone` and `phone`. Until you fix it, every request fails.

## Writes and approvals

### new row violates row-level security policy for table

```text
database refused the query: new row violates row-level security policy for table "notes"
```

The write would create or move a row outside the agent's [row lock](/docs/row-locks#writes), for example an insert with another account's `account_id`. The dry run caught it, so nothing was proposed. Set the lock column to one of the agent's values.

### changes no rows: they may not exist, or be outside this agent's scope

A warning, not an error. The dry run changed 0 rows. The rows may not exist, or a row lock hides them from this agent. Check the `WHERE` clause before approving.

### is 'pending', not approved

```text
write w_377e8285 is 'pending', not approved
```

The agent called `execute_write` before the write was approved. If it needs two people, it stays `pending` until the second approves. The quoted status can also be `denied`, `executed`, `failed` or `expired`; only an `approved` write can run.

### approval expired; propose it again

```text
approval for w_377e8285 expired; propose it again
```

The agent did not execute the write within `approval_ttl_seconds` of its approval. The agent proposes it again and someone approves the new one. See [approval expiry](/docs/approvals#approval-expiry).

### would now change more rows than were approved

```text
it would now change 3 rows, more than the 2 that were approved, so nothing was changed. Propose it again for a fresh approval.
```

The data changed between the dry run and the execute, and the statement would now touch more rows than the approval covered. Lokra rolled it back and marked the write `failed`. The agent proposes it again so a person sees the new row count. See [the row-count check](/docs/approvals#the-row-count-check-at-execute).

### has already approved; it needs a different person

```text
error: alice has already approved w_377e8285; it needs a different person
```

The write needs two people and this person already approved it. Lokra treats the same name from the terminal, Slack (`alice (slack)`) and Lokra Cloud (`alice (cloud)`) as one person. Ask a second approver.

### is already denied

```text
error: write w_377e8285 is already 'denied'
```

Someone already decided the write, possibly in another channel. A write can only be decided while it is `pending`. Check `lokra pending --all`.

### unknown write_id

The write ID does not exist in this Lokra's home folder, or belongs to another agent. An agent can only check and run its own writes. If you approve from a different machine or folder from the one the server runs in, you are looking at a different set of pending writes.

## Stopping agents

### database login unchanged (no admin connection; pass --admin-dsn to change it too)

```text
support-bot is paused: Lokra refuses its requests from now on; database login unchanged (no admin connection; pass --admin-dsn to change it too)
```

The pause worked: Lokra refuses every request from the agent. It could not also block the agent's database login because there was no admin connection. To block the login and end the agent's open sessions too, pass `--admin-dsn` or set `LOKRA_ADMIN_DSN`. The same applies to `lokra resume`, `lokra slack` and `lokra cloud`. See [stop agents](/docs/stop-agents).

### could not disable the database login

```text
support-bot is paused: Lokra refuses its requests from now on; could not disable the database login: connection failed: …
```

The pause took effect in Lokra, but the admin connection failed, so the login is still open. Fix the admin connection string and run `lokra pause` again.

## Slack and Lokra Cloud

### Lokra Cloud does not recognise this gate token

```text
error: Lokra Cloud does not recognise this gate token. It may be mistyped or only partly copied, or its gate was removed. Create a new token under Gates in Lokra Cloud and run lokra cloud again.
```

Lokra Cloud rejected the token. Copy it again in full, or create a new one under **Gates**, and set `LOKRA_CLOUD_TOKEN`. See [connect a gate](/docs/cloud-gates).

### LOKRA_CLOUD_TOKEN should start with lkg_

The value in `LOKRA_CLOUD_TOKEN` is not a gate token. Gate tokens start with `lkg_`. Agent tokens (`lokra1.…`) and Slack tokens do not work here.

### set LOKRA_CLOUD_TOKEN to a gate token from Lokra Cloud

`lokra cloud` needs `LOKRA_CLOUD_TOKEN` in its environment. Create a token under **Gates** in Lokra Cloud.

### could not reach Lokra Cloud

```text
lokra: could not reach Lokra Cloud: [Errno 61] Connection refused; retrying in 3s
```

The gate could not connect out to Lokra Cloud. Check outbound HTTPS from the machine, any proxy, and `--url` or `LOKRA_CLOUD_URL` if you set them. `lokra cloud` keeps retrying, waiting up to a minute between attempts.

### Slack support needs an extra package

```text
error: Slack support needs an extra package: pipx install 'lokra[slack]'
```

Install Lokra with the Slack extra, as the message says.

### set LOKRA_SLACK_BOT_TOKEN and LOKRA_SLACK_APP_TOKEN

```text
error: set LOKRA_SLACK_BOT_TOKEN (xoxb-…) and LOKRA_SLACK_APP_TOKEN (xapp-…)
```

`lokra slack` needs both tokens in its environment. [Approve in Slack](/docs/slack) shows where to find them.

### add the channel to policies.yaml

```text
error: add the channel to policies.yaml: slack: {channel: C0123ABCD, approvers: [U0123ABCD]}
```

The policy has no `slack` block, or no `channel` in it. Add the channel ID and your approvers' Slack member IDs. A missing approver list gives `list who may approve: slack: {approvers: [U0123ABCD]} (Slack member IDs)`.

### You're not on Lokra's approver list for this database.

Slack shows this only to the person who clicked. Their Slack member ID is not in `slack.approvers`. Add it to the policy and restart `lokra slack`.
