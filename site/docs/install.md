---
title: Install
description: Every way to install Lokra on macOS, Linux and Windows, how to upgrade it, and how to check which version you run.
---
Lokra is one command, `lokra`. Homebrew and the install script give you a standalone build that needs no Python. The Python package needs Python 3.11 or newer, and there is a container image for servers. Every route installs the same Lokra, and the standalone build includes Slack support.

| You have | Install with |
|---|---|
| macOS or Linux with Homebrew | `brew install lokrahq/tap/lokra` |
| macOS or Linux, no Homebrew | The [install script](#install-script) |
| Python | `pipx install lokra` or `uv tool install lokra` |
| Windows | `pipx install lokra`, or the [Windows download](#download-a-build) |
| A server or Kubernetes | The [container image](#container-image) |

## Homebrew

```bash
brew install lokrahq/tap/lokra
```

Installing by its full name trusts only this formula from the `lokrahq/tap` tap, as Homebrew asks for any tap outside its own. If Homebrew stops with "Your Command Line Tools are too outdated", update them from Software Update in System Settings and run the command again.

## Install script

```bash
curl -fsSL https://lokra.dev/install.sh | sh
```

The script picks the build for your system from the latest [GitHub release](https://github.com/lokrahq/lokra/releases), checks it against the release's `SHA256SUMS` and installs it in `~/.local/share/lokra`, with the `lokra` command in `~/.local/bin`. It stops without changing anything if the checksum does not match, or if `~/.local/bin/lokra` already exists from pipx or uv. [Read the script](https://lokra.dev/install.sh) before you run it if you prefer.

To install a particular release, or put the command somewhere else:

```bash
curl -fsSL https://lokra.dev/install.sh | LOKRA_VERSION=0.8.3 LOKRA_BIN_DIR="$HOME/bin" sh
```

## pipx

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

`uvx` runs Lokra without installing it, which suits trying the demo:

```bash
uvx lokra demo
```

## pip in a virtual environment

```bash
python3 -m venv .venv
.venv/bin/pip install lokra
.venv/bin/lokra --version
```

## Download a build

Each [GitHub release](https://github.com/lokrahq/lokra/releases) has a standalone build for every system, and a `SHA256SUMS` file to check them against:

| System | File |
|---|---|
| macOS, Apple silicon | `lokra-<version>-macos-arm64.tar.gz` |
| macOS, Intel | `lokra-<version>-macos-x86_64.tar.gz` |
| Linux, x86-64 | `lokra-<version>-linux-x86_64.tar.gz` |
| Linux, ARM64 | `lokra-<version>-linux-arm64.tar.gz` |
| Windows, x86-64 | `lokra-<version>-windows-x86_64.zip` |

Each holds a `lokra` folder. Keep the folder together and run `lokra` (or `lokra.exe`) from inside it, or put a link to it on your PATH. The Linux builds need glibc 2.35 or newer, as on Ubuntu 22.04, Debian 12 and Fedora 36; on an older system use pipx or the container image.

## Windows

Lokra runs on Windows 10 and 11. Install it with `pipx install lokra` or `uv tool install lokra`, or unzip the Windows build and add its `lokra` folder to your PATH. `lokra connect` writes Claude Desktop's config to `%APPDATA%\Claude`. `lokra demo` needs Docker Desktop, or pass `--dsn` to use a Postgres you already have. The command examples in these docs use a Unix shell; in PowerShell, write `$env:LOKRA_TOKEN = "…"` instead of `export LOKRA_TOKEN=…`.

## Container image

Each release is published for amd64 and arm64:

```bash
docker pull ghcr.io/lokrahq/lokra:0.8.3
```

Tags: an exact version such as `0.8.3`, the minor line such as `0.8`, and `latest`. The image runs `lokra serve --http` on port 8787 as a non-root user, reads `policies.yaml` from `/work` and keeps its state in `/work/.lokra`. Any other command works by naming it:

```bash
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/work" ghcr.io/lokrahq/lokra:0.8.3 check --dsn "postgresql://..."
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
lokra 0.8.3
```

Run `lokra` with no command to see a short welcome screen with the main commands.

## Upgrade

| Installed with | Upgrade with |
|---|---|
| Homebrew | `brew upgrade lokra` |
| Install script | Run the script again |
| pipx | `pipx upgrade lokra` |
| uv | `uv tool upgrade lokra` |
| Container image | Change the tag and recreate the container |

Right after a release, pip can briefly keep serving a cached list of versions. If `pipx upgrade` says you already have the latest version when you know you don't, skip the cache:

```bash
pipx upgrade lokra --pip-args="--no-cache-dir"
```

Upgrades keep your policy and the `.lokra` state folder as they are. Before upgrading a production gate, read what changed in the [release notes on GitHub](https://github.com/lokrahq/lokra/releases).

## Uninstall

| Installed with | Uninstall with |
|---|---|
| Homebrew | `brew uninstall lokra` |
| Install script | `rm -r ~/.local/share/lokra ~/.local/bin/lokra` |
| pipx | `pipx uninstall lokra` |
| uv | `uv tool uninstall lokra` |

This removes the command. It does not touch your database: the agent roles and row-level security policies Lokra provisioned stay until you drop them. Your `policies.yaml` and `.lokra` folder also stay where they are.
