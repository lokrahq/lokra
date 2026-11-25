"""Connect this Lokra to Lokra Cloud.

`lokra cloud` sends the agents, pending and recently decided writes and new ledger entries to
Lokra Cloud, and applies the approvals and pauses people make there. The SQL in writes and ledger
entries is already masked; query results never leave this machine.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request

from . import __version__, controls
from .config import Config, ConfigError
from .dashboard import write_view
from .gateway import GatewayError, decide, list_pending
from .ledger import Ledger
from .masking import Masker

MAX_EVENTS = 500
MAX_WRITES = 200
RECENT_SECONDS = 86400


class CloudError(Exception):
    pass


def http_post(url: str, token: str, payload: dict, timeout: float = 15) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload, default=str).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}",
                                          "User-Agent": f"lokra/{__version__}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read())
    except urllib.error.HTTPError as e:
        try:
            message = json.loads(e.read()).get("error") or e.reason
        except ValueError:
            message = e.reason
        raise CloudError(f"Lokra Cloud refused the sync ({e.code}): {message}") from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise CloudError(f"could not reach Lokra Cloud: {getattr(e, 'reason', e)}") from None


class CloudSync:
    def __init__(self, cfg: Config, url: str, token: str, admin_dsn: str | None = None, post=http_post):
        if not token.startswith("lkg_"):
            raise ConfigError("LOKRA_CLOUD_TOKEN should start with lkg_; create one under Gates in Lokra Cloud")
        self.cfg = cfg
        self.url = url.rstrip("/") + "/v1/sync"
        self.token = token
        self.admin_dsn = admin_dsn
        self.post = post
        self.masker = Masker(cfg.masking_columns, cfg.masking_detectors)
        self.path = cfg.home / "cloud.json"
        gate = hashlib.sha256(f"{self.url}|{token}".encode()).hexdigest()[:16]
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {}
        if self.state.get("gate") != gate:
            self.state = {"gate": gate, "cursor": 0, "results": []}

    def _save(self) -> None:
        self.cfg.ensure_home()
        self.path.write_text(json.dumps(self.state, indent=2))
        self.path.chmod(0o600)

    def payload(self) -> dict:
        paused = controls.snapshot(self.cfg)["paused"]
        agents = [{"name": a.name, "description": a.description, "rate_limit": a.rate_limit,
                   "paused": paused.get(a.name)} for a in self.cfg.agents.values()]
        cutoff = time.time() - RECENT_SECONDS
        records = [r for r in list_pending(self.cfg) if r["status"] == "pending" or max(
            r.get(k) or 0 for k in ("created_at", "approved_at", "denied_at", "executed_at")) > cutoff]
        writes = [write_view(r, self.masker, self.cfg.approval_ttl_seconds) for r in records[-MAX_WRITES:]]
        entries = Ledger(self.cfg.ledger_path, self.cfg.signing_key()).entries()
        cursor = min(self.state["cursor"], entries[-1]["seq"] if entries else 0)
        events = [{k: v for k, v in e.items() if k != "sig"} for e in entries if e["seq"] > cursor][:MAX_EVENTS]
        return {"version": __version__, "agents": agents, "writes": writes, "events": events,
                "results": self.state["results"]}

    def _apply(self, cmd: dict) -> tuple[bool, str]:
        kind, target, by = cmd.get("kind"), str(cmd.get("target", "")), str(cmd.get("by") or "lokra cloud")
        try:
            if kind in ("approve", "deny"):
                record = decide(self.cfg, target, kind == "approve", by)
                return True, f"{target} {record['status']}"
            if kind == "pause":
                return True, controls.pause_agent(self.cfg, self.cfg.agent(target), by, cmd.get("reason") or "",
                                                  self.admin_dsn)
            if kind == "resume":
                return True, controls.resume_agent(self.cfg, self.cfg.agent(target), by, self.admin_dsn)[1]
        except (GatewayError, ConfigError) as e:
            return False, str(e)
        return False, f"unknown command {kind!r}"

    def sync_once(self) -> list[str]:
        response = self.post(self.url, self.token, self.payload())
        self.state["results"] = []
        self.state["cursor"] = int(response.get("cursor") or 0)
        log = []
        for cmd in response.get("commands") or []:
            ok, detail = self._apply(cmd)
            self.state["results"].append({"id": cmd.get("id"), "ok": ok, "detail": detail})
            log.append(f"{cmd.get('kind')} {cmd.get('target')} from {cmd.get('by')}: {detail}")
        self._save()
        return log


def run(cfg: Config, url: str, admin_dsn: str | None, once: bool = False, interval: float = 3) -> None:
    token = os.environ.get("LOKRA_CLOUD_TOKEN", "")
    if not token:
        raise ConfigError("set LOKRA_CLOUD_TOKEN to a gate token from Lokra Cloud (Gates → Create gate token)")
    sync = CloudSync(cfg, url, token, admin_dsn)
    print(f"lokra: connected to {url} (Ctrl+C to stop)" if not once else f"lokra: syncing with {url}")
    delay = interval
    while True:
        try:
            applied = sync.sync_once()
            for line in applied:
                print(f"lokra: {line}")
            if applied:
                sync.sync_once()
            delay = interval
        except CloudError as e:
            if once:
                raise ConfigError(str(e)) from None
            print(f"lokra: {e}; retrying in {delay:.0f}s")
            delay = min(delay * 2, 60)
        if once:
            return
        try:
            time.sleep(delay)
        except KeyboardInterrupt:
            return
