from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path

import yaml

LAB_ADMIN_DSN = "postgresql://postgres:postgres@127.0.0.1:55432/clinic"


class ConfigError(Exception):
    pass


@dataclass
class AgentPolicy:
    name: str
    description: str = ""
    clinic_ids: list[int] = field(default_factory=list)
    read: dict[str, list[str] | str] = field(default_factory=dict)
    write: dict[str, list[str]] = field(default_factory=dict)
    max_rows: int = 200

    @property
    def role(self) -> str:
        return "lokra_" + re.sub(r"[^a-z0-9]+", "_", self.name.lower()).strip("_")


@dataclass
class Config:
    path: Path
    home: Path
    db_host: str
    db_port: int
    db_name: str
    agents: dict[str, AgentPolicy]
    masking_columns: dict[str, str]
    masking_detectors: list[str]
    approval_ttl_seconds: int = 3600

    @property
    def ledger_path(self) -> Path:
        return self.home / "ledger.jsonl"

    @property
    def pending_dir(self) -> Path:
        return self.home / "pending"

    @property
    def secrets_path(self) -> Path:
        return self.home / "secrets.json"

    def ensure_home(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.pending_dir.mkdir(exist_ok=True, mode=0o700)

    def signing_key(self) -> bytes:
        self.ensure_home()
        p = self.home / "signing.key"
        if not p.exists():
            p.write_bytes(secrets.token_bytes(32))
            p.chmod(0o600)
        return p.read_bytes()

    def db_secrets(self) -> dict[str, str]:
        if not self.secrets_path.exists():
            return {}
        return json.loads(self.secrets_path.read_text())

    def save_db_secrets(self, data: dict[str, str]) -> None:
        self.ensure_home()
        self.secrets_path.write_text(json.dumps(data, indent=2))
        self.secrets_path.chmod(0o600)

    def agent(self, name: str) -> AgentPolicy:
        try:
            return self.agents[name]
        except KeyError:
            raise ConfigError(f"unknown agent '{name}'") from None


def load_config(path: str | os.PathLike | None = None, home: str | os.PathLike | None = None) -> Config:
    path = Path(path or os.environ.get("LOKRA_CONFIG", "policies.yaml")).resolve()
    if not path.exists():
        raise ConfigError(f"config not found: {path}")
    raw = yaml.safe_load(path.read_text()) or {}
    db = raw.get("database", {})
    agents = {}
    for name, a in (raw.get("agents") or {}).items():
        a = a or {}
        agents[name] = AgentPolicy(
            name=name,
            description=a.get("description", ""),
            clinic_ids=[int(c) for c in a.get("clinic_ids", [])],
            read=a.get("read", {}) or {},
            write=a.get("write", {}) or {},
            max_rows=int(a.get("max_rows", 200)),
        )
    masking = raw.get("masking", {}) or {}
    home = Path(home or os.environ.get("LOKRA_HOME") or path.parent / ".lokra").resolve()
    return Config(
        path=path,
        home=home,
        db_host=db.get("host", "127.0.0.1"),
        db_port=int(db.get("port", 55432)),
        db_name=db.get("dbname", "clinic"),
        agents=agents,
        masking_columns={k.lower(): v for k, v in (masking.get("columns") or {}).items()},
        masking_detectors=list(masking.get("detectors") or []),
        approval_ttl_seconds=int(raw.get("approval_ttl_seconds", 3600)),
    )
