---
title: Install
description: Every way to install Lokra, how to upgrade it, and how to check which version you run.
---
Lokra is one Python package, `lokra`, with one command, also called `lokra`. It needs Python 3.11 or newer. There is also a container image for servers.

## pipx (recommended)

[pipx](https://pipx.pypa.io) installs command-line tools in their own environment, so Lokra's dependencies never clash with your projects:

```bash
pipx install lokra
```

To approve writes in Slack, install the Slack extra instead:

```bash
pipx install 'lokra[slack]'
```

## uv

```bash
uv tool install lokra
```

## pip in a virtual environment

```bash
python3 -m venv .venv
.venv/bin/pip install lokra
.venv/bin/lokra --version
```

## Container image

Each release is published for amd64 and arm64:

```bash
docker pull ghcr.io/lokrahq/lokra:0.8.2
```

Tags: an exact version such as `0.8.2`, the minor line such as `0.8`, and `latest`. The image runs `lokra serve --http` on port 8787 as a non-root user, reads `policies.yaml` from `/work` and keeps its state in `/work/.lokra`. Any other command works by naming it:

```bash
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/work" ghcr.io/lokrahq/lokra:0.8.2 check --dsn "postgresql://..."
```

[Run in production](/docs/production#run-in-a-container) covers volumes, networking and compose files.

## From source

```bash
git clone https://github.com/lokrahq/lokra
cd lokra
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
docker compose up -d
.venv/bin/python -m pytest -q
```

`docker compose up -d` starts the lab Postgres on `127.0.0.1:55432` with fake clinic data, which the test suite uses.

## Check your version

```bash
lokra --version
```

```text
lokra 0.8.2
```

Run `lokra` with no command to see a short welcome screen with the main commands.

## Upgrade

```bash
pipx upgrade lokra
```

Right after a release, pip can briefly keep serving a cached list of versions. If `pipx upgrade` says you already have the latest version when you know you don't, skip the cache:

```bash
pipx upgrade lokra --pip-args="--no-cache-dir"
```

For the image, change the tag and restart the container. Upgrades keep your policy and the `.lokra` state folder as they are. Before upgrading a production gate, read what changed: each [release tag on GitHub](https://github.com/lokrahq/lokra/tags) points at a commit whose message describes the release.

## Uninstall

```bash
pipx uninstall lokra
```

This removes the command. It does not touch your database: the agent roles and row-level security policies Lokra provisioned stay until you drop them. Your `policies.yaml` and `.lokra` folder also stay where they are.
