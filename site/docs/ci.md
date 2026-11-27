---
title: Use Lokra in CI
description: Run lokra check --strict in GitHub Actions against a throwaway Postgres with your migrations applied, so policy mistakes fail the build.
---
Your schema changes more often than your policy. A migration that adds `customers.phone` gives every agent with `customers: "*"` a new sensitive column, unmasked, the moment it reaches production. `lokra check` catches that: it reads the schema and compares it with `policies.yaml`. Run it in CI against a database with your migrations applied, and the pull request fails before the column ships.

## What lokra check does

```bash
lokra check --dsn "postgresql://postgres:postgres@localhost:5432/app" --strict
```

It connects read-only, reads the tables, views and columns in the schemas your agents use, and reports what does not line up:

```text
  WARN   support-bot: can read 'customers.phone', which looks sensitive, with no masking rule or detector

0 error(s), 1 warning(s)
```

When nothing is wrong:

```text
  OK     policy matches the database, nothing sensitive left unmasked

0 error(s), 0 warning(s)
```

It needs no agent roles, no `lokra provision`, no state directory and no admin rights: any login that can connect to the database will do, and it writes nothing. That makes it safe to run against a throwaway CI database.

| Flag | What it does |
|---|---|
| `--dsn` | Connection string to check against. Default: `$LOKRA_ADMIN_DSN`. |
| `--strict` | Fail on warnings too, not only errors. |

The global `--config` option points at the policy if it is not `./policies.yaml`, for example `lokra --config infra/policies.yaml check --strict`.

## What fails the build

`lokra check` exits with status 1 when it finds an error. With `--strict`, it also exits with status 1 on any warning. Notes marked `INFO` never fail the build.

| Level | Finding | How to fix it |
|---|---|---|
| `ERROR` | `reads table 'invoices' which does not exist` | Remove the table from the policy, or check the migration that should create it. |
| `ERROR` | `reads column 'customers.mobile' which does not exist` | Fix the column name in the agent's read list. |
| `ERROR` | `writes table 'refunds' which does not exist` | Remove it from `write`, or fix the name. |
| `ERROR` | `<view> is a view, which runs with its owner's rights, so the row lock would not apply through it` | Grant the tables it reads instead, or make it `security_invoker` (Postgres 15+). See [row locks](/docs/row-locks). |
| `ERROR` | `<view> is a materialized view, which a row lock cannot reach` | Grant the tables it reads instead. |
| `WARN` | `can read 'customers.phone', which looks sensitive, with no masking rule or detector` | Add a [masking](/docs/masking) rule for the column, or drop it from the agent's reads. |
| `WARN` | `sees every row of 'products', which has no 'account_id' column to lock rows by` | Map the table to its scope column with `rows.tables`, or remove it. See [row locks](/docs/row-locks). |
| `WARN` | `can write 'orders' but cannot read it` | Give the agent read access, so it can see what it is changing. |
| `INFO` | `masking rule for column 'fax' matches no column in the database` | Optional: remove the rule if you no longer need it. |

Lokra also stops with an error if it cannot connect, or if the connection is to a different database from the one the policy names:

```text
error: the admin connection is to database 'postgres', but the policy is for 'app'
```

Use `--strict` in CI. Without it, the unmasked `phone` column above is only a warning and the build passes.

## GitHub Actions

This workflow starts Postgres as a service container, applies your migrations, installs Lokra and checks the policy on every pull request and every push to `main`:

```yaml GitHub Actions
name: Lokra policy

on:
  pull_request:
  push:
    branches: [main]

jobs:
  lokra-check:
    runs-on: ubuntu-latest

    services:
      postgres:
        image: postgres:16-alpine
        env:
          POSTGRES_USER: postgres
          POSTGRES_PASSWORD: postgres
          POSTGRES_DB: app
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U postgres -d app"
          --health-interval 2s
          --health-timeout 3s
          --health-retries 30

    env:
      DATABASE_URL: postgresql://postgres:postgres@localhost:5432/app

    steps:
      - uses: actions/checkout@v4

      - name: Apply migrations
        run: |
          for f in db/migrations/*.sql; do
            psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f"
          done

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install Lokra
        run: pip install "lokra==0.8.2"

      - name: Check the policy against the schema
        run: lokra check --dsn "$DATABASE_URL" --strict
```

Three things to get right:

- **The database name.** `POSTGRES_DB` must match `database.dbname` in `policies.yaml`, because Lokra refuses to check a database the policy does not name. The policy's `host` and `port` do not matter here; `--dsn` decides where Lokra connects.
- **The schema, not the data.** The check only reads table and column definitions, so migrations are enough. You do not need seed data.
- **A pinned version.** Pin Lokra as you would any other tool, and upgrade it on purpose, so a new release cannot change your build without a pull request.

### Apply your migrations

Replace the `Apply migrations` step with whatever your project already uses. The runner needs whatever your tool needs, such as Node.js or your app's Python dependencies, installed before this step.

| Tool | Command |
|---|---|
| Plain SQL files | `psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f <file>` for each file, in order |
| Prisma | `npx prisma migrate deploy` |
| Alembic | `alembic upgrade head` |
| Django | `python manage.py migrate` |
| Rails | `bin/rails db:migrate` |
| Flyway | `flyway -url=jdbc:postgresql://localhost:5432/app -user=postgres -password=postgres migrate` |

Most of these read the connection from an environment variable such as `DATABASE_URL`, which the job sets for every step.

### Policy in a subfolder

If the policy is not at the repository root, pass `--config` before the command:

```yaml GitHub Actions
      - name: Check the policy against the schema
        run: lokra --config infra/lokra/policies.yaml check --dsn "$DATABASE_URL" --strict
```

### Run it only when it matters

The check takes a few seconds, so running it on every pull request is simplest. If you would rather run it only when the schema or policy changes, add path filters:

```yaml GitHub Actions
on:
  pull_request:
    paths:
      - "db/migrations/**"
      - "policies.yaml"
```

If you use path filters, make sure they cover every place a migration can live. A migration that slips past the filter is exactly the change this check is for.

## Other CI systems

Any CI that can start Postgres can run the same three steps: apply migrations, `pip install lokra`, then `lokra check --dsn "<dsn>" --strict`. Lokra needs Python 3.11 or later. The [container image](/docs/production#run-in-a-container) works too, if your CI prefers containers:

```bash
docker run --rm -v "$PWD:/work" ghcr.io/lokrahq/lokra:0.8.2 \
  check --dsn "postgresql://postgres:postgres@db:5432/app" --strict
```

The database host must be reachable from inside that container, for example a service on the same Docker network.

## After the check passes

CI proves the policy matches the schema. It does not change production. When the change merges, apply it to the real database with `lokra provision` and restart the long-running Lokra processes; see [change the policy](/docs/production#change-the-policy).

## The conformance suite

The Lokra repository also contains a conformance suite, in `conformance/`, that sends hostile SQL through a database access layer and scores twelve safety guarantees, such as refusing stacked statements and keeping one tenant's rows from another. It is for testing a layer itself, against the repository's disposable lab database. It is not part of the `lokra` package and does not check your policy, so it does not belong in your app's CI.

> **Warning:** Run the conformance suite only against layers you operate, pointed at a disposable lab database. If you find a failure in someone else's project, report it to them privately first. See the rules in [conformance/README.md](https://github.com/lokrahq/lokra/blob/main/conformance/README.md).
