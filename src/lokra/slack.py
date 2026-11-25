"""Approve or deny proposed writes from Slack.

`lokra slack` posts each pending write to a channel with Approve and Deny buttons and records
the decision when an allowed approver clicks one. It talks to Slack over Socket Mode, an
outbound connection, so Lokra needs no public URL.
"""
from __future__ import annotations

import json
import os
import threading
import time

from .config import Config, ConfigError
from .dashboard import write_view
from .gateway import GatewayError, decide, list_pending
from .masking import Masker

APPROVE, DENY = "lokra_approve", "lokra_deny"
MAX_SQL = 2800


def _summary(w: dict) -> str:
    return f"*{w['agent']}* wants to {(w['statement'] or 'write').upper()} `{', '.join(w['tables'])}`"


def _detail_blocks(w: dict) -> list[dict]:
    context = [f"`{w['id']}`"]
    if w.get("on_behalf_of"):
        context.append(f"on behalf of {w['on_behalf_of']}")
    sql = w["sql"] if len(w["sql"]) <= MAX_SQL else w["sql"][:MAX_SQL] + " …"
    rows = w.get("would_affect_rows")
    lines = [f">{w['reason']}" if w.get("reason") else "_No reason given._",
             f"```{sql}```",
             f"Dry run: *{rows if rows is not None else '?'} row{'' if rows == 1 else 's'}* would change"]
    lines += [f":warning: {warning}" for warning in w.get("warnings") or []]
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": _summary(w)}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": " · ".join(context)}]},
        {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}},
    ]


def pending_blocks(w: dict) -> list[dict]:
    return _detail_blocks(w) + [{"type": "actions", "elements": [
        {"type": "button", "action_id": APPROVE, "value": w["id"], "style": "primary",
         "text": {"type": "plain_text", "text": "Approve"}},
        {"type": "button", "action_id": DENY, "value": w["id"], "style": "danger",
         "text": {"type": "plain_text", "text": "Deny"}},
    ]}]


def closed_blocks(w: dict, outcome: str) -> list[dict]:
    return _detail_blocks(w) + [{"type": "context", "elements": [{"type": "mrkdwn", "text": outcome}]}]


def _outcome(w: dict) -> str:
    who = f" by {w['decided_by']}" if w.get("decided_by") else ""
    icon = ":white_check_mark:" if w["status"] in ("approved", "executed") else ":no_entry:"
    return f"{icon} {w['status'].capitalize()}{who}"


class SlackApprovals:
    def __init__(self, cfg: Config, client):
        if not cfg.slack_channel:
            raise ConfigError("add the channel to policies.yaml: slack: {channel: C0123ABCD, approvers: [U0123ABCD]}")
        if not cfg.slack_approvers:
            raise ConfigError("list who may approve: slack: {approvers: [U0123ABCD]} (Slack member IDs)")
        self.cfg = cfg
        self.client = client
        self.masker = Masker(cfg.masking_columns, cfg.masking_detectors)
        self.path = cfg.home / "slack.json"
        self.lock = threading.Lock()
        self.posted: dict[str, dict] = json.loads(self.path.read_text()) if self.path.exists() else {}

    def _save(self) -> None:
        self.cfg.ensure_home()
        self.path.write_text(json.dumps(self.posted, indent=2))
        self.path.chmod(0o600)

    def _views(self) -> dict[str, dict]:
        return {r["id"]: write_view(r, self.masker, self.cfg.approval_ttl_seconds) for r in list_pending(self.cfg)}

    def sync(self) -> None:
        """Post new pending writes, and close messages for writes decided anywhere else."""
        views = self._views()
        with self.lock:
            changed = False
            for wid, w in views.items():
                msg = self.posted.get(wid)
                if w["status"] == "pending" and msg is None:
                    res = self.client.chat_postMessage(channel=self.cfg.slack_channel, text=_summary(w),
                                                       blocks=pending_blocks(w))
                    self.posted[wid] = {"channel": res["channel"], "ts": res["ts"], "open": True}
                    changed = True
                elif w["status"] != "pending" and msg and msg["open"]:
                    self.client.chat_update(channel=msg["channel"], ts=msg["ts"], text=_summary(w),
                                            blocks=closed_blocks(w, _outcome(w)))
                    msg["open"] = False
                    changed = True
            if changed:
                self._save()

    def handle_action(self, payload: dict) -> None:
        action = next((a for a in payload.get("actions") or [] if a.get("action_id") in (APPROVE, DENY)), None)
        if action is None:
            return
        user = payload.get("user") or {}
        channel = (payload.get("channel") or {}).get("id") or self.cfg.slack_channel
        if user.get("id") not in self.cfg.slack_approvers:
            self.client.chat_postEphemeral(channel=channel, user=user.get("id"),
                                           text="You're not on Lokra's approver list for this database.")
            return
        name = user.get("username") or user.get("name") or user.get("id")
        try:
            decide(self.cfg, action["value"], action["action_id"] == APPROVE, f"{name} (slack)")
        except GatewayError as e:
            self.client.chat_postEphemeral(channel=channel, user=user["id"], text=str(e))
        self.sync()


def run(cfg: Config) -> None:
    try:
        from slack_sdk import WebClient
        from slack_sdk.socket_mode import SocketModeClient
        from slack_sdk.socket_mode.response import SocketModeResponse
    except ModuleNotFoundError:
        raise ConfigError("Slack support needs an extra package: pipx install 'lokra[slack]'") from None
    bot, app = os.environ.get("LOKRA_SLACK_BOT_TOKEN"), os.environ.get("LOKRA_SLACK_APP_TOKEN")
    if not bot or not app:
        raise ConfigError("set LOKRA_SLACK_BOT_TOKEN (xoxb-…) and LOKRA_SLACK_APP_TOKEN (xapp-…)")
    web = WebClient(token=bot)
    approvals = SlackApprovals(cfg, web)

    def on_request(client, req):
        if req.type != "interactive":
            return
        client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
        approvals.handle_action(req.payload)

    socket = SocketModeClient(app_token=app, web_client=web)
    socket.socket_mode_request_listeners.append(on_request)
    socket.connect()
    print(f"lokra: posting write approvals to Slack channel {cfg.slack_channel} (Ctrl+C to stop)")
    try:
        while True:
            approvals.sync()
            time.sleep(2)
    except KeyboardInterrupt:
        socket.close()
