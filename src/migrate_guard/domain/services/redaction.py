"""Hide secrets before anything reaches a report or a log (SPEC §7)."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from migrate_guard.domain.entities.case import Headers
from migrate_guard.domain.entities.difference import MISSING, Difference, Side
from migrate_guard.domain.value_objects.json_value import JsonValue

REDACTED = "[REDACTED]"

SENSITIVE_HEADERS = frozenset({"authorization", "cookie", "set-cookie", "x-api-key"})
DEFAULT_FIELD_PATTERNS = ("password", "passwd", "token", "secret", "api_key", "apikey")

# 13 to 19 digits, optionally grouped by spaces or dashes. Luhn-checked to limit false positives.
_CARD_CANDIDATE = re.compile(r"(?<!\d)\d(?:[ -]?\d){12,18}(?!\d)")


@dataclass(frozen=True)
class RedactionPolicy:
    """Field names are matched case-insensitively, as substrings: `token` hides `access_token`."""

    field_patterns: tuple[str, ...] = DEFAULT_FIELD_PATTERNS

    def is_sensitive_field(self, name: str) -> bool:
        lowered = name.lower()
        return any(p.lower() in lowered for p in self.field_patterns)


def redact_headers(headers: Headers, policy: RedactionPolicy) -> Headers:
    return tuple(
        (name, REDACTED)
        if name.lower() in SENSITIVE_HEADERS or policy.is_sensitive_field(name)
        else (name, value)
        for name, value in headers
    )


def redact_value(value: JsonValue, policy: RedactionPolicy) -> JsonValue:
    if isinstance(value, dict):
        return {
            k: REDACTED if policy.is_sensitive_field(k) else redact_value(v, policy)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact_value(v, policy) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, int) and not isinstance(value, bool) and _is_card(str(value)):
        return REDACTED
    return value


def redact_text(text: str) -> str:
    return _CARD_CANDIDATE.sub(lambda m: REDACTED if _is_card(m.group()) else m.group(), text)


def redact_difference(diff: Difference, policy: RedactionPolicy) -> Difference:
    # Under a sensitive field everything goes, the detail too: it can quote the values.
    if any(policy.is_sensitive_field(token) for token in diff.location.tokens):
        return replace(
            diff,
            baseline=_hide(diff.baseline),
            candidate=_hide(diff.candidate),
            detail=REDACTED if diff.detail else "",
        )
    return replace(
        diff,
        baseline=_redact_side(diff.baseline, policy),
        candidate=_redact_side(diff.candidate, policy),
        detail=redact_text(diff.detail),
    )


def _hide(side: Side) -> Side:
    return side if side is MISSING else REDACTED


def _redact_side(side: Side, policy: RedactionPolicy) -> Side:
    return side if side is MISSING else redact_value(side, policy)


def _is_card(candidate: str) -> bool:
    digits = [int(c) for c in candidate if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0
