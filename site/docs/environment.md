---
title: Environment variables
description: Every environment variable Lokra reads, what it does and which commands use it.
---
Command-line flags win over environment variables, and environment variables win over defaults. Keep secrets such as tokens and admin connection strings in your secret manager or service definition, not in shell history.

## Configuration

| Variable | Default | What it does |
|---|---|---|
| `LOKRA_CONFIG` | `./policies.yaml` | Path to the policy file. `--config` sets it too. |
| `LOKRA_HOME` | `.lokra` next to the policy file | State folder: signing key, agent passwords, pending writes, ledger, pause state. `--home` sets it too. |

## Database administration

| Variable | Used by | What it does |
|---|---|---|
| `LOKRA_ADMIN_DSN` | `check`, `provision`, `pause`, `resume`, `dashboard`, `slack`, `cloud` | Admin connection string, used when `--admin-dsn` (or `--dsn` for `check`) is not given. With it, a pause also blocks the agent's database login and ends its sessions. |

The admin connection must be to the database the policy names; Lokra refuses a connection to any other database. Lokra never guesses an admin connection for a real database. The only fallback is the lab database used by the test suite, and only when the policy points at it.

## Agent tokens

| Variable | Used by | What it does |
|---|---|---|
| `LOKRA_TOKEN` | `lokra serve` (stdio) | The agent's signed token. Create one with `lokra mint-token <agent>`; it lasts at most 8 hours. `serve --dev-agent` sets it for you on a local machine. |

Over HTTP (`lokra serve --http`), each request carries its own token in an `Authorization: Bearer` header instead. See [run in production](/docs/production).

## Lokra Cloud

| Variable | Default | What it does |
|---|---|---|
| `LOKRA_CLOUD_TOKEN` | none | The gate token from **Gates → Create token** in Lokra Cloud. `lokra cloud` needs it and refuses to start without it. |
| `LOKRA_CLOUD_URL` | `https://cloud.lokra.dev` | Where `lokra cloud` syncs. `--url` sets it too. Only change it for a self-hosted or local copy of Cloud. |

## Slack

| Variable | What it does |
|---|---|
| `LOKRA_SLACK_BOT_TOKEN` | The Slack bot token (`xoxb-…`) for `lokra slack`. |
| `LOKRA_SLACK_APP_TOKEN` | The Slack app-level token (`xapp-…`) with `connections:write`, for Socket Mode. |

[Approve in Slack](/docs/slack) walks through creating both.

## Output and debugging

| Variable | What it does |
|---|---|
| `NO_COLOR` | Turns off colour in terminal output. |
| `FORCE_COLOR` | Keeps colour even when output is not a terminal. |
| `LOKRA_DEBUG` | Prints the full Python traceback instead of a one-line error. Useful when reporting a bug. |
