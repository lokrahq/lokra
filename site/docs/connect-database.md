---
title: Connect your database
description: What Lokra needs from your Postgres, how to scaffold and check a policy, and what provisioning creates in the database.
---
Lokra works with your existing Postgres database. You point `lokra init` at it to scaffold a policy, check that policy with `lokra check`, then run `lokra provision` to create one locked-down Postgres role for each agent. This page covers what each step needs and what it changes, for Supabase, Amazon RDS and any other Postgres.

## Requirements

- **Postgres that Lokra can reach** at the host and port in your policy. Agents log in there as their own roles, so the machine running Lokra needs a network path to it.
- **A login that can read the schema** for `lokra init` and `lokra check`. Both connect read-only. Your admin login works.
- **An admin login for `lokra provision`** that can create roles and owns the tables in the policy. It does not need to be a superuser. See [the admin role](#the-admin-role).
- **Python 3.11 or newer**, or the container image. See [install](/docs/install).

## Postgres versions

Lokra's lab database, demo and test suite run on Postgres 16. Older versions are not tested. Lokra uses standard features (roles, column grants, row-level security), but two of them depend on the version:

| Feature | Needs | Why it matters |
|---|---|---|
| [Row locks](/docs/row-locks) | Row-level security with restrictive policies (Postgres 10 or later) | Each locked agent gets a permissive and a restrictive policy. |
| Views for an agent with a row lock | `security_invoker` views (Postgres 15 or later) | Only views that run as the agent keep the row lock. See [views](/docs/row-locks#views). |

On Postgres 16 and later, a role with `CREATEROLE` can only manage roles it created or holds `ADMIN OPTION` on. That is why some admin setups need the extra grant described under [the admin role](#the-admin-role).

## Supabase

Use the connection string from **Project Settings → Database**. Copy the **direct connection**, not a pooler connection.

```bash
lokra init "postgresql://postgres:PASSWORD@db.PROJECT_REF.supabase.co:5432/postgres"
```

Use the Supabase `postgres` user as the admin login for `lokra provision`. It is not a superuser, but it can create roles, which is what `provision` needs. It must also own the tables in the policy; see [the admin role](#the-admin-role).

If your tables already have row-level security on, which Supabase recommends, Lokra adds its own policies next to yours and leaves yours alone. Your policies are written for roles such as `anon` and `authenticated`, so they do not apply to Lokra's agent roles; each agent sees only what Lokra's policies allow. See [row locks](/docs/row-locks#tables-that-already-use-row-level-security).

> **Warning:** Try provisioning on a development project or a branch first. It creates roles, changes grants and can turn on row-level security.

## Amazon RDS

Use the RDS master user as the admin login. It can create roles but is not a superuser, which is what `lokra provision` needs.

```bash
lokra provision --admin-dsn "postgresql://postgres:PASSWORD@mydb.abc123.ap-southeast-2.rds.amazonaws.com:5432/app"
```

Two things to check first:

- The instance's security group allows connections from the machine that runs Lokra.
- The master user owns the tables in the policy. Tables created by a separate migration user belong to that user; see [the admin role](#the-admin-role).

## Any other Postgres

Self-hosted Postgres, Postgres in Docker and other hosted services follow the same rules. You need:

- a direct connection (Lokra opens its own connections as each agent role),
- an admin login with `CREATEROLE` that owns the tables you want agents to reach,
- the database name in your policy to match the database the admin login connects to.

## Scaffold a policy with lokra init

`lokra init` connects read-only, lists the base tables in the schemas you name, and writes a starter `policies.yaml`:

```bash
lokra init "postgresql://admin:PASSWORD@db.example.com:5432/app"
```

```text
introspected 5 table(s), 25 column(s) (read-only)
flagged 3 column(s) to mask: card_number, email, phone
wrote policies.yaml

next steps:
  1. review the policy and tighten the agent's read grants
  2. lokra provision --admin-dsn "<admin connection string>"
  3. lokra connect          (adds the agent to Claude Code; --client cursor, vscode, ...)
```

The file it writes has:

- a `database` block with the host, port and database name from your connection string. The user name and password are not stored.
- one agent, `app-reader`, that can read every column of every table (`"*"`), with `max_rows: 500`.
- a `masking` block with a rule for each column that looks sensitive, plus value detectors.
- `approval_ttl_seconds: 3600`.

It only sees tables the login can see, and it skips views. It will not overwrite an existing file: pass `--out other.yaml` to write somewhere else, or `--force` to replace it.

> **Tip:** The starter agent can read everything. Treat it as a list of what exists, then cut it down. [Write a policy](/docs/policies) walks through each option.

### How init spots sensitive columns

`init` matches column names, not data. It splits each name into words on underscores, punctuation and camelCase boundaries, so `phoneNumber`, `phone_number` and `PhoneNumber` all contain the word `phone`. Keywords of five letters or more also match inside a longer word, such as `phone` in `telephone`.

| Keyword in the column name | Masking rule | Detector added |
|---|---|---|
| `medicare` | `last4` | `medicare` |
| `ihi` | `last4` | `ihi` |
| `email` | `redact` | `email` |
| `mobile`, `phone` | `last3` | `phone` |
| `fax` | `last3` | none |
| `dob`, `birth` | `year_only` | none |
| `ssn` | `last4` | `us_ssn` |
| `tfn`, `passport`, `licence`, `license`, `national` | `last4` | none |
| `card` | `redact` | `card` |
| `iban` | `redact` | `iban` |
| `bsb` | `redact` | none |
| `account` or `acct` with `number`, `no` or `num` | `redact` | none |

An account number is flagged, but `account_id` is not. Because matching is by name only, review the result: a boolean `email_verified` column is flagged as an email, and a column called `tax_file_number` is not flagged at all.

### Base detectors

Free text can hold anything, so every policy `init` writes includes the `card`, `email`, `phone` and `au_phone` detectors, whatever your column names. They scan every text value in every result, including notes, JSON and arrays. See [masking](/docs/masking#detectors).

### Other schemas

`init` reads the `public` schema unless you name others:

```bash
lokra init "postgresql://admin:PASSWORD@db.example.com:5432/app" --schemas public,billing
```

Tables outside `public` are written as `schema.table`, for example `billing.invoices`. See [schemas other than public](#schemas-other-than-public).

## Check the policy with lokra check

`lokra check` reads the database (read-only) and compares it with the policy:

```bash
lokra check --dsn "postgresql://admin:PASSWORD@db.example.com:5432/app"
```

```text
  ERROR  support-bot: reads column 'customers.nickname' which does not exist
  WARN   support-bot: can read 'customers.phone', which looks sensitive, with no masking rule or detector
  WARN   support-bot: sees every row of 'products', which has no 'account_id' column to lock rows by
  WARN   support-bot: can write 'notes' but cannot read it
  INFO   masking rule for column 'fax' matches no column in the database

1 error(s), 3 warning(s)
```

| Level | What it reports |
|---|---|
| `ERROR` | A table or column in the policy that does not exist, and views that would read past a row lock. |
| `WARN` | A sensitive-looking column an agent can read with no masking rule and no matching detector, a table the agent can read that has no column to lock rows by, and a table an agent can write but not read. |
| `INFO` | A masking rule that matches no column anywhere in the database. |

`--dsn` falls back to `LOKRA_ADMIN_DSN`. The connection must be to the database named in the policy, or `check` refuses it.

`check` exits with status 1 when it finds an error, and 0 otherwise. With `--strict` it also fails on warnings, which suits CI; see [use Lokra in CI](/docs/ci). Mistakes in the policy file itself, such as a typo like `max_row` or an unknown masking strategy, stop every command with an error before it runs; see [validation errors](/docs/policy-reference#validation-errors).

### Fail on warnings in CI

```bash
lokra check --strict --dsn "$LOKRA_ADMIN_DSN"
```

With `--strict`, warnings fail the run too. A new `phone` column that nothing masks then stops the merge instead of reaching production. `--strict` treats every warning the same, including ones you accept, such as a shared `products` table with no `account_id` column. See [run checks in CI](/docs/ci).

## Provision the agent roles

```bash
lokra provision --admin-dsn "postgresql://admin:PASSWORD@db.example.com:5432/app"
```

```text
created role lokra_support_bot: rows where account_id in [42] (accounts: id) on ['accounts', 'customers', 'notes', 'orders']; all rows on ['products'], reads ['accounts', 'customers', 'notes', 'orders', 'products'], writes ['notes']
created role lokra_ops_bot: all rows (no row lock), reads ['orders'], writes ['orders']
secrets saved to /home/dana/app/.lokra/secrets.json
```

Each agent gets a Postgres role named `lokra_` plus its name in lower case, with any run of other characters turned into `_`. `support-bot` becomes `lokra_support_bot`.

### Give provision the admin connection

Pass `--admin-dsn`, or set `LOKRA_ADMIN_DSN` so you do not have to repeat it:

```bash
export LOKRA_ADMIN_DSN="postgresql://admin:PASSWORD@db.example.com:5432/app"
lokra provision
```

The same variable is used by `check`, `pause`, `resume`, `dashboard`, `slack` and `cloud`. The admin connection must be to the database named in the policy; Lokra refuses any other. See [environment variables](/docs/environment).

### The admin role

The admin login needs:

- **`CREATEROLE`**, to create and update the agent roles.
- **Ownership of the tables in the policy**, or membership of the role that owns them. Granting access needs ownership (or `GRANT OPTION`), and a row lock needs ownership, because only a table's owner can turn on row-level security and create policies on it.
- **`ADMIN OPTION` on `lokra_agents`**, if another role created it. The first `provision` creates this group role and its creator can add agents to it. If someone else created it, run this once as a superuser:

```sql
GRANT lokra_agents TO admin_user WITH ADMIN OPTION;
```

It does not need to be a superuser. If it is one, each `provision` also resets the agent roles' attributes, for example removing a `BYPASSRLS` that someone added by hand. If it is not, Postgres does not let it reset every attribute, so `provision` stops when an agent role has gained `CREATEDB`, `REPLICATION` or `BYPASSRLS` and tells you how to remove it. An agent role that has gained `SUPERUSER` stops it too, with Postgres's own `permission denied to alter role`.

Use the same admin login every time. On Postgres 16 and later, a different `CREATEROLE` login cannot alter agent roles the first one created. [Troubleshooting](/docs/troubleshooting#provisioning) lists the errors each of these causes and the fix.

### What provision creates

For each agent, `provision` runs roughly this:

```sql
CREATE ROLE lokra_support_bot WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT
  NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 10 PASSWORD '…';
ALTER ROLE lokra_support_bot SET statement_timeout = '5s';
ALTER ROLE lokra_support_bot SET idle_in_transaction_session_timeout = '10s';
ALTER ROLE lokra_support_bot SET search_path = 'public';
GRANT lokra_agents TO lokra_support_bot;

GRANT CONNECT ON DATABASE app TO lokra_support_bot;
GRANT USAGE ON SCHEMA public TO lokra_support_bot;
GRANT SELECT (id, account_id, name, email, phone, created_at) ON customers TO lokra_support_bot;
GRANT SELECT ON orders TO lokra_support_bot;
GRANT INSERT ON notes TO lokra_support_bot;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO lokra_support_bot;
```

In detail:

- **A login role per agent** with no elevated attributes, at most 10 connections, a 5-second statement timeout and a 10-second idle-in-transaction timeout. The password is random and stored in `.lokra/secrets.json` (readable only by you). Agents never see it; they get [signed tokens](/docs/security-model) instead.
- **Membership of `lokra_agents`**, a `NOLOGIN` group role. It grants nothing; Lokra uses it to tell agent sessions apart from everyone else in row-level security policies.
- **Grants** that match the policy: `CONNECT` on the database, `USAGE` on each schema the agent touches, `SELECT` on whole tables or listed columns, `INSERT`, `UPDATE` or `DELETE` where the policy allows writes, and `USAGE` on the sequences in schemas it writes to, so inserts can fill identity and serial columns.
- **Row-level security policies** for agents with a `rows` rule. See [row locks](/docs/row-locks#what-provision-creates).

`provision` checks that every table in the policy exists, and that no agent with a row lock is given an unsafe view, before it changes anything.

### Re-running provision

Run `provision` again whenever the policy changes. It is safe to repeat:

- Passwords stay the same.
- Each agent's grants are revoked and granted again from the policy, so a table or column you remove is taken away. Schemas the agent no longer needs lose their `USAGE` grant.
- Row-level security policies are dropped and recreated. If no agent's rows are locked on a table any more, and Lokra turned row-level security on there, it turns it off again.
- A [paused](/docs/stop-agents) agent stays paused: its role keeps `NOLOGIN`.

If `provision` stops partway, for example on a permission error, fix the cause and run it again.

### Removing an agent

Deleting an agent from `policies.yaml` stops Lokra issuing it tokens, but `lokra provision` does not drop its role. Drop it yourself as the admin:

```sql
DROP OWNED BY lokra_old_bot;
DROP ROLE lokra_old_bot;
```

`DROP OWNED BY` removes the role's grants and its row-level security policies. Then run `lokra provision` so Lokra turns off row-level security on any table it no longer needs it for.

## Schemas other than public

Name tables outside `public` as `schema.table`. Unqualified names mean `public`:

```yaml
agents:
  billing-bot:
    rows: {column: account_id, values: [42]}
    read:
      billing.invoices: "*"
      customers: [id, name]
```

Lokra grants the agent `USAGE` on only the schemas it needs, plus `public`, and takes a schema away again when its last table leaves the policy. A `rows.tables` mapping uses the same names, for example `tables: {billing.accounts: id}`.

Agent roles have `search_path` set to `public`, so agents must write the schema in their SQL too:

```sql
SELECT id, total FROM billing.invoices WHERE status = 'open';
```

An unqualified `FROM invoices` fails with `relation "invoices" does not exist`. The agent's `list_tables` tool shows tables with their schema, so agents pick this up.

## Encrypted connections

The policy's `database` block holds only `host`, `port` and `dbname`. Lokra connects through libpq, which uses TLS when the server offers it (`sslmode=prefer`). To insist on TLS, or to verify the server's certificate, set the standard libpq variables where Lokra runs:

```bash
export PGSSLMODE=verify-full
export PGSSLROOTCERT=/etc/ssl/certs/db-ca.pem
```

These apply to agent connections and to admin connections. You can also put `?sslmode=require` on the admin connection string itself.

## Next steps

- [Write a policy](/docs/policies): decide what each agent can read and change.
- [Row locks](/docs/row-locks): limit an agent to one account's rows.
- [Connect AI clients](/docs/connect-clients): add the agent to Claude Code, Cursor and others.
