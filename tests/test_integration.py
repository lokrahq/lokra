import json
from pathlib import Path

import psycopg
import pytest

from lokra import sqlcheck, tokens
from lokra.config import LAB_ADMIN_DSN, load_config
from lokra.gateway import Gateway, GatewayError, decide
from lokra.ledger import Ledger
from lokra.provision import provision

POLICY = """
database: {host: 127.0.0.1, port: 55432, dbname: clinic}
agents:
  test-scheduler:
    rows: {column: clinic_id, values: [1], tables: {clinics: id}}
    read:
      clinics: "*"
      patients: [id, clinic_id, full_name, dob, phone, medicare_number]
      appointments: "*"
    write:
      appointments: [insert, update]
    max_rows: 25
  test-researcher:
    rows: {column: clinic_id, values: [1, 2, 3]}
    read:
      patients: [id, clinic_id, dob]
      clinical_notes: [id, clinic_id, patient_id, note]
masking:
  columns: {medicare_number: last4, phone: last3, dob: year_only}
  detectors: [ihi, medicare, email, au_phone]
"""


def _db_up() -> bool:
    try:
        psycopg.connect(LAB_ADMIN_DSN, connect_timeout=2).close()
        return True
    except psycopg.Error:
        return False


pytestmark = pytest.mark.skipif(not _db_up(), reason="lab database not running (docker compose up -d)")


@pytest.fixture(scope="module")
def cfg(tmp_path_factory):
    d = tmp_path_factory.mktemp("lokra")
    (d / "policies.yaml").write_text(POLICY)
    c = load_config(d / "policies.yaml")
    provision(c, LAB_ADMIN_DSN)
    return c


def gw(cfg, agent):
    return Gateway(cfg, tokens.mint(cfg.signing_key(), agent, "pytest", 300))


def raw(cfg, agent):
    role = cfg.agent(agent).role
    return psycopg.connect(host="127.0.0.1", port=55432, dbname="clinic", user=role,
                           password=cfg.db_secrets()[role], autocommit=True)


@pytest.fixture
def admin():
    with psycopg.connect(LAB_ADMIN_DSN, autocommit=True) as c:
        yield c


def test_row_scope_single_clinic(cfg):
    out = gw(cfg, "test-scheduler").query("SELECT DISTINCT clinic_id FROM patients")
    assert out["rows"] == [[1]]


def test_row_scope_multi_clinic(cfg):
    out = gw(cfg, "test-researcher").query("SELECT DISTINCT clinic_id FROM patients ORDER BY 1")
    assert out["rows"] == [[1], [2], [3]]


def test_column_grant_blocks_ihi(cfg):
    with pytest.raises(GatewayError, match="permission denied"):
        gw(cfg, "test-scheduler").query("SELECT ihi FROM patients")


def test_table_grant_blocks_notes(cfg):
    with pytest.raises(GatewayError, match="permission denied"):
        gw(cfg, "test-scheduler").query("SELECT note FROM clinical_notes")


def test_max_rows_truncates(cfg):
    out = gw(cfg, "test-scheduler").query("SELECT id FROM appointments")
    assert out["row_count"] == 25 and out["truncated"] is True


def test_list_tables_reflects_grants(cfg):
    t = gw(cfg, "test-scheduler").list_tables()["tables"]
    assert "clinical_notes" not in t
    assert "ihi" not in t["patients"]["read"]
    assert "medicare_number" in t["patients"]["read"]


def test_masking_columns_and_aliases(cfg):
    g = gw(cfg, "test-scheduler")
    medicare, phone, dob = g.query("SELECT medicare_number, phone, dob FROM patients LIMIT 1")["rows"][0]
    assert medicare.startswith("******") and phone.startswith("*") and len(dob) == 4
    for sql in ("SELECT medicare_number AS m2 FROM patients",
                "SELECT row_to_json(p) FROM patients p",
                "SELECT array_agg(dob) FROM patients",
                "SELECT x FROM patients, unnest(ARRAY[phone]) AS x"):
        with pytest.raises(GatewayError, match="masked|whole row"):
            g.query(sql)
    assert g.query("SELECT count(*) FROM patients WHERE dob < '1990-01-01'")["rows"][0][0] >= 0


def test_masking_reaches_into_json(cfg):
    out = gw(cfg, "test-scheduler").query(
        "SELECT json_build_object('note', 'mail ana@example.com', 'medicare_number', '2123 45670 1') AS j, "
        "ARRAY['call 0412 345 678'] AS a")
    j, a = out["rows"][0]
    assert j == {"note": "mail [EMAIL]", "medicare_number": "******6701"}
    assert a == ["call [PHONE]"]


def test_masking_free_text(cfg):
    out = gw(cfg, "test-researcher").query("SELECT note FROM clinical_notes")
    text = " ".join(r[0] for r in out["rows"])
    assert "[MEDICARE]" in text and "[IHI]" in text and "[EMAIL]" in text and "[PHONE]" in text
    assert "@example.com" not in text


def test_stacked_statements_blocked_even_without_parser(cfg, monkeypatch):
    monkeypatch.setattr(sqlcheck, "check_read", lambda s, masked=(): sqlcheck.Checked("read", "select", [], []))
    with pytest.raises(GatewayError, match="multiple commands"):
        gw(cfg, "test-scheduler").query("SELECT 1; DROP TABLE patients")


def test_cte_write_blocked_by_read_only_txn_even_without_parser(cfg, monkeypatch):
    monkeypatch.setattr(sqlcheck, "check_read", lambda s, masked=(): sqlcheck.Checked("read", "select", [], []))
    with pytest.raises(GatewayError, match="read-only transaction"):
        gw(cfg, "test-scheduler").query(
            "WITH d AS (UPDATE appointments SET status='x' WHERE id=1 RETURNING id) SELECT * FROM d")


@pytest.mark.parametrize("sql,expect", [
    ("DROP TABLE patients", "must be owner"),
    ("SET ROLE postgres", "permission denied"),
    ("SET SESSION AUTHORIZATION postgres", "permission denied"),
    ("SELECT * FROM pg_authid", "permission denied"),
    ("DELETE FROM patients", "permission denied"),
    ("CREATE TABLE stolen (x int)", "permission denied"),
    ("SELECT pg_read_file('/etc/passwd')", "permission denied"),
    ("INSERT INTO appointments (clinic_id, patient_id, starts_at, reason) "
     "VALUES (2, 21, now(), 'x')", "row-level security"),
])
def test_direct_database_attacks_as_agent_role(cfg, sql, expect):
    with raw(cfg, "test-scheduler") as c:
        with pytest.raises(psycopg.Error, match=expect):
            c.execute(sql)


def test_set_config_cannot_widen_scope(cfg):
    with raw(cfg, "test-scheduler") as c:
        c.execute("SELECT set_config('app.clinic_id', '2', false)")
        assert c.execute("SELECT DISTINCT clinic_id FROM patients").fetchall() == [(1,)]


def test_expired_or_unknown_agent_denied_and_logged(cfg):
    with pytest.raises(GatewayError, match="denied"):
        Gateway(cfg, "lokra1.garbage.sig").query("SELECT 1")
    with pytest.raises(GatewayError, match="unknown agent"):
        Gateway(cfg, tokens.mint(cfg.signing_key(), "ghost", "x", 60)).query("SELECT 1")
    events = [e["event"] for e in Ledger(cfg.ledger_path, cfg.signing_key()).entries()]
    assert events.count("denied") >= 2


def test_write_flow(cfg, admin):
    g = gw(cfg, "test-scheduler")
    with pytest.raises(GatewayError, match="propose_write"):
        g.query("UPDATE appointments SET status='cancelled' WHERE id=2")
    p = g.propose_write("UPDATE appointments SET status='cancelled' WHERE id=2", "pytest")
    assert p["would_affect_rows"] == 1
    assert admin.execute("SELECT status FROM appointments WHERE id=2").fetchone()[0] == "booked"
    with pytest.raises(GatewayError, match="not approved"):
        g.execute_write(p["write_id"])
    decide(cfg, p["write_id"], True, "pytest-human")
    assert g.execute_write(p["write_id"])["affected_rows"] == 1
    assert admin.execute("SELECT status FROM appointments WHERE id=2").fetchone()[0] == "cancelled"
    with pytest.raises(GatewayError, match="executed"):
        g.execute_write(p["write_id"])
    admin.execute("UPDATE appointments SET status='booked' WHERE id=2")


def test_write_denied(cfg):
    g = gw(cfg, "test-scheduler")
    p = g.propose_write("UPDATE appointments SET status='x' WHERE id=3", "pytest")
    decide(cfg, p["write_id"], False, "pytest-human")
    with pytest.raises(GatewayError, match="denied"):
        g.execute_write(p["write_id"])


def test_other_agent_cannot_execute_your_write(cfg):
    p = gw(cfg, "test-scheduler").propose_write("UPDATE appointments SET status='x' WHERE id=4", "pytest")
    decide(cfg, p["write_id"], True, "pytest-human")
    with pytest.raises(GatewayError, match="unknown write_id"):
        gw(cfg, "test-researcher").execute_write(p["write_id"])


def test_read_only_agent_cannot_write_even_if_approved(cfg):
    with pytest.raises(GatewayError, match="permission denied"):
        gw(cfg, "test-researcher").propose_write("UPDATE patients SET dob = now() WHERE id = 1", "pytest")


def test_ledger_intact_and_masks_sql(cfg):
    gw(cfg, "test-scheduler").query("SELECT id FROM patients WHERE medicare_number = '4332 18195 7'")
    led = Ledger(cfg.ledger_path, cfg.signing_key())
    assert led.verify()[0]
    assert "4332 18195 7" not in cfg.ledger_path.read_text()


def test_agent_can_see_what_an_approved_write_does(cfg):
    g = gw(cfg, "test-scheduler")
    p = g.propose_write("UPDATE appointments SET reason = reason WHERE id = 5", "no-op check")
    decide(cfg, p["write_id"], True, "pytest-human")
    status = g.write_status(p["write_id"])
    assert status["sql"].startswith("UPDATE appointments") and status["reason"] == "no-op check"
    assert status["approved_by"] == "pytest-human" and status["tables"] == ["appointments"]
    done = g.execute_write(p["write_id"])
    assert done["status"] == "executed" and done["affected_rows"] == 1 and "sql" in done


def test_dashboard_api_payload(cfg):
    from lokra.dashboard import activity_payload, api_payload, make_handler
    gw(cfg, "test-scheduler").query("SELECT full_name FROM patients LIMIT 1")
    payload = api_payload(cfg)
    assert payload["report"]["chain_verified"] is True
    assert isinstance(payload["recent"], list)
    assert any(a["agent"] == "test-scheduler" for a in payload["report"]["agents"])
    act = activity_payload(cfg, limit=5)
    assert act["chain_verified"] is True and act["shown"] <= 5
    assert act["shown"] <= act["total"] and (not act["entries"] or "detail" in act["entries"][0])
    make_handler(cfg, LAB_ADMIN_DSN)  # loads the packaged dashboard.html


def test_dashboard_applies_policy_and_drops_removed_roles(tmp_path):
    from lokra.dashboard import apply_policy, policy_payload
    (tmp_path / "policies.yaml").write_text(
        "database: {host: 127.0.0.1, port: 55432, dbname: clinic}\n"
        "agents:\n  ui-alpha:\n    rows: {column: clinic_id, values: [1]}\n    read: {patients: [id, clinic_id]}\n    max_rows: 10\n"
        "  ui-beta:\n    rows: {column: id, values: [2]}\n    read: {clinics: \"*\"}\n"
        "masking: {columns: {}, detectors: []}\n")
    c = load_config(tmp_path / "policies.yaml")
    try:
        provision(c, LAB_ADMIN_DSN)
        payload = policy_payload(c)
        assert {a["name"] for a in payload["agents"]} == {"ui-alpha", "ui-beta"}
        # drop ui-beta, add a write grant to ui-alpha, through the same path the UI uses
        new = {
            "agents": [{"name": "ui-alpha", "rows": {"column": "clinic_id", "values": "1"},
                        "read": [{"table": "patients", "columns": "id, clinic_id"}],
                        "write": [{"table": "appointments", "ops": ["insert"]}], "max_rows": 10}],
            "masking": {"columns": [{"column": "phone", "strategy": "last3"}], "detectors": ["medicare"]},
            "approval_ttl_seconds": 1800,
        }
        res = apply_policy(c, LAB_ADMIN_DSN, new)
        assert res["ok"] and res["roles"] == ["lokra_ui_alpha"]
        with psycopg.connect(LAB_ADMIN_DSN, autocommit=True) as conn:
            assert conn.execute("SELECT 1 FROM pg_roles WHERE rolname = 'lokra_ui_beta'").fetchone() is None
            assert conn.execute("SELECT 1 FROM pg_roles WHERE rolname = 'lokra_ui_alpha'").fetchone() is not None
        reloaded = policy_payload(load_config(tmp_path / "policies.yaml"))
        assert reloaded["approval_ttl_seconds"] == 1800
        assert reloaded["masking"]["detectors"] == ["medicare"]
        assert reloaded["agents"][0]["rows"] == {"column": "clinic_id", "values": [1]}
    finally:
        with psycopg.connect(LAB_ADMIN_DSN, autocommit=True) as conn:
            for role in ("lokra_ui_alpha", "lokra_ui_beta"):
                if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
                    conn.execute(f"DROP OWNED BY {role}")
                    conn.execute(f"DROP ROLE IF EXISTS {role}")


def test_build_doc_rejects_bad_input():
    from lokra.dashboard import _build_doc
    cfg = load_config_stub()
    with pytest.raises(ValueError):
        _build_doc(cfg, {"agents": []})
    with pytest.raises(ValueError):
        _build_doc(cfg, {"agents": [{"name": "a b/c", "read": []}]})
    with pytest.raises(ValueError):
        _build_doc(cfg, {"agents": [{"name": "ok", "max_rows": 5}],
                         "masking": {"columns": [{"column": "x", "strategy": "bogus"}]}})
    doc = _build_doc(cfg, {"agents": [{"name": "ok", "rows": {"column": "clinic_id", "values": [1]},
                                       "read": [{"table": "patients", "columns": "*"}],
                                       "write": [{"table": "appointments", "ops": ["insert", "bad"]}],
                                       "max_rows": 7}],
                           "masking": {"columns": [{"column": "Phone", "strategy": "last3"}],
                                       "detectors": ["medicare", "nope"]}})
    assert doc["agents"]["ok"]["write"] == {"appointments": ["insert"]}
    assert doc["masking"]["columns"] == {"phone": "last3"}
    assert doc["masking"]["detectors"] == ["medicare"]


def load_config_stub():
    import tempfile
    d = tempfile.mkdtemp()
    p = Path(d) / "policies.yaml"
    p.write_text("database: {host: 127.0.0.1, port: 55432, dbname: clinic}\nagents: {}\n")
    return load_config(p)


def test_lokra_init_introspects_lab_db(tmp_path):
    from lokra.config import dump_policy
    from lokra.introspect import build_policy, introspect

    tables = introspect(LAB_ADMIN_DSN)
    assert {"patients", "appointments", "clinics"} <= set(tables)
    doc = build_policy(LAB_ADMIN_DSN, tables)
    cols = doc["masking"]["columns"]
    assert cols.get("medicare_number") == "last4"
    assert cols.get("dob") == "year_only"
    assert cols.get("phone") == "last3"
    (tmp_path / "policies.yaml").write_text(dump_policy(doc))
    c = load_config(tmp_path / "policies.yaml")
    assert "app-reader" in c.agents and c.agents["app-reader"].rows is None


def test_provision_without_row_lock(tmp_path):
    (tmp_path / "policies.yaml").write_text(
        "database: {host: 127.0.0.1, port: 55432, dbname: clinic}\n"
        "agents:\n  ui-plain:\n    read: {clinics: \"*\"}\n    max_rows: 50\n"
        "masking: {columns: {}, detectors: []}\n")
    c = load_config(tmp_path / "policies.yaml")
    role = c.agents["ui-plain"].role
    try:
        provision(c, LAB_ADMIN_DSN)
        with psycopg.connect(LAB_ADMIN_DSN, autocommit=True) as conn:
            assert conn.execute("SELECT has_table_privilege(%s, 'clinics', 'SELECT')", (role,)).fetchone()[0]
        with raw(c, "ui-plain") as r:
            assert r.execute("SELECT count(*) FROM clinics").fetchone()[0] == 3
    finally:
        with psycopg.connect(LAB_ADMIN_DSN, autocommit=True) as conn:
            if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
                conn.execute(f"DROP OWNED BY {role}")
                conn.execute(f"DROP ROLE IF EXISTS {role}")


def test_policy_check_against_lab(cfg):
    from lokra.check import check
    findings = check(cfg, LAB_ADMIN_DSN)
    assert [m for lvl, m in findings if lvl == "error"] == []


def test_rows_lock_any_table_without_touching_other_roles(tmp_path):
    policy = tmp_path / "policies.yaml"
    locked = ("database: {host: 127.0.0.1, port: 55432, dbname: clinic}\n"
              "agents:\n"
              "  acct-bot:\n    rows: {column: account_id, values: [7]}\n"
              "    read: {lk_orders: \"*\"}\n    write: {lk_orders: [insert]}\n"
              "  acct-all:\n    read: {lk_orders: \"*\"}\n")
    roles = ("lokra_acct_bot", "lokra_acct_all")
    with psycopg.connect(LAB_ADMIN_DSN, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS lk_orders")
        conn.execute("CREATE TABLE lk_orders (id int GENERATED ALWAYS AS IDENTITY PRIMARY KEY, "
                     "account_id int NOT NULL, total numeric NOT NULL)")
        conn.execute("INSERT INTO lk_orders (account_id, total) VALUES (7, 10), (7, 20), (8, 30)")
        conn.execute("DROP ROLE IF EXISTS lk_app")
        conn.execute("CREATE ROLE lk_app LOGIN PASSWORD 'lk_app'")
        conn.execute("GRANT SELECT ON lk_orders TO lk_app")
    try:
        policy.write_text(locked)
        c = load_config(policy)
        log = provision(c, LAB_ADMIN_DSN)
        assert any("rows where account_id in [7]" in line for line in log)
        with raw(c, "acct-bot") as r:
            assert r.execute("SELECT DISTINCT account_id FROM lk_orders").fetchall() == [(7,)]
            with pytest.raises(psycopg.Error, match="row-level security"):
                r.execute("INSERT INTO lk_orders (account_id, total) VALUES (8, 1)")
        with raw(c, "acct-all") as r:
            assert r.execute("SELECT count(*) FROM lk_orders").fetchone()[0] == 3
        with psycopg.connect(host="127.0.0.1", port=55432, dbname="clinic", user="lk_app", password="lk_app") as app:
            assert app.execute("SELECT count(*) FROM lk_orders").fetchone()[0] == 3

        policy.write_text(locked.replace("    rows: {column: account_id, values: [7]}\n", ""))
        provision(load_config(policy), LAB_ADMIN_DSN)
        with psycopg.connect(LAB_ADMIN_DSN, autocommit=True) as conn:
            assert conn.execute("SELECT relrowsecurity FROM pg_class WHERE relname = 'lk_orders'").fetchone()[0] is False
            assert conn.execute("SELECT count(*) FROM pg_policies WHERE tablename = 'lk_orders'").fetchone()[0] == 0
        with raw(c, "acct-bot") as r:
            assert r.execute("SELECT count(*) FROM lk_orders").fetchone()[0] == 3
    finally:
        with psycopg.connect(LAB_ADMIN_DSN, autocommit=True) as conn:
            conn.execute("DROP TABLE IF EXISTS lk_orders")
            for role in (*roles, "lk_app"):
                if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
                    conn.execute(f"DROP OWNED BY {role}")
                    conn.execute(f"DROP ROLE IF EXISTS {role}")


def test_dashboard_approves_and_denies_writes(cfg):
    import http.server
    import threading
    import urllib.error
    import urllib.request

    from lokra.dashboard import make_handler, writes_payload

    agent = gw(cfg, "test-scheduler")
    first = agent.propose_write("UPDATE appointments SET status = 'confirmed' WHERE id = 1", "patient confirmed")
    second = agent.propose_write("UPDATE appointments SET status = 'cancelled' WHERE id = 1", "duplicate")
    pending = {w["id"]: w for w in writes_payload(cfg)["pending"]}
    assert {first["write_id"], second["write_id"]} <= set(pending)
    assert pending[first["write_id"]]["would_affect_rows"] == 1

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cfg, LAB_ADMIN_DSN))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def post(path, origin=None):
        req = urllib.request.Request(base + path, data=b"", method="POST",
                                     headers={"Origin": origin} if origin else {})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    try:
        status, _ = post(f"/api/writes/{first['write_id']}/approve", origin="https://evil.example")
        assert status == 403
        status, body = post(f"/api/writes/{first['write_id']}/approve", origin=base)
        assert status == 200 and body["write"]["status"] == "approved"
        status, body = post(f"/api/writes/{second['write_id']}/deny")
        assert status == 200 and body["write"]["status"] == "denied"
        status, body = post(f"/api/writes/{second['write_id']}/approve")
        assert status == 409 and "already" in body["error"]
    finally:
        server.shutdown()

    assert agent.execute_write(first["write_id"])["status"] == "executed"
    with pytest.raises(GatewayError, match="denied"):
        agent.execute_write(second["write_id"])
    recent = {w["id"]: w for w in writes_payload(cfg)["recent"]}
    assert recent[first["write_id"]]["status"] == "executed"
    assert recent[second["write_id"]]["decided_by"].endswith("(dashboard)")


def test_http_transport_takes_a_token_per_request(cfg):
    import socket
    import threading
    import time
    import urllib.request

    import uvicorn

    from lokra.server import build_server

    app = build_server(cfg, http=True).streamable_http_app(stateless_http=True, json_response=True)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)

    def call(tool, arguments, token=None):
        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}
        req = urllib.request.Request(f"http://127.0.0.1:{port}/mcp", data=json.dumps(body).encode(),
                                     headers=headers, method="POST")
        with urllib.request.urlopen(req) as r:
            result = json.loads(r.read())["result"]
        return result["isError"], result["content"][0]["text"]

    try:
        sched = tokens.mint(cfg.signing_key(), "test-scheduler", "pytest", 60)
        research = tokens.mint(cfg.signing_key(), "test-researcher", "pytest", 60)
        err, text = call("query", {"sql": "SELECT DISTINCT clinic_id FROM patients ORDER BY 1"}, sched)
        assert not err and json.loads(text)["rows"] == [[1]]
        err, text = call("query", {"sql": "SELECT DISTINCT clinic_id FROM patients ORDER BY 1"}, research)
        assert not err and json.loads(text)["rows"] == [[1], [2], [3]]
        err, text = call("whoami", {})
        assert err and "no agent token" in text
        err, text = call("whoami", {}, sched[:-2] + "xx")
        assert err and "signature" in text
    finally:
        server.should_exit = True


def test_tables_in_other_schemas(tmp_path):
    from lokra.introspect import introspect

    policy = tmp_path / "policies.yaml"
    granted = ("database: {host: 127.0.0.1, port: 55432, dbname: clinic}\n"
               "agents:\n  bill-bot:\n    rows: {column: account_id, values: [7]}\n"
               "    read: {lk_billing.invoices: \"*\"}\n"
               "masking: {columns: {email: redact}, detectors: []}\n")
    with psycopg.connect(LAB_ADMIN_DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS lk_billing CASCADE")
        conn.execute("CREATE SCHEMA lk_billing")
        conn.execute("CREATE TABLE lk_billing.invoices (id int PRIMARY KEY, account_id int, email text)")
        conn.execute("INSERT INTO lk_billing.invoices VALUES (1, 7, 'a@x.com'), (2, 8, 'b@y.com')")
        conn.execute("CREATE TABLE lk_billing.secrets (x text)")
    try:
        assert "lk_billing.invoices" in introspect(LAB_ADMIN_DSN, ["public", "lk_billing"])
        policy.write_text(granted)
        c = load_config(policy)
        assert any("lk_billing.invoices" in line for line in provision(c, LAB_ADMIN_DSN))
        agent = gw(c, "bill-bot")
        assert list(agent.list_tables()["tables"]) == ["lk_billing.invoices"]
        out = agent.query("SELECT account_id, email FROM lk_billing.invoices")
        assert [r[0] for r in out["rows"]] == [7] and out["rows"][0][1] != "a@x.com"
        with pytest.raises(GatewayError, match="permission denied"):
            agent.query("SELECT * FROM lk_billing.secrets")
        reads = [e for e in Ledger(c.ledger_path, c.signing_key()).entries() if e["event"] == "read"]
        assert reads[-1]["tables"] == ["lk_billing.invoices"]

        policy.write_text(granted.replace("lk_billing.invoices", "clinics").replace(
            "    rows: {column: account_id, values: [7]}\n", ""))
        provision(load_config(policy), LAB_ADMIN_DSN)
        with raw(c, "bill-bot") as r:
            with pytest.raises(psycopg.Error, match="permission denied for schema"):
                r.execute("SELECT * FROM lk_billing.invoices")

        policy.write_text(granted.replace("lk_billing.invoices", "lk_billing.nope"))
        with pytest.raises(ValueError, match="lk_billing.nope"):
            provision(load_config(policy), LAB_ADMIN_DSN)
    finally:
        with psycopg.connect(LAB_ADMIN_DSN, autocommit=True) as conn:
            conn.execute("DROP SCHEMA IF EXISTS lk_billing CASCADE")
            if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = 'lokra_bill_bot'").fetchone():
                conn.execute("DROP OWNED BY lokra_bill_bot")
                conn.execute("DROP ROLE lokra_bill_bot")


def test_pause_blocks_the_database_login_and_survives_provision(cfg):
    from lokra import controls
    from lokra.provision import set_login

    agent = cfg.agent("test-researcher")
    try:
        controls.pause(cfg, agent.name, "pytest")
        set_login(cfg, LAB_ADMIN_DSN, agent, allowed=False)
        with pytest.raises(psycopg.OperationalError, match="not permitted to log in"):
            raw(cfg, "test-researcher")
        assert any(line.endswith("PAUSED") or ", PAUSED," in line for line in provision(cfg, LAB_ADMIN_DSN))
        with pytest.raises(psycopg.OperationalError, match="not permitted to log in"):
            raw(cfg, "test-researcher")
    finally:
        controls.resume(cfg, agent.name)
        set_login(cfg, LAB_ADMIN_DSN, agent, allowed=True)
    with raw(cfg, "test-researcher") as r:
        assert r.execute("SELECT 1").fetchone() == (1,)


def test_demo_runs_end_to_end_and_cleans_up():
    from lokra.demo import DemoError, run_demo

    lines: list[str] = []
    run_demo(LAB_ADMIN_DSN, out=lines.append)
    text = "\n".join(lines)
    assert "3 of 6 customers." in text and "✗ hidden" in text
    assert "[CARD]" in text and "ben@acme.example" not in text and "4012 8888" not in text
    assert text.count("✗ blocked") == 5
    assert "one statement per call" in text and "pg_read_file() not allowed" in text
    assert "writes wait for a person" in text
    assert "2 rows updated" in text and "rate limit, 5 a minute" in text
    assert "paused by you (demo)" in text and "hash chain verified" in text
    assert "retry loop" in text and "probing" in text
    with psycopg.connect(LAB_ADMIN_DSN, autocommit=True) as conn:
        assert not conn.execute("SELECT 1 FROM pg_namespace WHERE nspname = 'lokra_demo'").fetchone()
        assert not conn.execute("SELECT 1 FROM pg_roles WHERE rolname LIKE 'lokra\\_demo%'").fetchone()
        assert conn.execute("SELECT 1 FROM pg_roles WHERE rolname = 'lokra_agents'").fetchone()
        conn.execute("CREATE SCHEMA lokra_demo")
    try:
        with pytest.raises(DemoError, match="already exists"):
            run_demo(LAB_ADMIN_DSN, out=lines.append)
    finally:
        with psycopg.connect(LAB_ADMIN_DSN, autocommit=True) as conn:
            conn.execute("DROP SCHEMA lokra_demo")


def test_connect_logs_in_as_the_agent(cfg):
    from lokra.connect import readable_tables
    assert readable_tables(cfg, cfg.agent("test-scheduler")) == 3
    assert readable_tables(cfg, cfg.agent("test-researcher")) == 2


def test_admin_connection_must_be_the_policy_database(cfg):
    from lokra.provision import set_login
    other = LAB_ADMIN_DSN.rsplit("/", 1)[0] + "/postgres"
    with pytest.raises(ValueError, match="is to database 'postgres', but the policy is for 'clinic'"):
        provision(cfg, other)
    with pytest.raises(ValueError, match="but the policy is for 'clinic'"):
        set_login(cfg, other, cfg.agent("test-scheduler"), allowed=False)
    with raw(cfg, "test-scheduler") as conn:
        assert conn.execute("SELECT 1").fetchone() == (1,)


def test_write_that_changes_nothing_says_so(cfg):
    out = gw(cfg, "test-scheduler").propose_write(
        "UPDATE appointments SET status = 'cancelled' WHERE id = -1", "nothing to change")
    assert out["would_affect_rows"] == 0 and any("changes no rows" in w for w in out["warnings"])


def test_non_superuser_admin_can_provision_again(tmp_path, admin):
    """The Supabase and RDS shape: the admin has CREATEROLE but is not a superuser."""
    def reset():
        admin.execute("DROP SCHEMA IF EXISTS lokra_t CASCADE")
        for role in ("lokra_t_bot", "lokra_t_admin"):
            if admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
                admin.execute(f"DROP OWNED BY {role} CASCADE")
                admin.execute(f"DROP ROLE {role}")

    reset()
    if not admin.execute("SELECT 1 FROM pg_roles WHERE rolname = 'lokra_agents'").fetchone():
        admin.execute("CREATE ROLE lokra_agents NOLOGIN")
    admin.execute("CREATE ROLE lokra_t_admin LOGIN CREATEROLE PASSWORD 'lokra_t_admin'")
    admin.execute("GRANT lokra_agents TO lokra_t_admin WITH ADMIN OPTION")
    admin.execute("GRANT CREATE ON DATABASE clinic TO lokra_t_admin")
    dsn = "postgresql://lokra_t_admin:lokra_t_admin@127.0.0.1:55432/clinic"
    try:
        with psycopg.connect(dsn, autocommit=True) as owner:
            owner.execute("CREATE SCHEMA lokra_t")
            owner.execute("CREATE TABLE lokra_t.items (id int, org int)")
            owner.execute("INSERT INTO lokra_t.items VALUES (1, 1), (2, 2)")
            owner.execute("CREATE TABLE lokra_t.secrets (id int)")
        policy = tmp_path / "policies.yaml"
        head = ("database: {host: 127.0.0.1, port: 55432, dbname: clinic}\n"
                "agents:\n  t-bot:\n    rows: {column: org, values: [1]}\n")
        policy.write_text(head + "    read: {lokra_t.items: '*', lokra_t.secrets: '*'}\n")
        provision(load_config(policy), dsn)
        policy.write_text(head + "    read: {lokra_t.items: '*'}\n")
        c = load_config(policy)
        provision(c, dsn)
        assert gw(c, "t-bot").query("SELECT id FROM lokra_t.items")["rows"] == [[1]]
        with pytest.raises(GatewayError, match="permission denied for table secrets"):
            gw(c, "t-bot").query("SELECT id FROM lokra_t.secrets")
        admin.execute("ALTER ROLE lokra_t_bot BYPASSRLS")
        with pytest.raises(ValueError, match="has BYPASSRLS"):
            provision(c, dsn)
    finally:
        reset()


def test_views_cannot_carry_rows_past_a_row_lock(tmp_path, admin):
    from lokra.check import check

    def reset():
        admin.execute("DROP SCHEMA IF EXISTS lokra_v CASCADE")
        if admin.execute("SELECT 1 FROM pg_roles WHERE rolname = 'lokra_v_bot'").fetchone():
            admin.execute("DROP OWNED BY lokra_v_bot CASCADE")
            admin.execute("DROP ROLE lokra_v_bot")

    reset()
    admin.execute("CREATE SCHEMA lokra_v")
    admin.execute("CREATE TABLE lokra_v.items (id int, org int)")
    admin.execute("INSERT INTO lokra_v.items VALUES (1, 1), (2, 2)")
    admin.execute("CREATE VIEW lokra_v.all_items AS SELECT id FROM lokra_v.items")
    admin.execute("CREATE VIEW lokra_v.my_items WITH (security_invoker = true) AS SELECT id FROM lokra_v.items")
    admin.execute("CREATE MATERIALIZED VIEW lokra_v.item_count AS SELECT count(*) AS n FROM lokra_v.items")
    policy = tmp_path / "policies.yaml"
    head = ("database: {host: 127.0.0.1, port: 55432, dbname: clinic}\n"
            "agents:\n  v-bot:\n    rows: {column: org, values: [1]}\n")
    try:
        for extra, refusal in (("lokra_v.all_items", "all_items is a view, which runs with its owner's rights"),
                               ("lokra_v.item_count", "item_count is a materialized view")):
            policy.write_text(head + f"    read: {{lokra_v.items: '*', {extra}: '*'}}\n")
            c = load_config(policy)
            with pytest.raises(ValueError, match=refusal):
                provision(c, LAB_ADMIN_DSN)
            assert any(lvl == "error" and refusal in m for lvl, m in check(c, LAB_ADMIN_DSN))
        assert not admin.execute("SELECT 1 FROM pg_roles WHERE rolname = 'lokra_v_bot'").fetchone()
        policy.write_text(head + "    read: {lokra_v.items: '*', lokra_v.my_items: '*'}\n")
        c = load_config(policy)
        provision(c, LAB_ADMIN_DSN)
        assert gw(c, "v-bot").query("SELECT id FROM lokra_v.my_items")["rows"] == [[1]]
        assert not [f for f in check(c, LAB_ADMIN_DSN) if f[0] == "error"]
    finally:
        reset()


def test_approval_rules_on_the_database(tmp_path, admin):
    def reset():
        admin.execute("DROP SCHEMA IF EXISTS lokra_a CASCADE")
        if admin.execute("SELECT 1 FROM pg_roles WHERE rolname = 'lokra_a_bot'").fetchone():
            admin.execute("DROP OWNED BY lokra_a_bot CASCADE")
            admin.execute("DROP ROLE lokra_a_bot")

    reset()
    admin.execute("CREATE SCHEMA lokra_a")
    admin.execute("CREATE TABLE lokra_a.items (id int PRIMARY KEY, org int, status text)")
    admin.execute("INSERT INTO lokra_a.items VALUES (1, 1, 'new'), (2, 1, 'new'), (3, 1, 'done'), (4, 2, 'new')")
    policy = tmp_path / "policies.yaml"
    policy.write_text("database: {host: 127.0.0.1, port: 55432, dbname: clinic}\n"
                      "agents:\n  a-bot:\n    rows: {column: org, values: [1]}\n"
                      "    read: {lokra_a.items: '*'}\n    write: {lokra_a.items: [update, delete]}\n"
                      "    approval: {auto_max_rows: 2, auto_statements: [update, delete], two_people: [delete]}\n")
    c = load_config(policy)
    status = lambda i: admin.execute("SELECT status FROM lokra_a.items WHERE id = %s", (i,)).fetchone()
    try:
        provision(c, LAB_ADMIN_DSN)
        small = gw(c, "a-bot").propose_write("UPDATE lokra_a.items SET status = 'open' WHERE id = 1", "small fix")
        assert small["status"] == "approved" and small["approvals_needed"] == 0
        assert gw(c, "a-bot").execute_write(small["write_id"])["approved_by"] == "policy (up to 2 rows)"
        assert status(1) == ("open",)

        wide = gw(c, "a-bot").propose_write("UPDATE lokra_a.items SET status = 'open'", "everything")
        assert (wide["status"], wide["would_affect_rows"], wide["approvals_needed"]) == ("pending", 3, 1)

        delete = gw(c, "a-bot").propose_write("DELETE FROM lokra_a.items WHERE id = 3", "duplicate")
        assert (delete["status"], delete["approvals_needed"]) == ("pending", 2)
        decide(c, delete["write_id"], True, "alice")
        with pytest.raises(GatewayError, match="not approved"):
            gw(c, "a-bot").execute_write(delete["write_id"])
        decide(c, delete["write_id"], True, "bob")
        assert gw(c, "a-bot").execute_write(delete["write_id"])["affected_rows"] == 1
        assert status(3) is None

        late = gw(c, "a-bot").propose_write("UPDATE lokra_a.items SET status = 'late' WHERE status = 'new'", "chase")
        assert (late["status"], late["would_affect_rows"]) == ("approved", 1)
        admin.execute("INSERT INTO lokra_a.items VALUES (5, 1, 'new')")
        with pytest.raises(GatewayError, match="would now change 2 rows, more than the 1 that were approved"):
            gw(c, "a-bot").execute_write(late["write_id"])
        assert status(2) == ("new",) and status(5) == ("new",)
        assert gw(c, "a-bot").write_status(late["write_id"])["status"] == "failed"
        events = [e["event"] for e in Ledger(c.ledger_path, c.signing_key()).entries()]
        assert events.count("write_approved") == 3 and "write_partly_approved" in events
        assert events[-1] == "write_rolled_back"
    finally:
        reset()
