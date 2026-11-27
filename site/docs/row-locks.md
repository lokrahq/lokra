---
title: Row locks
description: Limit an agent to the rows of one account, tenant or region, enforced by Postgres row-level security on the agent's own role.
---
A row lock limits an agent to the rows that belong to one account, tenant, clinic or region. You add a `rows` rule to the agent's policy, and `lokra provision` turns it into Postgres row-level security policies on that agent's role. The database enforces the lock on every read and write, so it holds even if a query gets past Lokra's SQL check.

## Add a row lock

```yaml
agents:
  support-bot:
    rows:
      column: account_id
      values: [42]
    read:
      customers: "*"
      orders: "*"
      notes: "*"
    write:
      notes: [insert]
```

`support-bot` now only sees, inserts, updates and deletes rows whose `account_id` is 42, on every table in its policy that has an `account_id` column. Rows outside the lock do not exist as far as the agent can tell: they do not appear in results, counts or joins.

- `values` takes one or more numbers or strings. With several, the agent sees rows matching any of them: `values: [42, 43]`.
- Strings work for `text` and `uuid` columns: `values: [a3b8f042-1e16-4f0a-a8f0-421e16f0a8f0]`.
- The lock is fixed per agent. To serve several accounts with separate scopes, define one agent per account. Each gets its own role.

The agent's `list_tables` tool tells it the scope, for example "You only see rows where account_id in [42]."

## Which tables are locked

The lock applies to every table the agent can read or write that has the lock column. A table without that column is not locked: the agent sees all of its rows. `provision` says so in its output, and `lokra check` warns:

```text
created role lokra_support_bot: rows where account_id in [42] on ['customers', 'notes', 'orders']; all rows on ['products'], reads ['customers', 'notes', 'orders', 'products'], writes ['notes']
```

```text
  WARN   support-bot: sees every row of 'products', which has no 'account_id' column to lock rows by
```

That is fine for a shared table such as a product catalogue. For a table that does hold per-account data, such as `order_items` linked only through `order_id`, either add an `account_id` column to it or leave it out of the agent's policy. A lock only follows a column on the table itself, not a foreign key.

### Map a column with a different name

Where a table names the scope differently, map it with `tables`:

```yaml
    rows:
      column: account_id
      values: [42]
      tables:
        accounts: id
        billing.invoices: customer_account
```

Here the lock is `id IN (42)` on `accounts`, `customer_account IN (42)` on `billing.invoices`, and `account_id IN (42)` everywhere else. Table names follow the policy's usual rule: `schema.table` outside `public`.

## What provision creates

For each locked table, `provision` creates two policies on the agent's role, both with the same condition for reading (`USING`) and for writing (`WITH CHECK`):

```sql
CREATE POLICY lokra_support_bot_rows ON customers AS PERMISSIVE FOR ALL TO lokra_support_bot
  USING (account_id IN (42)) WITH CHECK (account_id IN (42));
CREATE POLICY lokra_support_bot_lock ON customers AS RESTRICTIVE FOR ALL TO lokra_support_bot
  USING (account_id IN (42)) WITH CHECK (account_id IN (42));
```

Postgres needs both:

- With row-level security on, a role sees no rows at all unless a **permissive** policy lets it. `_rows` is what lets the agent see its own rows.
- Permissive policies add up: if any one of them allows a row, the role sees it. If your schema already has a permissive policy that applies to everyone, it would widen the agent's view. The **restrictive** `_lock` policy must also pass for every row, so no other policy can let the agent see past the lock.

Postgres skips row-level security for a table's owner, for superusers and for roles with `BYPASSRLS`. Agent roles are none of these: they are created `NOSUPERUSER NOBYPASSRLS`, never own tables, and `provision` refuses to run an agent whose role has gained such an attribute.

### Agents without a lock on the same table

Once row-level security is on for a table, any agent without a lock there would see nothing. So for each such agent, `provision` adds a policy that allows all rows:

```sql
CREATE POLICY lokra_ops_bot_all ON orders AS PERMISSIVE FOR ALL TO lokra_ops_bot
  USING (true) WITH CHECK (true);
```

### Other roles keep their access

If row-level security was off on a table, `provision` turns it on and adds a pass-through policy for everyone who is not a Lokra agent:

```sql
ALTER TABLE customers ENABLE ROW LEVEL SECURITY;
CREATE POLICY lokra_passthrough ON customers AS PERMISSIVE FOR ALL TO PUBLIC
  USING (NOT pg_has_role(session_user, 'lokra_agents', 'MEMBER'))
  WITH CHECK (NOT pg_has_role(session_user, 'lokra_agents', 'MEMBER'));
```

Your app and other roles see and change exactly what they did before. Agent roles are members of `lokra_agents`, so the pass-through does not apply to them; they only get the policies above.

### Tables that already use row-level security

If you already turned row-level security on for a table, for example on Supabase, Lokra leaves it on and adds no pass-through. It adds only its own agent policies, all named `lokra_…`, and never changes yours.

Your policies apply to the agent roles only if they are written for `PUBLIC`. A permissive policy for `PUBLIC` cannot widen a locked agent's view, because of the restrictive `_lock` policy. A restrictive policy for `PUBLIC`, such as `deleted_at IS NULL`, applies to agents as well, so it can only narrow what they see.

## Release a lock

Each `provision` drops every policy it made for an agent and creates them again from the current policy. So when you change `values`, remove a table from the agent, or delete the `rows` rule, the next `provision` updates the database to match.

When no agent's rows are locked on a table any more, and Lokra was the one that turned row-level security on there (the table has `lokra_passthrough`), `provision` drops all of Lokra's policies on it and turns row-level security off again. Tables where you turned it on yourself keep it on.

Deleting an agent from the policy does not drop its role or its policies. See [removing an agent](/docs/connect-database#removing-an-agent).

## Views

A plain Postgres view runs with its owner's rights, not the agent's. The row lock lives on the tables, so reading through such a view would skip it. For an agent with a row lock, `provision` refuses plain views, and `lokra check` reports them as errors:

```text
support-bot: order_totals is a view, which runs with its owner's rights, so the row lock would not apply through it; grant the tables it reads instead, or make it run as the agent (Postgres 15+): ALTER VIEW order_totals SET (security_invoker = true)
```

A materialized view stores its rows when it is refreshed, so no lock can reach them. It is refused the same way:

```text
support-bot: order_counts is a materialized view, which a row lock cannot reach; grant the tables it reads instead
```

A view that runs as the agent (`security_invoker`, Postgres 15 or later) is allowed, and the lock holds on the tables underneath:

```sql
ALTER VIEW order_totals SET (security_invoker = true);
```

Postgres checks a `security_invoker` view's tables with the agent's own grants. List the tables the view reads in the agent's `read` as well. That grants access and puts the lock on them; without it, the query fails with `permission denied for table orders`.

```yaml
    read:
      orders: "*"
      order_totals: "*"
```

`provision` checks views before it touches any agent role or table, so a refused view leaves your grants and tables as they were. Agents without a row lock can be given any view.

## Writes

Every lock has the same condition in `WITH CHECK` as in `USING`, so writes are held to it too:

- An `INSERT` of a row for another account fails, and so does an `UPDATE` that would move a row out of the lock. The dry run in `propose_write` hits this first, so the write is never proposed:

```text
database refused the query: new row violates row-level security policy for table "notes"
```

- An `UPDATE` or `DELETE` aimed at another account's rows matches nothing, because the agent cannot see them. The dry run reports 0 rows and warns: `changes no rows: they may not exist, or be outside this agent's scope`.

The lock does not fill in the column for you. An agent inserting a note still has to set `account_id` to 42, unless the column has a default.

See [approve writes](/docs/approvals) for the rest of the write flow.
