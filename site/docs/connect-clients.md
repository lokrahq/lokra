---
title: Connect AI clients
description: Add a Lokra agent to Claude Code, Claude Desktop, Cursor, VS Code, Windsurf or any other MCP client, over stdio or HTTP.
---
An AI client talks to Lokra through MCP. On your own machine, the client starts `lokra serve` as a local process and the agent's tools appear in the chat. `lokra connect` writes that client config for you, after checking that the agent can log in to the database.

Before you start, you need a policy with at least one agent, and that agent provisioned with `lokra provision`. See [connect your database](/docs/connect-database) and [quickstart](/docs/quickstart) if you have not done that yet.

## Connect in one command

Run `lokra connect` in the folder that holds `policies.yaml`:

```bash
lokra connect
```

```text
  ✓ support-bot logs in to app and can read 3 tables
  ✓ Added lokra-support-bot to .mcp.json for Claude Code

  Next: Start Claude Code in this folder and allow lokra-support-bot when it asks.
  Then ask it: "What tables can you see?"
```

With no flags, Lokra connects the only agent in the policy to Claude Code. When the policy has more than one agent, name the one you want:

```bash
lokra connect support-bot
```

If you leave the name out, Lokra stops and lists the agents it found:

```text
error: say which agent to connect, one of: support-bot, ops-bot
```

## Supported clients

Pick a client with `--client`. The default is `claude-code`.

| `--client` | Client | File Lokra writes | Top-level key |
|---|---|---|---|
| `claude-code` | Claude Code | `.mcp.json` in the current folder | `mcpServers` |
| `claude-desktop` | Claude Desktop | per-OS path, see below | `mcpServers` |
| `cursor` | Cursor | `.cursor/mcp.json` in the current folder | `mcpServers` |
| `vscode` | VS Code (Copilot Chat agent mode) | `.vscode/mcp.json` in the current folder | `servers` |
| `windsurf` | Windsurf | `~/.codeium/windsurf/mcp_config.json` | `mcpServers` |

Claude Code, Cursor and VS Code read a config file from the project folder, so run `lokra connect` from the folder you will open in the client. Claude Desktop and Windsurf read one file in your home folder, so the server is available everywhere.

Claude Desktop keeps its config here:

| OS | Path |
|---|---|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Linux | `~/.config/Claude/claude_desktop_config.json` |

> **Note:** Lokra runs on macOS and Linux. It uses POSIX file locks for its ledger and controls, so it does not run natively on Windows.

After writing the file, Lokra tells you the next step for that client:

| Client | Next step |
|---|---|
| Claude Code | Start Claude Code in this folder and allow the server when it asks. |
| Claude Desktop | Quit and reopen Claude Desktop. |
| Cursor | Open this folder in Cursor and switch the server on in its MCP settings. |
| VS Code | Open this folder in VS Code; the server appears in Copilot Chat's agent tools. |
| Windsurf | Refresh the MCP servers in Windsurf. |

Then ask the agent something like "What tables can you see?". It should call `list_tables` and answer with the tables in its policy.

## What Lokra writes

For Claude Code, Claude Desktop, Cursor and Windsurf, the entry looks like this:

```json
{
  "mcpServers": {
    "lokra-support-bot": {
      "command": "/Users/dana/.local/bin/lokra",
      "args": [
        "--config",
        "/Users/dana/projects/shop/policies.yaml",
        "serve",
        "--dev-agent",
        "support-bot"
      ]
    }
  }
}
```

VS Code uses a `servers` key and needs a `type`, so its entry is:

```json
{
  "servers": {
    "lokra-support-bot": {
      "type": "stdio",
      "command": "/Users/dana/.local/bin/lokra",
      "args": [
        "--config",
        "/Users/dana/projects/shop/policies.yaml",
        "serve",
        "--dev-agent",
        "support-bot"
      ]
    }
  }
}
```

Two more arguments appear when they apply:

- `--home <path>` goes before `serve` when you keep Lokra's state somewhere other than `.lokra` next to the policy (you passed `--home` or set `LOKRA_HOME`).
- `--purpose <purpose>` goes after the agent name when you pass `--purpose` to `lokra connect`.

### Why the paths are absolute

Desktop apps such as Claude Desktop and Cursor do not start with your shell's `PATH`, so a bare `lokra` command often fails with "command not found". Lokra writes the full path of the `lokra` you ran instead. If you ran it as `python -m lokra`, the entry uses the full path of that Python with `-m lokra`.

The policy path is absolute too, because the client may start the server from a different working folder. If you move the project or reinstall Lokra somewhere else, run `lokra connect` again to update the entry.

### What the server does when the client starts it

`serve --dev-agent support-bot` issues the agent an 8-hour token each time the client starts it, on behalf of your username, and serves MCP over stdio. Lokra logs in to the database as the agent's role on its behalf, so the agent never sees a database password. If a session runs past 8 hours, the agent's calls start failing with `token has expired, mint a new one`; restart the server from the client to get a fresh token.

If `LOKRA_TOKEN` is set in the server's environment, Lokra uses that token and does not issue one.

> **Warning:** `--dev-agent` suits an agent on your own machine. Anyone who can start that command can act as the agent, so do not use it on shared or production hosts. See [run in production](/docs/production).

## The login check

Before it writes anything, `lokra connect` logs in to the database as the agent's own role (for `support-bot`, the role `lokra_support_bot`) with the password `lokra provision` saved in `.lokra/secrets.json`. It connects read-only with a five-second timeout, counts the tables the role may select from, and prints the result:

```text
  ✓ support-bot logs in to app and can read 3 tables
```

This catches the common problems before you switch to the client. The check connects to the host and port in the policy, from the machine where you run `lokra connect`.

| Problem | What you see |
|---|---|
| Agent not provisioned | `error: support-bot is not provisioned yet; run lokra provision --admin-dsn "<admin connection string>" first` |
| Login refused or database unreachable | `error: support-bot could not log in to app at db.internal:5432: <reason from Postgres> (--no-check writes the config anyway)` |

Pass `--no-check` to skip the login and write the config anyway, for example when the database is only reachable from another network.

## Existing entries and other servers

Lokra adds or updates one server and keeps everything else in the file, including other MCP servers. It writes to a temporary file and renames it into place, keeping the file's permissions. The file is rewritten with two-space indentation.

| What is already there | What happens |
|---|---|
| No file, or an empty file | Lokra creates it, and its folder if needed. Prints `Added`. |
| The same entry | Nothing changes. Prints `is already in`. |
| A Lokra entry with the same name but different arguments | Lokra replaces it. Prints `Updated`. |
| A different server with the same name | Lokra stops: `already has a server called lokra-support-bot that is not Lokra; choose another --name, or --force to replace it`. |
| A file that is not plain JSON, such as JSON with comments | Lokra stops and changes nothing: `is not plain JSON (...); add the entry by hand from lokra connect --print`. |

Lokra recognises its own entry by its arguments: a server whose `args` contain both `serve` and `--config`.

## Flags

| Flag | What it does |
|---|---|
| `agent` | The agent from the policy. Optional when the policy has only one. |
| `--client` | Where to add it: `claude-code` (default), `claude-desktop`, `cursor`, `vscode` or `windsurf`. |
| `--name` | The server name in the client. Default: `lokra-<agent>`. |
| `--purpose` | A declared purpose-of-use, added to the agent's session token and recorded on every request. |
| `--print` | Print the config entry instead of writing it. Skips the login check. |
| `--no-check` | Skip logging in as the agent. |
| `--force` | Replace a server of the same name that is not Lokra. |

The global `--config` and `--home` options go before the command, for example `lokra --config ./policies.yaml connect`. The [CLI reference](/docs/cli#lokra-connect) lists them all.

### Purpose of use

If the policy sets `require_purpose: true`, `lokra connect` refuses to write an entry without `--purpose`. If the agent lists `purposes`, the purpose must be one of them:

```bash
lokra connect support-bot --purpose customer-support
```

See [policies](/docs/policies) for how purposes work.

### Two agents in one client

Connect each agent separately. Each gets its own server entry, so the client sees two sets of tools:

```bash
lokra connect support-bot
lokra connect ops-bot
```

## Any other MCP client

For a client Lokra does not know, print the entry and paste it into that client's config:

```bash
lokra connect support-bot --print
```

The output is the same JSON shown above, under `mcpServers`. Most stdio clients want the same `command` and `args`; adapt the outer key to what your client expects. `--print --client vscode` prints the `servers` form with `"type": "stdio"`.

You can also wire Claude Code by hand with a token you mint yourself, instead of `--dev-agent`:

```bash
claude mcp add shop-db \
  -e LOKRA_TOKEN="$(lokra mint-token support-bot --ttl 28800)" \
  -- "$(command -v lokra)" --config "$PWD/policies.yaml" serve
```

The token lasts at most 8 hours (28800 seconds), so you will need to mint a new one and update the server after that.

## Connect over HTTP

Agents on other machines, and hosted agents, connect to one long-running `lokra serve --http` instead of starting their own process. Each request carries the agent's token in an `Authorization: Bearer` header. Set up the server and TLS first, as described in [run in production](/docs/production#serve-over-http).

Mint a token for the agent on the Lokra host:

```bash
lokra mint-token support-bot --ttl 28800
```

Then add the server to Claude Code with that token:

```bash
claude mcp add --transport http lokra https://lokra.internal.example/mcp \
  --header "Authorization: Bearer <token>"
```

The MCP endpoint is always `/mcp`. Other clients that support remote MCP servers over streamable HTTP take the same URL and header; check your client's documentation for where to put them.

A call without a valid token is refused and recorded in the ledger as `denied`. Because tokens expire within 8 hours, something has to mint and hand over fresh ones: a person, or a job you run on the Lokra host.

## Next steps

- See every tool the agent gets in the [MCP tools reference](/docs/mcp-tools).
- Decide how writes get approved in [approvals](/docs/approvals) and [approve in Slack](/docs/slack).
- Learn how to pause an agent or revoke a token in [stop an agent](/docs/stop-agents).
- If the client does not show the tools, see [troubleshooting](/docs/troubleshooting).
