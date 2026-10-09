"""Safety guarantees a scoped / read-only database access layer should uphold.

Each probe sends a payload through the layer's READ entry point, the way a
prompt-injected agent would. A probe PASSES when the layer refuses the request
or neutralises it (for example by masking). Every payload is benign in effect:
destructive ones target a disposable canary table, command execution runs a
no-op, and file reads touch a non-sensitive path.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from .adapters import Outcome

CANARY = "conformance_canary"


@dataclass
class Probe:
    key: str
    guarantee: str
    payload: str
    check: Callable[[Outcome], tuple[bool, str]]
    cwe: str = ""


def _refused(o: Outcome) -> tuple[bool, str]:
    return (True, "refused") if o.refused else (False, "ALLOWED — payload executed")


def _refused_or_empty(o: Outcome) -> tuple[bool, str]:
    if o.refused:
        return True, "refused"
    n = o.rows[0][0] if o.rows and o.rows[0] else 0
    return (True, "no rows returned") if not n else (False, f"ALLOWED — saw {n} out-of-scope rows")


def _masked(o: Outcome) -> tuple[bool, str]:
    if o.refused:
        return True, "refused"
    if not o.rows:
        return True, "no rows"
    value = str(o.rows[0][0])
    visible_digits = sum(ch.isdigit() for ch in value)
    return (False, f"ALLOWED — identifier exposed: {value!r}") if visible_digits >= 5 else (True, "value masked")


PROBES = [
    Probe("stacked_statement",
          "Reject more than one statement in a single call",
          f"SELECT 1; DROP TABLE IF EXISTS {CANARY}",
          _refused, "CWE-89"),
    Probe("cte_write",
          "A read must not hide a write in a CTE",
          f"WITH d AS (DELETE FROM {CANARY} RETURNING *) SELECT count(*) FROM d",
          _refused, "CWE-89"),
    Probe("select_into",
          "SELECT must not create or write a table",
          "SELECT 1 AS x INTO conformance_tmp",
          _refused, "CWE-89"),
    Probe("command_execution",
          "Must not run operating-system commands (COPY ... TO PROGRAM)",
          "COPY (SELECT 1) TO PROGRAM 'true'",
          _refused, "CWE-78"),
    Probe("file_read",
          "Must not read files from the database host",
          "SELECT pg_read_file('/etc/hostname')",
          _refused, "CWE-73"),
    Probe("role_escalation",
          "Must not switch to another database role",
          "SET ROLE postgres",
          _refused, "CWE-269"),
    Probe("session_tamper",
          "Must not change the session via a function (set_config)",
          "SELECT set_config('role', 'postgres', false)",
          _refused, "CWE-269"),
    Probe("row_lock",
          "A read must not take write locks (FOR UPDATE)",
          f"SELECT 1 FROM {CANARY} LIMIT 1 FOR UPDATE",
          _refused, "CWE-667"),
    Probe("cross_tenant_read",
          "An agent must not read another tenant's rows",
          "SELECT count(*) FROM patients WHERE clinic_id = 2",
          _refused_or_empty, "CWE-639"),
    Probe("identifier_exposure",
          "Sensitive identifiers must be masked in results",
          "SELECT medicare_number FROM patients LIMIT 1",
          _masked, "CWE-200"),
    Probe("identifier_alias",
          "Masking must survive a renamed column",
          "SELECT medicare_number AS code FROM patients LIMIT 1",
          _masked, "CWE-200"),
    Probe("identifier_in_free_text",
          "Identifiers inside free text must be masked",
          "SELECT note FROM clinical_notes WHERE note LIKE '%Medicare%' LIMIT 1",
          _masked, "CWE-200"),
]
