"""The conformance suite must score Lokra 9/9, and must actually detect failures."""
import psycopg
import pytest

from lokra.config import LAB_ADMIN_DSN


def _db_up() -> bool:
    try:
        psycopg.connect(LAB_ADMIN_DSN, connect_timeout=2).close()
        return True
    except psycopg.Error:
        return False


pytestmark = pytest.mark.skipif(not _db_up(), reason="lab database not running")


def test_lokra_upholds_every_guarantee():
    from conformance.adapters import LokraAdapter
    from conformance.probes import PROBES
    from conformance.run import score
    results = score(LokraAdapter(), LAB_ADMIN_DSN)
    failed = [r["key"] for r in results if not r["passed"]]
    assert len(results) == len(PROBES)
    assert not failed, f"Lokra failed: {failed}"


def test_probes_detect_a_broken_layer():
    """Sanity: against a keyword-filter-over-superuser proxy, probes must fail."""
    from conformance.adapters import NaiveProxyAdapter
    from conformance.run import score
    results = score(NaiveProxyAdapter(LAB_ADMIN_DSN), LAB_ADMIN_DSN)
    assert any(not r["passed"] for r in results), "probes failed to detect a broken layer"
