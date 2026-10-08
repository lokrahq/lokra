from __future__ import annotations

import hashlib
import re
from collections import Counter
from datetime import date, datetime
from typing import Any


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def medicare_valid(raw: str) -> bool:
    d = _digits(raw)
    if len(d) not in (10, 11) or d[0] not in "23456":
        return False
    weights = [1, 3, 7, 9, 1, 3, 7, 9]
    return sum(int(a) * b for a, b in zip(d[:8], weights)) % 10 == int(d[8])


def luhn_valid(raw: str) -> bool:
    d = _digits(raw)
    total = 0
    for i, ch in enumerate(reversed(d)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


DETECTORS: dict[str, tuple[re.Pattern, Any]] = {
    # ihi must run before medicare
    "ihi": (re.compile(r"\b8003[ -]?60\d{2}[ -]?\d{4}[ -]?\d{4}\b"), luhn_valid),
    "medicare": (re.compile(r"\b[2-6]\d{3}[ -]?\d{5}[ -]?\d(?:[ -]?\d)?\b"), medicare_valid),
    "email": (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), None),
    "au_phone": (re.compile(r"(?<!\d)(?:\+?61[ -]?|0)4\d{2}[ -]?\d{3}[ -]?\d{3}(?!\d)"
                            r"|(?<!\d)(?:\+?61[ -]?|0)[2378][ -]?\d{4}[ -]?\d{4}(?!\d)"), None),
}
LABELS = {"ihi": "[IHI]", "medicare": "[MEDICARE]", "email": "[EMAIL]", "au_phone": "[PHONE]"}


def _strategy(value: Any, how: str) -> Any:
    if value is None or how == "none":
        return value
    if how == "year_only":
        if isinstance(value, (date, datetime)):
            return str(value.year)
        return str(value)[:4]
    s = str(value)
    if how == "redact":
        return "[REDACTED]"
    if how.startswith("last"):
        keep = int(how[4:] or 4)
        d = _digits(s) or s
        return "*" * max(len(d) - keep, 0) + d[-keep:]
    if how == "hash":
        return "h:" + hashlib.sha256(s.encode()).hexdigest()[:12]
    return "[REDACTED]"


class Masker:
    def __init__(self, column_rules: dict[str, str], detectors: list[str]):
        self.column_rules = {k.lower(): v for k, v in column_rules.items()}
        unknown = set(detectors) - set(DETECTORS)
        if unknown:
            raise ValueError(f"unknown detectors: {sorted(unknown)}")
        self.detectors = [d for d in DETECTORS if d in detectors]

    def scrub_text(self, text: str, counts: Counter | None = None) -> str:
        for name in self.detectors:
            pattern, validator = DETECTORS[name]

            def repl(m: re.Match, name=name, validator=validator) -> str:
                if validator and not validator(m.group(0)):
                    return m.group(0)
                if counts is not None:
                    counts[name] += 1
                return LABELS[name]

            text = pattern.sub(repl, text)
        return text

    def mask_rows(self, columns: list[str], rows: list[tuple]) -> tuple[list[list], dict[str, int]]:
        counts: Counter = Counter()
        rules = [self.column_rules.get(c.lower()) for c in columns]
        out = []
        for row in rows:
            new = []
            for value, rule in zip(row, rules):
                if rule and rule != "none" and value is not None:
                    counts[f"column:{rule}"] += 1
                    value = _strategy(value, rule)
                elif isinstance(value, str):
                    value = self.scrub_text(value, counts)
                new.append(value)
            out.append(new)
        return out, dict(counts)
