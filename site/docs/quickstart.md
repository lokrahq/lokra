---
title: Quickstart
description: Install Lokra, watch it work against a throwaway database, then connect an agent to your own Postgres.
---
This takes about ten minutes. You will install the `lokra` command, run the built-in demo, then put one read-only agent in front of your own database and connect it to Claude Code.

## 1. Install

```bash
brew install lokrahq/tap/lokra
```

No Homebrew? `curl -fsSL https://lokra.dev/install.sh | sh` installs the same build, and `pipx install lokra` or `uv tool install lokra` work if you prefer Python. Check it is on your path:

```bash
lokra --version
```

Windows, the container image and every other option are on the [install](/docs/install) page.

## 2. Watch the demo

```bash
lokra demo
```

The demo starts a throwaway Postgres in Docker, loads a small fake SaaS database and walks two agents through row locks, masking, refused injections, an approved write, a rate limit, a pause and the signed ledger. It removes everything when it finishes.

No Docker? Point it at any Postgres where you can create roles. It works in its own schema and cleans up after itself:

```bash
lokra demo --dsn "postgresql://admin:password@localhost:5432/postgres"
```

## 3. Scaffold a policy from your database

Run this from the folder where you want `policies.yaml` to live. It connects **read-only**, lists your tables and flags columns that look sensitive:

```bash
lokra init "postgresql://app_user:password@db.example.com:5432/shop"
```

```text
introspected 6 table(s), 41 column(s) (read-only)
flagged 3 column(s) to mask: card_last4, email, phone
wrote policies.yaml

next steps:
  1. review the policy and tighten the agent's read grants
  2. lokra provision --admin-dsn "<admin connection string>"
  3. lokra connect          (adds the agent to Claude Code; --client cursor, vscode, ...)
```

The starter policy gives one agent, `app-reader`, read access to every table. Open `policies.yaml` and cut it down to what the agent needs. [Write a policy](/docs/policies) walks through every option. A small first version could be:

```yaml
database: {host: db.example.com, port: 5432, dbname: shop}
agents:
  app-reader:
    description: Answers questions about orders. Read only.
    read:
      orders: "*"
      customers: [id, name, email, created_at]
    max_rows: 200
masking:
  columns: {email: redact, phone: last3}
  detectors: [card, email, phone, au_phone]
```

## 4. Check it against the database

```bash
lokra check --dsn "postgresql://app_user:password@db.example.com:5432/shop"
```

`check` reads the schema and reports tables or columns that do not exist, sensitive-looking columns an agent can read with nothing masking them, and writes without a matching read. Fix anything marked `ERROR` before you go on.

## 5. Provision the agent's role

```bash
lokra provision --admin-dsn "postgresql://postgres:password@db.example.com:5432/shop"
```

This creates one Postgres login role per agent, grants it exactly what the policy lists, and adds row-level security policies for any row locks. The admin login needs to be able to create roles; it does not need to be a superuser, so the Supabase `postgres` user and the RDS master user work. Agent passwords are stored in `.lokra/secrets.json` next to your policy, readable only by you.

> **Warning:** Try this against a development or staging database first. Provisioning changes roles and grants, and turns on row-level security for tables that need row locks.

## 6. Connect Claude Code

```bash
lokra connect
```

`connect` logs in as the agent's role to check it works, then adds a `lokra-app-reader` server to `.mcp.json` in the current folder. For other clients, add `--client claude-desktop`, `cursor`, `vscode` or `windsurf`. See [connect AI clients](/docs/connect-clients).

Open Claude Code in that folder, allow the `lokra-app-reader` server when asked, and try:

```text Prompt
What tables can you see?
```

Then ask for something that touches a masked column, such as recent customers with their email. The `email` column comes back as `[REDACTED]`, from the masking rule, and any email address inside free text, such as a note, comes back as `[EMAIL]`, from the detector.

## 7. Approve a write

Give the agent write access to one table, provision again, and ask it to change something:

```yaml
    write:
      orders: [update]
```

The agent's `propose_write` call is dry-run and held. List and approve it from the terminal:

```bash
lokra pending
```

```bash
lokra approve w_3f9c1a2b
```

The agent then calls `execute_write` to run it. Approvals also work from a [local dashboard, Slack or your phone](/docs/approvals#where-to-approve), and [approval rules](/docs/approvals#approval-rules) can let small writes through on their own.

## Next steps

- [Masking](/docs/masking): every strategy and detector.
- [Row locks](/docs/row-locks): limit an agent to one customer's rows.
- [Run in production](/docs/production): HTTP mode, tokens and the container image.
- [Lokra Cloud](/docs/cloud-gates): approve writes and pause agents from your phone.
