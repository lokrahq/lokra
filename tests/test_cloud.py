import json
import time

import pytest

from lokra import controls
from lokra.cloud import CloudSync
from lokra.config import ConfigError, load_config
from lokra.gateway import _save_pending
from lokra.ledger import Ledger

POLICY = """
agents:
  ops-bot:
    description: fixes orders
    read: {orders: "*"}
    write: {orders: [update]}
    rate_limit: 5
masking: {columns: {}, detectors: [email]}
"""


class FakeCloud:
    def __init__(self):
        self.sent, self.replies = [], []

    def __call__(self, url, token, payload):
        self.sent.append(json.loads(json.dumps(payload, default=str)))
        return self.replies.pop(0) if self.replies else {"cursor": payload["events"][-1]["seq"] if payload["events"] else 0,
                                                          "commands": []}


@pytest.fixture
def cfg(tmp_path):
    (tmp_path / "policies.yaml").write_text(POLICY)
    c = load_config(tmp_path / "policies.yaml")
    _save_pending(c, {"id": "w_cafe0001", "status": "pending", "agent": "ops-bot", "on_behalf_of": "sam",
                      "sql": "UPDATE orders SET status = 'x' WHERE email = 'ann@example.com'", "reason": "late",
                      "statement": "update", "tables": ["orders"], "would_affect_rows": 2, "warnings": [],
                      "created_at": time.time(), "token_id": "t"})
    led = Ledger(c.ledger_path, c.signing_key())
    led.append({"event": "write_proposed", "agent": "ops-bot", "write_id": "w_cafe0001"})
    led.append({"event": "read", "agent": "ops-bot", "rows": 3})
    return c


def test_payload_sends_masked_writes_agents_and_new_events(cfg):
    cloud = FakeCloud()
    sync = CloudSync(cfg, "http://cloud.test/", "lkg_x", post=cloud)
    sync.sync_once()
    sent = cloud.sent[0]
    assert sent["agents"] == [{"name": "ops-bot", "description": "fixes orders", "rate_limit": 5, "paused": None}]
    assert sent["writes"][0]["id"] == "w_cafe0001" and "ann@example.com" not in sent["writes"][0]["sql"]
    assert [e["seq"] for e in sent["events"]] == [1, 2] and all("sig" not in e and "hash" in e for e in sent["events"])
    Ledger(cfg.ledger_path, cfg.signing_key()).append({"event": "read", "agent": "ops-bot", "rows": 1})
    sync.sync_once()
    assert [e["seq"] for e in cloud.sent[1]["events"]] == [3]


def test_commands_from_the_cloud_are_applied_and_reported(cfg):
    cloud = FakeCloud()
    cloud.replies = [{"cursor": 2, "commands": [
        {"id": "c_1", "kind": "approve", "target": "w_cafe0001", "by": "alice (cloud)"},
        {"id": "c_2", "kind": "pause", "target": "ops-bot", "reason": "runaway", "by": "alice (cloud)"},
        {"id": "c_3", "kind": "deny", "target": "w_missing", "by": "alice (cloud)"},
    ]}]
    sync = CloudSync(cfg, "http://cloud.test", "lkg_x", post=cloud)
    log = sync.sync_once()
    assert len(log) == 3
    record = json.loads((cfg.pending_dir / "w_cafe0001.json").read_text())
    assert record["status"] == "approved" and record["approved_by"] == "alice (cloud)"
    assert controls.snapshot(cfg)["paused"]["ops-bot"]["reason"] == "runaway"
    sync.sync_once()
    results = {r["id"]: r for r in cloud.sent[1]["results"]}
    assert results["c_1"]["ok"] and results["c_2"]["ok"] and not results["c_3"]["ok"]
    assert cloud.sent[1]["writes"][0]["status"] == "approved"
    assert cloud.sent[1]["agents"][0]["paused"]["by"] == "alice (cloud)"
    sync.sync_once()
    assert cloud.sent[2]["results"] == []


def test_gate_token_must_look_right(cfg):
    with pytest.raises(ConfigError, match="lkg_"):
        CloudSync(cfg, "http://cloud.test", "sk-live-abc")
