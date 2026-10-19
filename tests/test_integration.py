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
    clinic_ids: [1]
    read:
      clinics: "*"
      patients: [id, clinic_id, full_name, dob, phone, medicare_number]
      appointments: "*"
    write:
      appointments: [insert, update]
    max_rows: 25
  test-researcher:
    clinic_ids: [1, 2, 3]
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
    out = gw(cfg, "test-scheduler").query(
        "SELECT medicare_number, medicare_number AS m2, phone, dob FROM patients LIMIT 1")
    medicare, alias, phone, dob = out["rows"][0]
    assert medicare.startswith("******") and alias == "[MEDICARE]"
    assert phone.startswith("*") and len(dob) == 4


def test_masking_free_text(cfg):
    out = gw(cfg, "test-researcher").query("SELECT note FROM clinical_notes")
    text = " ".join(r[0] for r in out["rows"])
    assert "[MEDICARE]" in text and "[IHI]" in text and "[EMAIL]" in text and "[PHONE]" in text
    assert "@example.com" not in text


def test_stacked_statements_blocked_even_without_parser(cfg, monkeypatch):
    monkeypatch.setattr(sqlcheck, "check_read", lambda s: sqlcheck.Checked("read", "select", [], []))
    with pytest.raises(GatewayError, match="multiple commands"):
        gw(cfg, "test-scheduler").query("SELECT 1; DROP TABLE patients")


def test_cte_write_blocked_by_read_only_txn_even_without_parser(cfg, monkeypatch):
    monkeypatch.setattr(sqlcheck, "check_read", lambda s: sqlcheck.Checked("read", "select", [], []))
    with pytest.raises(GatewayError, match="read-only transaction"):
        gw(cfg, "test-scheduler").query(
            "WITH d AS (UPDATE appointments SET status='x' WHERE id=1 RETURNING id) SELECT * FROM d")


@pytest.mark.parametrize("sql,expect", [
    ("DROP TABLE patients", "must be owner"),
    ("SET ROLE postgres", "permission denied"),
    ("SET SESSION AUTHORIZATION postgres", "permission denied"),
    ("SELECT * FROM lokra.agent_scopes", "permission denied"),
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
        "agents:\n  ui-alpha:\n    clinic_ids: [1]\n    read: {patients: [id, clinic_id]}\n    max_rows: 10\n"
        "  ui-beta:\n    clinic_ids: [2]\n    read: {clinics: \"*\"}\n"
        "masking: {columns: {}, detectors: []}\n")
    c = load_config(tmp_path / "policies.yaml")
    try:
        provision(c, LAB_ADMIN_DSN)
        payload = policy_payload(c)
        assert {a["name"] for a in payload["agents"]} == {"ui-alpha", "ui-beta"}
        # drop ui-beta, add a write grant to ui-alpha, through the same path the UI uses
        new = {
            "agents": [{"name": "ui-alpha", "clinic_ids": [1],
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
    finally:
        with psycopg.connect(LAB_ADMIN_DSN, autocommit=True) as conn:
            for role in ("lokra_ui_alpha", "lokra_ui_beta"):
                if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
                    conn.execute(f"DROP OWNED BY {role}")
                    conn.execute(f"DROP ROLE IF EXISTS {role}")
                conn.execute("DELETE FROM lokra.agent_scopes WHERE role_name = %s", (role,))


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
    doc = _build_doc(cfg, {"agents": [{"name": "ok", "clinic_ids": [1],
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
