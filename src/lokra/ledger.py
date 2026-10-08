from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
from datetime import datetime, timezone
from pathlib import Path

GENESIS = "0" * 64


def _canonical(entry: dict) -> bytes:
    return json.dumps(entry, sort_keys=True, separators=(",", ":"), default=str).encode()


class Ledger:
    def __init__(self, path: Path, key: bytes):
        self.path = Path(path)
        self.key = key

    def append(self, event: dict) -> dict:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a+", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0)
                lines = [ln for ln in f.read().splitlines() if ln.strip()]
                prev = json.loads(lines[-1]) if lines else None
                entry = {
                    "seq": (prev["seq"] + 1) if prev else 1,
                    "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                    **event,
                    "prev": prev["hash"] if prev else GENESIS,
                }
                digest = hashlib.sha256(_canonical(entry)).hexdigest()
                entry["hash"] = digest
                entry["sig"] = hmac.new(self.key, digest.encode(), hashlib.sha256).hexdigest()
                f.seek(0, 2)
                f.write(json.dumps(entry, sort_keys=True, default=str) + "\n")
                f.flush()
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        return entry

    def entries(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(ln) for ln in self.path.read_text().splitlines() if ln.strip()]

    def verify(self) -> tuple[bool, int, str]:
        prev_hash, n = GENESIS, 0
        for n, entry in enumerate(self.entries(), start=1):
            body = {k: v for k, v in entry.items() if k not in ("hash", "sig")}
            if body.get("prev") != prev_hash:
                return False, n, f"entry {n}: chain broken (an earlier entry was removed or changed)"
            digest = hashlib.sha256(_canonical(body)).hexdigest()
            if digest != entry.get("hash"):
                return False, n, f"entry {n}: contents were modified"
            expected = hmac.new(self.key, digest.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, entry.get("sig", "")):
                return False, n, f"entry {n}: signature invalid"
            prev_hash = digest
        return True, n, f"ok: {n} entries verified"
