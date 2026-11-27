---
title: Run in production
description: Serve Lokra over HTTP with tokens and TLS, run it in a container or under systemd, and back up, change and upgrade it safely.
---
On a laptop, each AI client starts its own `lokra serve` process. In production you usually run one long-lived Lokra next to your database that many agents reach over HTTP, plus `lokra cloud` or `lokra slack` for approvals. This page covers both transports, tokens, TLS, the container image, systemd, where state lives, and how to change and upgrade a running deployment.

## Stdio or HTTP

| | stdio | HTTP |
|---|---|---|
| Command | `lokra serve` | `lokra serve --http` |
| Who starts it | The AI client, once per session | You, as a service |
| Where the agent runs | Same machine as Lokra | Anywhere that can reach Lokra |
| Token | `LOKRA_TOKEN` in the process environment, or `--dev-agent` | `Authorization: Bearer` header on every request |
| Agents per process | One | Many: each request is checked against its own token |

Use stdio for an agent on your own machine; [connect AI clients](/docs/connect-clients) sets it up. Use HTTP for always-on agents, hosted agents and agents on other machines.

## Serve over HTTP

```bash
lokra serve --http --host 127.0.0.1 --port 8787
```

```text
lokra: MCP over HTTP at http://127.0.0.1:8787/mcp
```

| Flag | Default | What it does |
|---|---|---|
| `--http` | off | Serve MCP over HTTP instead of stdio. |
| `--host` | `127.0.0.1` | Address to bind. |
| `--port` | `8787` | Port to listen on. |

The MCP endpoint is `/mcp`. The server is stateless and answers each request with JSON, so it needs no sticky sessions behind a proxy. Listing the tools needs no token; every tool call does, and a call without a valid token is refused and written to the ledger as `denied`.

`--dev-agent` is refused with `--http`, because over HTTP each agent must send its own token.

When you bind to anything other than `127.0.0.1`, `localhost` or `::1`, Lokra reminds you:

```text
lokra: listening beyond this machine; put TLS in front, since agent tokens travel in the Authorization header
```

> **Note:** When Lokra binds to `127.0.0.1` (the default), it only accepts requests whose `Host` header is `127.0.0.1`, `localhost` or `[::1]`, and answers others with `421 Invalid Host header`. A reverse proxy on the same machine must therefore send `Host: 127.0.0.1:8787`, as in the Caddy example below. The container binds to `0.0.0.0`, where this check does not apply.

### What the server needs

- **The policy and state directory.** It reads `policies.yaml` once at start-up, and reads and writes `.lokra` on every request.
- **A network path to Postgres.** Lokra logs in as each agent's own role at the host and port in the policy, with the password from `.lokra/secrets.json`. Your `pg_hba.conf` and firewall must allow password logins for the `lokra_*` roles from the Lokra host.
- **No admin connection.** `lokra serve` never uses `LOKRA_ADMIN_DSN`, so keep the admin connection string out of its environment.

Each tool call opens its own database connection as the agent's role. `lokra provision` limits each agent role to 10 connections, a 5-second statement timeout and a 10-second idle-in-transaction timeout, so one agent cannot exhaust the database.

## Tokens

Each agent gets a short-lived token signed with the key in `.lokra/signing.key`. Mint one on the Lokra host:

```bash
lokra mint-token support-bot --ttl 28800 --on-behalf-of dana --purpose customer-support
```

| Flag | Default | What it does |
|---|---|---|
| `agent` | required | The agent from the policy. Unknown names are refused. |
| `--ttl` | `3600` | Lifetime in seconds. The maximum is 8 hours (`28800`). |
| `--on-behalf-of` | your username | The person the agent acts for, recorded on every request. |
| `--purpose` | none | A declared purpose-of-use, recorded on every request. See [policies](/docs/policies). |

The token looks like `lokra1.<payload>.<signature>`. Its payload holds the agent name, the person it acts for, the issue and expiry times, a token id and the purpose, if any. Lokra checks the signature and expiry on every call, so a forged or expired token fails:

| Problem | What the agent sees |
|---|---|
| No token | `access denied: no agent token supplied (LOKRA_TOKEN, or an Authorization: Bearer header over HTTP)` |
| Altered or signed with another key | `access denied: token signature is invalid` |
| Expired | `access denied: token has expired, mint a new one` |
| TTL over 8 hours | `error: ttl must be between 1 and 28800 seconds` (from `mint-token`) |

Hand the token to the agent in one of two ways:

- **stdio:** set `LOKRA_TOKEN` in the environment of `lokra serve`.
- **HTTP:** send it on every request as `Authorization: Bearer <token>`.

Tokens are not refreshed for you. Plan for something to mint a new token at least every 8 hours, such as a job on the Lokra host that hands it to the agent's platform. To cut off one token early, revoke it; see [stop an agent](/docs/stop-agents#revoke-one-token).

> **Warning:** Anyone with `.lokra/signing.key` can mint a token for any agent. Keep it readable only by the user that runs Lokra, and never copy it to agent hosts.

## Put TLS in front

Tokens are bearer credentials, so never send them over plain HTTP beyond the local machine. Run Lokra on `127.0.0.1` and put a reverse proxy that terminates TLS in front of it. Caddy is a short example because it gets and renews certificates by itself:

```text Caddyfile
lokra.example.com {
	reverse_proxy 127.0.0.1:8787 {
		header_up Host {upstream_hostport}
	}
}
```

The `header_up Host` line sends `Host: 127.0.0.1:8787` to Lokra, which it requires when bound to `127.0.0.1`. For a host that is only reachable inside your network, replace the automatic certificate with your own, or add `tls internal` inside the block to use Caddy's internal CA.

Agents then connect to `https://lokra.example.com/mcp`. Also limit who can reach the port at all, with a firewall, security group or VPN: a valid token is all a caller needs.

> **Warning:** Never expose `lokra serve --http` to another machine without TLS.

## Run in a container

Each release is published to `ghcr.io/lokrahq/lokra` for `linux/amd64` and `linux/arm64`.

| Tag | Follows |
|---|---|
| `0.8.2` | That exact release. Pin this in production. |
| `0.8` | The latest patch release of 0.8. |
| `latest` | The latest release. |

The image runs `lokra serve --http --host 0.0.0.0 --port 8787`, with `/work` as its working folder. It reads `/work/policies.yaml` and keeps its state in `/work/.lokra`, so mount a directory that persists. Its entrypoint is `lokra`, so anything after the image name is a Lokra command. The image includes the Slack extra.

```bash
docker run -d --name lokra -p 127.0.0.1:8787:8787 \
  --user "$(id -u):$(id -g)" -v "$PWD:/work" ghcr.io/lokrahq/lokra:0.8.2
```

Run other commands against the same mount:

```bash
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/work" ghcr.io/lokrahq/lokra:0.8.2 \
  provision --admin-dsn "postgresql://admin:secret@db.internal:5432/app"
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/work" ghcr.io/lokrahq/lokra:0.8.2 \
  mint-token support-bot --ttl 28800
```

Two things trip people up:

- **File ownership.** The image runs as user `lokra` (uid 10001). The mounted directory must be writable by the container's user, so either pass `--user` matching the directory's owner, as above, or make the directory owned by uid 10001.
- **The database host.** Inside the container, `127.0.0.1` is the container itself. The `host` in `policies.yaml` must be reachable from the container: a hostname on your network, a Compose service name, or `host.docker.internal` for a database on the Docker host (on Linux, add `--add-host=host.docker.internal:host-gateway`).

### Docker Compose

This runs the HTTP server, `lokra cloud` and Caddy, all sharing one state directory. Put `policies.yaml` in `./lokra` and make that directory owned by uid 10001 (`sudo chown -R 10001:10001 lokra`).

```yaml compose.yaml
services:
  lokra:
    image: ghcr.io/lokrahq/lokra:0.8.2
    restart: unless-stopped
    volumes:
      - ./lokra:/work
    expose:
      - "8787"

  lokra-cloud:
    image: ghcr.io/lokrahq/lokra:0.8.2
    restart: unless-stopped
    command: ["cloud"]
    env_file: ./cloud.env
    volumes:
      - ./lokra:/work

  caddy:
    image: caddy:2
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
    depends_on:
      - lokra

volumes:
  caddy_data:
```

Inside Compose, Caddy reaches Lokra by its service name, and no `Host` rewrite is needed because the container binds to `0.0.0.0`:

```text Caddyfile
lokra.example.com {
	reverse_proxy lokra:8787
}
```

`cloud.env` holds the secrets for `lokra cloud`. Keep it out of version control:

```ini cloud.env
LOKRA_CLOUD_TOKEN=lkg_your_gate_token
LOKRA_ADMIN_DSN=postgresql://lokra_admin:secret@db.internal:5432/app
```

Provision and mint tokens with one-off containers:

```bash
docker compose run --rm lokra provision --admin-dsn "postgresql://lokra_admin:secret@db.internal:5432/app"
docker compose run --rm lokra mint-token support-bot --ttl 28800
```

To approve in Slack instead of, or as well as, Lokra Cloud, add a service with `command: ["slack"]` and the Slack tokens in its env file. See [approve in Slack](/docs/slack).

## Run with systemd

On a plain Linux host, install Lokra into its own virtual environment so the service has a fixed path. It needs Python 3.11 or later.

```bash
sudo useradd --system --home-dir /srv/lokra --create-home --shell /usr/sbin/nologin lokra
sudo python3 -m venv /opt/lokra
sudo /opt/lokra/bin/pip install lokra
```

Put `policies.yaml` in `/srv/lokra` and run every Lokra command for this deployment as the `lokra` user, because the state files are readable only by their owner:

```bash
sudo -u lokra /opt/lokra/bin/lokra --config /srv/lokra/policies.yaml provision --admin-dsn "postgresql://lokra_admin:secret@db.internal:5432/app"
```

### The HTTP server

Save as `/etc/systemd/system/lokra.service`:

```ini lokra.service
[Unit]
Description=Lokra MCP server
After=network-online.target
Wants=network-online.target

[Service]
User=lokra
Group=lokra
WorkingDirectory=/srv/lokra
ExecStart=/opt/lokra/bin/lokra --config /srv/lokra/policies.yaml serve --http --host 127.0.0.1 --port 8787
Environment=PYTHONUNBUFFERED=1
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/srv/lokra

[Install]
WantedBy=multi-user.target
```

### The Lokra Cloud connector

Save the secrets as `/etc/lokra/cloud.env`, owned by root with mode 600:

```ini cloud.env
LOKRA_CLOUD_TOKEN=lkg_your_gate_token
LOKRA_ADMIN_DSN=postgresql://lokra_admin:secret@db.internal:5432/app
```

Then `/etc/systemd/system/lokra-cloud.service`:

```ini lokra-cloud.service
[Unit]
Description=Lokra Cloud connector
After=network-online.target
Wants=network-online.target

[Service]
User=lokra
Group=lokra
WorkingDirectory=/srv/lokra
EnvironmentFile=/etc/lokra/cloud.env
ExecStart=/opt/lokra/bin/lokra --config /srv/lokra/policies.yaml cloud
Environment=PYTHONUNBUFFERED=1
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/srv/lokra

[Install]
WantedBy=multi-user.target
```

Start both and follow their output:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now lokra lokra-cloud
journalctl -u lokra -u lokra-cloud -f
```

`PYTHONUNBUFFERED=1` makes Lokra's messages reach the journal straight away. With `LOKRA_ADMIN_DSN` set, a pause from Lokra Cloud also blocks the agent's database login; see [Lokra Cloud](/docs/cloud). A `lokra slack` service looks the same, with `slack` in place of `cloud` and the Slack tokens in its environment file.

## Where state lives

Lokra keeps its state in `.lokra` next to `policies.yaml`, or wherever `--home` or `LOKRA_HOME` points.

| File | What it holds | If you lose it |
|---|---|---|
| `signing.key` | The key that signs tokens and ledger entries | Every token stops working, and the existing ledger can no longer be verified |
| `secrets.json` | Each agent role's database password | Run `lokra provision` again; it sets new passwords |
| `pending/` | One file per proposed write, with its approvals | Pending and approved writes are gone; agents must propose them again |
| `ledger.jsonl` | The hash-chained, signed audit ledger | Your audit history is gone |
| `controls.json` | Paused agents, revoked tokens and rate-limit counters | Paused agents run again and revoked tokens work again until they expire |
| `slack.json`, `cloud.json` | Which Slack messages were posted; the Cloud sync position | Lokra posts or syncs again |

Lokra creates the directory with mode 700 and the secret files with mode 600.

Every Lokra process for one database must share the same state directory: `lokra serve`, `lokra slack`, `lokra cloud`, `lokra dashboard` and the commands you run by hand. That is how an approval in Slack reaches the server, and how a pause takes effect at once. Keep it on a local disk: Lokra uses file locks to keep the ledger and controls consistent.

### Back it up

Back up the whole directory regularly; it is small. Treat the backup as a secret, since it holds the signing key and the agents' database passwords. For a consistent copy of the ledger, take the backup while no requests are being written, or snapshot the volume. Run `lokra verify-ledger` against a restored copy to check it.

Losing `signing.key` is the costly case. Keep a copy somewhere safe, apart from the hosts agents run on. For long-term audit retention beyond backups, see [audit ledger and reports](/docs/audit).

## Change the policy

Policy changes take three steps:

```bash
lokra check --dsn "$LOKRA_ADMIN_DSN"
lokra provision --admin-dsn "$LOKRA_ADMIN_DSN"
sudo systemctl restart lokra lokra-cloud
```

1. `lokra check` validates the new policy against the database. Run it in CI too; see [use Lokra in CI](/docs/ci).
2. `lokra provision` applies grants, row locks and new agents to the database. It is safe to run again: it updates existing roles, keeps their passwords, and keeps paused agents paused.
3. Restart the long-running processes. `lokra serve --http`, `lokra slack` and `lokra cloud` read the policy once at start-up, so they only pick up new agents and changed masking, approval and rate-limit rules after a restart. Stdio servers pick it up the next time the client starts them.

Grants and row locks take effect in the database as soon as `provision` finishes.

## Upgrade

Check what you are running with `lokra --version`, then upgrade the way you installed:

| Installed with | Upgrade with |
|---|---|
| pipx | `pipx upgrade lokra` |
| uv | `uv tool upgrade lokra` |
| A virtual environment | `/opt/lokra/bin/pip install --upgrade lokra` |
| The container image | Change the tag, for example `0.8.2` to the new version, and recreate the container |

After upgrading, run `lokra check`, then `lokra provision`, so the roles match what the new version expects, and restart the long-running processes. The state directory carries over between versions.

## Checklist

- `lokra serve --http` binds to `127.0.0.1`, behind a TLS proxy and a firewall.
- Tokens are minted on the Lokra host with the shortest TTL that works, never more than 8 hours.
- `.lokra` is owned by the service user, on local disk, and backed up.
- The admin connection string is only given to the commands that use it (`check`, `provision`, `pause`, `resume`, `dashboard`, `slack`, `cloud`), never to `serve`.
- `lokra check --strict` runs in CI on every migration; see [use Lokra in CI](/docs/ci).
- You know how to [stop an agent](/docs/stop-agents) before you need to.
