import time

import pytest

from lokra.config import ConfigError, load_config
from lokra.gateway import _save_pending, decide
from lokra.slack import APPROVE, DENY, SlackApprovals

POLICY = """
agents:
  ops-bot:
    read: {orders: "*"}
    write: {orders: [update]}
masking: {columns: {}, detectors: [email]}
slack: {channel: C0LOKRA, approvers: [U0BOSS]}
"""


class FakeSlack:
    def __init__(self):
        self.posted, self.updated, self.ephemeral = [], [], []

    def chat_postMessage(self, channel, text, blocks):
        self.posted.append({"channel": channel, "text": text, "blocks": blocks})
        return {"channel": channel, "ts": f"17000.{len(self.posted)}"}

    def chat_update(self, channel, ts, text, blocks):
        self.updated.append({"ts": ts, "blocks": blocks})

    def chat_postEphemeral(self, channel, user, text):
        self.ephemeral.append({"user": user, "text": text})


def _cfg(tmp_path, policy=POLICY):
    (tmp_path / "policies.yaml").write_text(policy)
    return load_config(tmp_path / "policies.yaml")


def _pending(cfg, wid, sql="UPDATE orders SET status = 'shipped' WHERE email = 'ann@example.com'"):
    _save_pending(cfg, {"id": wid, "status": "pending", "agent": "ops-bot", "on_behalf_of": "sam",
                        "sql": sql, "reason": "courier picked it up", "statement": "update",
                        "tables": ["orders"], "would_affect_rows": 1, "warnings": [],
                        "created_at": time.time(), "token_id": "t"})


def _click(wid, action, user="U0BOSS"):
    return {"type": "block_actions", "user": {"id": user, "username": "boss"}, "channel": {"id": "C0LOKRA"},
            "actions": [{"action_id": action, "value": wid}]}


def _text(blocks):
    return " ".join(b.get("text", {}).get("text", "") for b in blocks if "text" in b)


def test_posts_each_pending_write_once_with_masked_sql(tmp_path):
    cfg, slack = _cfg(tmp_path), FakeSlack()
    _pending(cfg, "w_aaaa0001")
    bot = SlackApprovals(cfg, slack)
    bot.sync()
    bot.sync()
    assert len(slack.posted) == 1
    blocks = slack.posted[0]["blocks"]
    assert "ann@example.com" not in _text(blocks) and "[EMAIL]" in _text(blocks)
    buttons = blocks[-1]["elements"]
    assert [b["action_id"] for b in buttons] == [APPROVE, DENY] and buttons[0]["value"] == "w_aaaa0001"
    assert SlackApprovals(cfg, FakeSlack()).posted["w_aaaa0001"]["open"]


def test_only_listed_approvers_can_decide(tmp_path):
    cfg, slack = _cfg(tmp_path), FakeSlack()
    _pending(cfg, "w_aaaa0002")
    bot = SlackApprovals(cfg, slack)
    bot.sync()
    bot.handle_action(_click("w_aaaa0002", APPROVE, user="U0INTERN"))
    assert slack.ephemeral and "approver list" in slack.ephemeral[0]["text"]
    assert not slack.updated

    bot.handle_action(_click("w_aaaa0002", APPROVE))
    record = __import__("json").loads((cfg.pending_dir / "w_aaaa0002.json").read_text())
    assert record["status"] == "approved" and record["approved_by"] == "boss (slack)"
    assert "Approved by boss (slack)" in str(slack.updated[-1]["blocks"][-1])
    assert all(b["type"] != "actions" for b in slack.updated[-1]["blocks"])

    bot.handle_action(_click("w_aaaa0002", DENY))
    assert "already 'approved'" in slack.ephemeral[-1]["text"]


def test_writes_decided_elsewhere_close_their_message(tmp_path):
    cfg, slack = _cfg(tmp_path), FakeSlack()
    _pending(cfg, "w_aaaa0003")
    bot = SlackApprovals(cfg, slack)
    bot.sync()
    decide(cfg, "w_aaaa0003", False, "dana (dashboard)")
    bot.sync()
    bot.sync()
    assert len(slack.updated) == 1 and "Denied by dana (dashboard)" in str(slack.updated[0]["blocks"])


def test_slack_needs_a_channel_and_approvers(tmp_path):
    with pytest.raises(ConfigError, match="channel"):
        SlackApprovals(_cfg(tmp_path, POLICY.replace("channel: C0LOKRA, ", "")), FakeSlack())
    with pytest.raises(ConfigError, match="approve"):
        SlackApprovals(_cfg(tmp_path, POLICY.replace(", approvers: [U0BOSS]", "")), FakeSlack())


def test_dashboard_save_keeps_the_slack_section(tmp_path):
    from lokra.dashboard import _build_doc
    doc = _build_doc(_cfg(tmp_path), {"agents": [{"name": "ops-bot", "read": [{"table": "orders", "columns": "*"}]}]})
    assert doc["slack"] == {"channel": "C0LOKRA", "approvers": ["U0BOSS"]}


def test_run_explains_missing_tokens(tmp_path, monkeypatch):
    from lokra.slack import run
    monkeypatch.delenv("LOKRA_SLACK_BOT_TOKEN", raising=False)
    with pytest.raises(ConfigError, match="LOKRA_SLACK_BOT_TOKEN"):
        run(_cfg(tmp_path))
