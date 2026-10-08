import json
import time
from pathlib import Path

import pytest

from lokra import sqlcheck, tokens
from lokra.ledger import Ledger
from lokra.masking import Masker, luhn_valid, medicare_valid

REJECT_READ = [
    "SELECT 1; DROP TABLE patients",
    "COMMIT; DROP TABLE patients",
    "WITH d AS (DELETE FROM appointments RETURNING *) SELECT count(*) FROM d",
    "SELECT * INTO stolen FROM patients",
    "RESET ROLE",
    "SET ROLE postgres",
    "SELECT * FROM appointments FOR UPDATE",
    "UPDATE appointments SET status = 'x'",
    "COPY patients TO '/tmp/x'",
    "VACUUM",
    "",
    "SELEC broken",
]
ALLOW_READ = [
    "SELECT 1",
    "SELECT * FROM patients WHERE full_name LIKE '%a%'",
    "WITH x AS (SELECT id FROM patients) SELECT count(*) FROM x",
    "SELECT id FROM patients UNION SELECT id FROM appointments",
    "SELECT 1;",
]


@pytest.mark.parametrize("sql", REJECT_READ)
def test_read_rejects(sql):
    with pytest.raises(sqlcheck.SqlRejected):
        sqlcheck.check_read(sql)


@pytest.mark.parametrize("sql", ALLOW_READ)
def test_read_allows(sql):
    assert sqlcheck.check_read(sql).kind == "read"


def test_write_checks():
    assert sqlcheck.check_write("UPDATE appointments SET status='x' WHERE id=1").warnings == []
    assert sqlcheck.check_write("DELETE FROM appointments").warnings
    for bad in ["DROP TABLE x", "SELECT 1", "UPDATE a SET b=1; DROP TABLE x"]:
        with pytest.raises(sqlcheck.SqlRejected):
            sqlcheck.check_write(bad)


def test_checksums():
    assert medicare_valid("4332 18195 7")
    assert not medicare_valid("4332 18194 7")
    assert luhn_valid("8003 6000 1338 9082")
    assert not luhn_valid("8003 6000 1338 9083")


def test_masker_columns_and_detectors():
    m = Masker({"medicare_number": "last4", "dob": "year_only", "email": "redact"},
               ["ihi", "medicare", "email", "au_phone"])
    rows, counts = m.mask_rows(
        ["medicare_number", "alias", "note", "dob"],
        [("4332 18195 7", "4332 18195 7",
          "IHI 8003 6000 1338 9082, call 0435 833 765 or a@b.com. Ref 1234 56789 0", "2006-07-12")],
    )
    medicare_col, alias, note, dob = rows[0]
    assert medicare_col == "******1957"
    assert alias == "[MEDICARE]"
    assert "[IHI]" in note and "[PHONE]" in note and "[EMAIL]" in note
    assert "1234 56789 0" in note
    assert dob == "2006"
    assert counts["medicare"] == 1


def test_tokens_roundtrip_expiry_and_tamper():
    key = b"k" * 32
    t = tokens.mint(key, "bot", "eshan", 60)
    assert tokens.verify(key, t)["agent"] == "bot"
    with pytest.raises(tokens.TokenError, match="expired"):
        tokens.verify(key, t, now=time.time() + 61)
    with pytest.raises(tokens.TokenError, match="signature"):
        tokens.verify(b"x" * 32, t)
    prefix, body, sig = t.split(".")
    forged = tokens._b64(json.dumps({"agent": "admin", "exp": 9e12}).encode())
    with pytest.raises(tokens.TokenError, match="signature"):
        tokens.verify(key, f"{prefix}.{forged}.{sig}")
    with pytest.raises(tokens.TokenError):
        tokens.mint(key, "bot", "eshan", 9 * 3600)


def _ledger(tmp_path: Path) -> Ledger:
    led = Ledger(tmp_path / "ledger.jsonl", b"k" * 32)
    for i in range(5):
        led.append({"event": "read", "agent": "bot", "rows": i})
    return led


def test_ledger_verifies(tmp_path):
    assert _ledger(tmp_path).verify()[0]


def test_ledger_detects_edit(tmp_path):
    led = _ledger(tmp_path)
    lines = led.path.read_text().splitlines()
    entry = json.loads(lines[2]); entry["rows"] = 999
    lines[2] = json.dumps(entry, sort_keys=True)
    led.path.write_text("\n".join(lines) + "\n")
    ok, n, msg = led.verify()
    assert not ok and n == 3 and "modified" in msg


def test_ledger_detects_deletion(tmp_path):
    led = _ledger(tmp_path)
    lines = led.path.read_text().splitlines()
    del lines[1]
    led.path.write_text("\n".join(lines) + "\n")
    ok, n, msg = led.verify()
    assert not ok and "chain broken" in msg


def test_ledger_detects_forged_rehash(tmp_path):
    import hashlib
    from lokra.ledger import _canonical
    led = _ledger(tmp_path)
    lines = led.path.read_text().splitlines()
    entry = json.loads(lines[-1]); entry["rows"] = 0
    body = {k: v for k, v in entry.items() if k not in ("hash", "sig")}
    entry["hash"] = hashlib.sha256(_canonical(body)).hexdigest()
    lines[-1] = json.dumps(entry, sort_keys=True)
    led.path.write_text("\n".join(lines) + "\n")
    ok, _, msg = led.verify()
    assert not ok and "signature" in msg
