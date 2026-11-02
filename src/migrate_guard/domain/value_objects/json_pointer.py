"""JSON Pointer (RFC 6901): the location of a value inside a JSON document."""

from __future__ import annotations

from dataclasses import dataclass

WILDCARD = "*"


@dataclass(frozen=True)
class JsonPointer:
    """Stored as unescaped tokens; `str()` gives the escaped form, e.g. `/items/0/price`."""

    tokens: tuple[str, ...] = ()

    @classmethod
    def parse(cls, text: str) -> JsonPointer:
        if text == "":
            return cls()
        if not text.startswith("/"):
            raise ValueError(f"JSON Pointer must be empty or start with '/': {text!r}")
        # Order matters: ~1 first, so "~01" becomes "~1" and not "/".
        return cls(tuple(t.replace("~1", "/").replace("~0", "~") for t in text[1:].split("/")))

    def child(self, token: str | int) -> JsonPointer:
        return JsonPointer((*self.tokens, str(token)))

    def matches(self, pattern: JsonPointer) -> bool:
        """True if this pointer fits the pattern; a `*` token in the pattern matches any token."""
        return len(self.tokens) == len(pattern.tokens) and all(
            p in (WILDCARD, t) for t, p in zip(self.tokens, pattern.tokens, strict=True)
        )

    def __str__(self) -> str:
        return "".join("/" + t.replace("~", "~0").replace("/", "~1") for t in self.tokens)
