"""Match every required concept against its explicit equivalent expressions."""
from __future__ import annotations

from faithfulness import term_covered


def keyword_matches(answer: str, item: dict) -> list[dict]:
    required = item.get("must_contain", [])
    aliases = item.get("keyword_aliases", {})
    if not isinstance(required, list) or not isinstance(aliases, dict):
        raise ValueError(f"{item['id']}: keywords must be a list and aliases must be a mapping")
    if any(not isinstance(term, str) or not term.strip() for term in required):
        raise ValueError(f"{item['id']}: required keywords must be nonempty strings")
    if len(set(required)) != len(required):
        raise ValueError(f"{item['id']}: required keywords must be unique")
    if set(aliases) - set(required):
        raise ValueError(f"{item['id']}: aliases must refer to required keywords")
    matches = []
    for term in required:
        alternatives = aliases.get(term, [])
        if not isinstance(alternatives, list) or any(
            not isinstance(alias, str) or not alias.strip() for alias in alternatives
        ):
            raise ValueError(f"{item['id']}: aliases must be nonempty strings")
        candidates = [term, *alternatives]
        matched = [candidate for candidate in candidates if term_covered(answer, candidate)]
        matches.append({"required": term, "alternatives": alternatives, "matched": matched, "ok": bool(matched)})
    return matches


def keywords_ok(matches: list[dict]) -> bool | None:
    return all(match["ok"] for match in matches) if matches else None
