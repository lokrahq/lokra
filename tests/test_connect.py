import json
from pathlib import Path

import pytest

from lokra import connect
from lokra.cli import main

ONE = """
agents:
  app-reader:
    read: {orders: "*"}
"""

TWO = """
require_purpose: true
agents:
  support-bot:
    read: {orders: "*"}
    purposes: [support]
  ops-bot:
    read: {orders: "*"}
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData/Roaming"))
    monkeypatch.delenv("LOKRA_HOME", raising=False)
    return tmp_path


def _policy(project, text=ONE):
    path = project / "policies.yaml"
    path.write_text(text)
    return str(path)


def _connect(conf, *extra):
    return main(["--config", conf, "connect", "--no-check", *extra])


def test_writes_claude_code_config_for_the_only_agent(project):
    conf = _policy(project)
    assert _connect(conf) == 0
    server = json.loads((project / "work/.mcp.json").read_text())["mcpServers"]["lokra-app-reader"]
    assert server["args"][-5:] == ["--config", conf, "serve", "--dev-agent", "app-reader"]
    assert Path(server["command"]).is_absolute()


def test_keeps_other_servers_and_is_idempotent(project, capsys):
    conf = _policy(project)
    mcp = project / "work/.mcp.json"
    mcp.write_text(json.dumps({"mcpServers": {"github": {"command": "gh-mcp"}}, "other": 1}))
    assert _connect(conf) == 0
    first = mcp.read_text()
    assert _connect(conf) == 0
    assert mcp.read_text() == first
    doc = json.loads(first)
    assert doc["other"] == 1 and set(doc["mcpServers"]) == {"github", "lokra-app-reader"}
    assert "already in .mcp.json" in capsys.readouterr().out


def test_updates_its_own_entry_but_not_a_stranger(project, capsys):
    conf = _policy(project)
    mcp = project / "work/.mcp.json"
    mcp.write_text(json.dumps({"mcpServers": {"lokra-app-reader": {
        "command": "/old/lokra", "args": ["--config", "/old/policies.yaml", "serve"]}}}))
    assert _connect(conf) == 0
    assert "Updated" in capsys.readouterr().out
    mcp.write_text(json.dumps({"mcpServers": {"lokra-app-reader": {"command": "something-else"}}}))
    assert _connect(conf) == 1
    assert "not Lokra" in capsys.readouterr().err
    assert _connect(conf, "--force") == 0
    assert json.loads(mcp.read_text())["mcpServers"]["lokra-app-reader"]["args"][-1] == "app-reader"


def test_refuses_to_rewrite_a_file_it_cannot_parse(project, capsys):
    conf = _policy(project)
    vscode = project / "work/.vscode/mcp.json"
    vscode.parent.mkdir()
    vscode.write_text('{\n  // my servers\n  "servers": {}\n}\n')
    assert _connect(conf, "--client", "vscode") == 1
    assert "not plain JSON" in capsys.readouterr().err
    assert "// my servers" in vscode.read_text()


def test_vscode_and_desktop_formats(project):
    conf = _policy(project)
    assert _connect(conf, "--client", "vscode", "--name", "orders") == 0
    server = json.loads((project / "work/.vscode/mcp.json").read_text())["servers"]["orders"]
    assert server["type"] == "stdio"
    desktop = connect.client("claude-desktop").path
    desktop.parent.mkdir(parents=True)
    desktop.write_text(json.dumps({"preferences": {"theme": "light"}}))
    assert _connect(conf, "--client", "claude-desktop") == 0
    doc = json.loads(desktop.read_text())
    assert doc["preferences"] == {"theme": "light"} and "lokra-app-reader" in doc["mcpServers"]
    assert str(desktop).startswith(str(project / "home"))


def test_print_writes_nothing(project, capsys):
    conf = _policy(project)
    assert main(["--config", conf, "connect", "--client", "cursor", "--print"]) == 0
    assert "lokra-app-reader" in json.loads(capsys.readouterr().out)["mcpServers"]
    assert not (project / "work/.cursor").exists()


def test_names_the_agent_and_purpose_when_the_policy_needs_them(project, capsys):
    conf = _policy(project, TWO)
    assert _connect(conf) == 2
    assert "support-bot, ops-bot" in capsys.readouterr().err
    assert _connect(conf, "support-bot") == 1
    assert "requires a purpose-of-use" in capsys.readouterr().err
    assert _connect(conf, "support-bot", "--purpose", "marketing") == 1
    assert "not permitted" in capsys.readouterr().err
    assert _connect(conf, "support-bot", "--purpose", "support") == 0
    args = json.loads((project / "work/.mcp.json").read_text())["mcpServers"]["lokra-support-bot"]["args"]
    assert args[-2:] == ["--purpose", "support"]


def test_custom_home_is_passed_to_the_server(project):
    conf = _policy(project)
    assert main(["--config", conf, "--home", str(project / "state"), "connect", "--no-check"]) == 0
    args = json.loads((project / "work/.mcp.json").read_text())["mcpServers"]["lokra-app-reader"]["args"]
    assert args[args.index("--home") + 1] == str(project / "state")


def test_check_needs_a_provisioned_agent(project, capsys):
    conf = _policy(project)
    assert main(["--config", conf, "connect"]) == 1
    assert "not provisioned yet" in capsys.readouterr().err
    assert not (project / "work/.mcp.json").exists()
