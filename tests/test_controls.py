import pytest

from lokra import controls, tokens
from lokra.config import load_config
from lokra.gateway import Gateway, GatewayError
from lokra.ledger import Ledger
from lokra.report import build_report

POLICY = """
agents:
  ops-bot:
    read: {orders: "*"}
  loop-bot:
    read: {orders: "*"}
    rate_limit: 3
"""


@pytest.fixture
def cfg(tmp_path):
    (tmp_path / "policies.yaml").write_text(POLICY)
    return load_config(tmp_path / "policies.yaml")


def _token(cfg, agent):
    return tokens.mint(cfg.signing_key(), agent, "sam", 300)


def _events(cfg):
    return [e for e in Ledger(cfg.ledger_path, cfg.signing_key()).entries() if e["event"] == "stopped"]


def test_pause_stops_every_token_until_resumed(cfg):
    a, b = _token(cfg, "ops-bot"), _token(cfg, "ops-bot")
    controls.pause(cfg, "ops-bot", "dana", "deleting too much")
    for t in (a, b):
        with pytest.raises(GatewayError, match="paused by dana: deleting too much"):
            Gateway(cfg, t).whoami()
    assert _events(cfg)[-1]["control"] == "paused"
    assert Gateway(cfg, _token(cfg, "loop-bot")).whoami()["agent"] == "loop-bot"
    assert controls.resume(cfg, "ops-bot") and not controls.resume(cfg, "ops-bot")
    assert Gateway(cfg, a).whoami()["agent"] == "ops-bot"


def test_revoking_one_token_leaves_the_others(cfg):
    leaked, fresh = _token(cfg, "ops-bot"), _token(cfg, "ops-bot")
    controls.revoke(cfg, tokens.verify(cfg.signing_key(), leaked)["jti"], "dana")
    with pytest.raises(GatewayError, match="revoked"):
        Gateway(cfg, leaked).whoami()
    assert Gateway(cfg, fresh).whoami()["agent"] == "ops-bot"


def test_rate_limit_stops_a_loop_and_flags_it(cfg):
    gw = Gateway(cfg, _token(cfg, "loop-bot"))
    for _ in range(3):
        gw.whoami()
    for _ in range(3):
        with pytest.raises(GatewayError, match="rate limit of 3 requests a minute"):
            gw.whoami()
    report = build_report(Ledger(cfg.ledger_path, cfg.signing_key()).entries())
    loop = report["agents"]["loop-bot"]
    assert loop.rate_limited == 3 and ("looping" in {k for k, _ in loop.flags})
    assert report["identity_denials"] == 0
    for _ in range(5):
        Gateway(cfg, _token(cfg, "ops-bot")).whoami()


def test_cli_pause_resume_and_revoke(cfg, capsys):
    from lokra.cli import main
    conf = str(cfg.path)
    t = _token(cfg, "ops-bot")
    assert main(["--config", conf, "pause", "ops-bot", "--reason", "incident", "--admin-dsn",
                 "postgresql://nobody@127.0.0.1:1/none?connect_timeout=2"]) == 0
    assert "paused" in capsys.readouterr().out
    with pytest.raises(GatewayError, match="paused"):
        Gateway(cfg, t).whoami()
    assert main(["--config", conf, "resume", "ops-bot", "--admin-dsn", "postgresql://nobody@127.0.0.1:1/none?connect_timeout=2"]) == 0
    assert main(["--config", conf, "revoke-token", t]) == 0
    with pytest.raises(GatewayError, match="revoked"):
        Gateway(cfg, t).whoami()
    events = [e["event"] for e in Ledger(cfg.ledger_path, cfg.signing_key()).entries()]
    assert {"agent_paused", "agent_resumed", "token_revoked"} <= set(events)


def test_dashboard_pauses_and_resumes_agents(cfg):
    import http.server
    import json
    import threading
    import urllib.error
    import urllib.request

    from lokra.dashboard import make_handler

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cfg, "postgresql://nobody@127.0.0.1:1/none?connect_timeout=2"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def post(path, body=None, origin=None):
        headers = {"Content-Type": "application/json", **({"Origin": origin} if origin else {})}
        req = urllib.request.Request(base + path, data=json.dumps(body or {}).encode(), method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    try:
        assert post("/api/agents/ops-bot/pause", origin="https://evil.example")[0] == 403
        status, body = post("/api/agents/ops-bot/pause", {"reason": "runaway loop"})
        assert status == 200 and body["paused"] and "could not disable" in body["detail"]
        with urllib.request.urlopen(base + "/api/agents") as r:
            agents = {a["name"]: a for a in json.loads(r.read())["agents"]}
        assert agents["ops-bot"]["paused"]["reason"] == "runaway loop" and agents["loop-bot"]["paused"] is None
        with pytest.raises(GatewayError, match="runaway loop"):
            Gateway(cfg, _token(cfg, "ops-bot")).whoami()
        assert post("/api/agents/ops-bot/resume")[0] == 200
        assert post("/api/agents/ghost/pause")[0] == 404
        assert Gateway(cfg, _token(cfg, "ops-bot")).whoami()["agent"] == "ops-bot"
    finally:
        server.shutdown()


def test_admin_connection_is_never_guessed_for_a_real_database(tmp_path, capsys, monkeypatch):
    from lokra.cli import main
    monkeypatch.delenv("LOKRA_ADMIN_DSN", raising=False)
    (tmp_path / "policies.yaml").write_text("database: {host: db.example, port: 5432, dbname: shop}\n" + POLICY)
    conf = str(tmp_path / "policies.yaml")
    assert main(["--config", conf, "provision"]) == 1
    assert 'pass --admin-dsn "postgresql://…", an admin login to shop on db.example:5432' in capsys.readouterr().err
    assert main(["--config", conf, "check"]) == 1
    assert "pass --dsn" in capsys.readouterr().err
    assert main(["--config", conf, "pause", "ops-bot"]) == 0
    assert "database login unchanged (no admin connection" in capsys.readouterr().out
    assert main(["--config", conf, "resume", "ops-bot"]) == 0
