---
title: Connect a gate
description: Create a gate token in Lokra Cloud, run lokra cloud next to your policy, and keep it running.
---

A gate is one Lokra install guarding one database. To bring it into [Lokra Cloud](/docs/cloud), you create a gate token in Cloud and run `lokra cloud` next to the gate's `policies.yaml`. The connection goes out from the gate to Cloud over HTTPS, so the gate needs no public address.

## Before you start

- A working gate: a `policies.yaml` and Lokra serving agents from it. If you don't have one yet, start with the [Quickstart](/docs/quickstart).
- Lokra 0.6.0 or later. The startup lines on this page are from 0.8.2. Check with `lokra --version`.
- The owner role in your Cloud organisation. Only owners can create gate tokens.

## Create a gate token

1. Sign in at [cloud.lokra.dev](https://cloud.lokra.dev) and open **Gates**.
2. Under **Connect a database**, type a name for the gate. Name it after the database it guards, such as `production-db`. Names can use letters, numbers, spaces, `.`, `_` and `-`, up to 60 characters.
3. Choose **Create token**.

Cloud shows the command to run, with the token in it:

```bash
LOKRA_CLOUD_TOKEN=lkg_… lokra cloud --url https://cloud.lokra.dev
```

Choose **Copy** to copy the whole command, or click the token to copy only the token. `--url https://cloud.lokra.dev` is the default, so you can leave it out.

> **Warning:** The token is shown once. Cloud stores only a hash of it, and the command disappears when the gate first connects or when you reload the page. If you lose the token before the gate connects, remove the gate and create a new one.

## Run lokra cloud

Run it from the folder that holds the gate's `policies.yaml`, as the same user that runs the gate:

```bash
cd /srv/lokra
LOKRA_CLOUD_TOKEN=lkg_… lokra cloud --admin-dsn "$LOKRA_ADMIN_DSN"
```

`lokra cloud` has to share the gate's state: the pending writes, pauses and ledger in the `.lokra` folder next to `policies.yaml`. That is how a decision made in Cloud reaches the agents. If the policy lives somewhere else, pass `--config` (or set `LOKRA_CONFIG`) before the command name:

```bash
LOKRA_CLOUD_TOKEN=lkg_… lokra --config /etc/lokra/policies.yaml cloud
```

`lokra cloud` reads `policies.yaml` when it starts. Restart it after you change the policy, so Cloud sees agents you added or removed.

### Options

| Option | Default | What it does |
| --- | --- | --- |
| `--url URL` | `$LOKRA_CLOUD_URL`, then `https://cloud.lokra.dev` | The Lokra Cloud address. |
| `--admin-dsn ADMIN_DSN` | `$LOKRA_ADMIN_DSN` | An admin connection string, so a pause from Cloud also blocks the agent's database login. |
| `--once` | off | Sync once and exit. |

The token always comes from the `LOKRA_CLOUD_TOKEN` environment variable, never from a flag, so it stays out of your process list. See the [CLI reference](/docs/cli) and [Environment variables](/docs/environment).

### What the startup lines mean

```text
lokra: connecting to https://cloud.lokra.dev
lokra: serving support-bot, billing-bot from /srv/lokra/policies.yaml
lokra: a pause from Cloud also blocks the agent's database login
lokra: connected as gate "production-db" (Ctrl+C to stop)
```

- **connecting to** is the Cloud address in use.
- **serving … from** lists the agents in the policy and the policy file. These are the agents Cloud will show. If an agent is missing, check that this is the right `policies.yaml`.
- The third line says what a pause from Cloud will do. Without `--admin-dsn` it reads: `lokra: no --admin-dsn, so a pause from Cloud refuses the agent's requests but leaves its database login on`.
- **connected as gate** confirms that Cloud accepted the token, and gives the gate's name in Cloud. If the name is not the one you expect, you used another gate's token.

When someone decides in Cloud, `lokra cloud` prints what it applied:

```text
lokra: approve w_3f9a1c2e from dana (cloud): w_3f9a1c2e approved
lokra: pause support-bot from dana (cloud): database login disabled, 2 open session(s) ended
```

### Why --admin-dsn matters for pauses

Without `--admin-dsn`, a pause from Cloud makes Lokra refuse every request from that agent. But the agent's own database role can still log in, so anything holding its database password could connect around Lokra.

With `--admin-dsn`, a pause also turns off the role's login and ends its open sessions, and a resume turns the login back on. The admin connection string stays on the gate; Cloud never receives it. See [Stop an agent](/docs/stop-agents).

### Check the connection once

`--once` syncs a single time, applies any decisions waiting in Cloud, and exits. Any error stops it with a non-zero exit code, which makes it a quick test before you set up a service:

```bash
LOKRA_CLOUD_TOKEN=lkg_… lokra cloud --once
```

```text
lokra: connecting to https://cloud.lokra.dev
lokra: serving support-bot from /srv/lokra/policies.yaml
lokra: no --admin-dsn, so a pause from Cloud refuses the agent's requests but leaves its database login on
lokra: connected as gate "production-db"
```

## If the token is refused

If Cloud does not know the token, `lokra cloud` stops at once with exit code 1 instead of retrying:

```text
error: Lokra Cloud does not recognise this gate token. It may be mistyped or only partly copied, or its gate was removed. Create a new token under Gates in Lokra Cloud and run lokra cloud again.
```

Two other mistakes stop it before it connects:

- `LOKRA_CLOUD_TOKEN` is not set: `error: set LOKRA_CLOUD_TOKEN to a gate token from Lokra Cloud …`
- The value does not start with `lkg_`: `error: LOKRA_CLOUD_TOKEN should start with lkg_; create one under Gates in Lokra Cloud`

Network problems do not stop it. It prints the error and retries, waiting 3 seconds at first and doubling each time up to 60 seconds:

```text
lokra: could not reach Lokra Cloud: timed out; retrying in 3s
```

## Keep it running

Run `lokra cloud` as a long-running service on the same machine, as the same user and with the same `policies.yaml` as the gate. Lokra keeps its state folder private to that user, so a different user cannot read it.

### systemd

Put the token and admin connection string in a file only root can read, such as `/etc/lokra/cloud.env`:

```text
LOKRA_CLOUD_TOKEN=lkg_…
LOKRA_ADMIN_DSN=postgresql://lokra_admin:…@db.internal:5432/app
```

```bash
sudo chmod 600 /etc/lokra/cloud.env
```

Then create `/etc/systemd/system/lokra-cloud.service`. Use the path `which lokra` prints, and the user and folder your gate runs with:

```ini
[Unit]
Description=Lokra Cloud sync
After=network-online.target
Wants=network-online.target

[Service]
User=lokra
WorkingDirectory=/srv/lokra
EnvironmentFile=/etc/lokra/cloud.env
ExecStart=/usr/local/bin/lokra cloud
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now lokra-cloud
journalctl -u lokra-cloud -f
```

`lokra cloud` reads `LOKRA_ADMIN_DSN` from the environment, so the unit does not need `--admin-dsn`.

### Docker

The Lokra image runs any `lokra` command. Run `cloud` with the same folder mounted at `/work` and the same user as the container that serves your agents, so both share `/work/.lokra`:

```bash
docker run -d --name lokra-cloud --restart unless-stopped \
  --user "$(id -u):$(id -g)" -v "$PWD:/work" \
  --env-file /etc/lokra/cloud.env \
  ghcr.io/lokrahq/lokra:0.8.2 cloud
```

The database host in `LOKRA_ADMIN_DSN` has to be reachable from inside the container, so not `127.0.0.1`. Follow the output with `docker logs -f lokra-cloud`.

## Several gates

Give each gate its own token and its own `lokra cloud`. The Inbox, Agents and Activity pages show every gate together, and each request and entry names its gate. Agents with the same name on different gates are separate agents in Cloud.

> **Warning:** Never use one token on two gates. Cloud treats everything sent with a token as one gate, so two gates would overwrite each other's agents and ledger entries.

## Remove a gate

Owners can remove a gate from its card on the **Gates** page with **Remove gate**. Cloud asks first:

> Its token stops working and its history in Lokra Cloud is deleted. The gate's own ledger on your server is not touched.

Its agents, writes, queued decisions and activity are deleted from Cloud. The next time that gate's `lokra cloud` syncs, it stops with the "does not recognise this gate token" error. Stop its service too, for example with `sudo systemctl disable --now lokra-cloud` or `docker rm -f lokra-cloud`, or it will keep restarting and failing.

## What "live" means

A gate is **live** when it has synced in the last 60 seconds. A running `lokra cloud` syncs about every 3 seconds, so a gate that is not live has stopped, lost its network or been refused.

- The **Gates** page shows each gate as `live` or `last seen` with how long ago, and a count such as `2 of 3 live` at the top.
- The **Gates** box in the left rail shows the first four gates as `live`, `seen 5m` or `not connected` (never synced). On a phone, the top bar shows a count such as `2/3 live`.

Decisions made while a gate is not live wait in Cloud, and the gate applies them when it reconnects.
