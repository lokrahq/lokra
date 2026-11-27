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


def card_valid(raw: str) -> bool:
    d = _digits(raw)
    if not 13 <= len(d) <= 19 or not luhn_valid(d):
        return False
    return (d[0] == "4" or d[:2] in {"30", "34", "35", "36", "37", "38", "62", "65"} or d[:4] == "6011"
            or 51 <= int(d[:2]) <= 55 or 2221 <= int(d[:4]) <= 2720 or 644 <= int(d[:3]) <= 649)


def iban_valid(raw: str) -> bool:
    s = raw.replace(" ", "").upper()
    if not 15 <= len(s) <= 34:
        return False
    rearranged = s[4:] + s[:4]
    return int("".join(str(int(ch, 36)) for ch in rearranged)) % 97 == 1


def phone_valid(raw: str) -> bool:
    return 8 <= len(_digits(raw)) <= 15


# Order matters: more specific patterns run first, so an IHI is never read as a card.
DETECTORS: dict[str, tuple[re.Pattern, Any]] = {
    "ihi": (re.compile(r"\b8003[ -]?60\d{2}[ -]?\d{4}[ -]?\d{4}\b"), luhn_valid),
    "medicare": (re.compile(r"\b[2-6]\d{3}[ -]?\d{5}[ -]?\d(?:[ -]?\d)?\b"), medicare_valid),
    "card": (re.compile(r"(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])"), card_valid),
    "iban": (re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b"), iban_valid),
    "us_ssn": (re.compile(r"(?<!\d)(?!000|666|9\d\d)\d{3}([- ])(?!00)\d{2}\1(?!0000)\d{4}(?!\d)"), None),
    "email": (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), None),
    "au_phone": (re.compile(r"(?<!\d)(?:\+?61[ -]?|0)4\d{2}[ -]?\d{3}[ -]?\d{3}(?!\d)"
                            r"|(?<!\d)(?:\+?61[ -]?|0)[2378][ -]?\d{4}[ -]?\d{4}(?!\d)"), None),
    "phone": (re.compile(r"(?<![\w+])\+[1-9]\d{0,2}[ .-]?(?:\(\d{1,4}\)[ .-]?)?\d{1,4}(?:[ .-]?\d{1,4}){1,4}(?!\d)"
                         r"|(?<![\d-])\(?[2-9]\d{2}\)?[ .-]\d{3}[ .-]\d{4}(?![\d-])"), phone_valid),
}
LABELS = {"ihi": "[IHI]", "medicare": "[MEDICARE]", "card": "[CARD]", "iban": "[IBAN]", "us_ssn": "[SSN]",
          "email": "[EMAIL]", "au_phone": "[PHONE]", "phone": "[PHONE]"}


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

    def _value(self, value: Any, rule: str | None, counts: Counter) -> Any:
        """Mask one value; JSON objects and arrays are walked, and their keys count as column names."""
        if rule and rule != "none" and value is not None:
            counts[f"column:{rule}"] += 1
            return _strategy(value, rule)
        if isinstance(value, str):
            return self.scrub_text(value, counts)
        if isinstance(value, dict):
            return {self.scrub_text(k, counts) if isinstance(k, str) else k:
                    self._value(v, self.column_rules.get(str(k).lower()), counts) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._value(v, None, counts) for v in value]
        return value

    def mask_rows(self, columns: list[str], rows: list[tuple]) -> tuple[list[list], dict[str, int]]:
        counts: Counter = Counter()
        rules = [self.column_rules.get(c.lower()) for c in columns]
        out = [[self._value(value, rule, counts) for value, rule in zip(row, rules)] for row in rows]
        return out, dict(counts)
