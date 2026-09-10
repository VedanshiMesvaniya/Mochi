"""
Deterministic passive fact extraction (Cognitive Upgrade spec sections
5/7/8/9) - looks for a small, fixed set of "the user is telling me
something about themselves" sentence patterns in an ORDINARY chat
message (not an explicit "remember that..." command - see
app/ai/intent.py's REMEMBER_TRIGGER for that separate, higher-confidence
path) and turns a match into a FactCandidate
app/memory/semantic_memory.remember_fact() can store.

Deliberately NOT an LLM call - every creation trigger elsewhere in
Mochi already prefers deterministic pattern matching over asking a
model to decide (spec section 60's central rule: "can a normal function
do it?"), and doing this via the LLM would mean either a synchronous
model call on every single chat message, or a background consolidation
job this phase doesn't yet have the infrastructure for (see
docs/ROADMAP_COGNITIVE_UPGRADE.md's "Deferred" section).

Because it's pattern-based, this only catches fairly direct, common
phrasings - "I finally finished the API and my back hurts" won't be
recognized as anything (spec section 5's own example of a message that
should NOT automatically create a task/fact). That's a deliberate,
documented trade-off: a missed inference is safe (nothing happens), a
WRONG inference stored as a confident fact is not - so this stays
narrow rather than guessing at meaning.

Every match returns a `confidence` reflecting how hedged the statement
itself was (spec section 8's exact example: "I might switch to Linux"
must be stored as a lower-confidence, hedged fact - "User is
considering switching to Linux" - never "User uses Linux") rather than
a fixed constant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# Small, fixed keyword -> category lookup used only so "I switched to
# X" / "I use X" resolves to the SAME subject as an earlier "I use Y"
# about the same category (spec section 9's Windows -> Linux
# contradiction example) - not a general semantic classifier, just
# enough to cover categories people actually correct Mochi about in
# practice. Extend this dict, not the regex list below, to cover more.
_CATEGORY_KEYWORDS: dict[str, str] = {
    "windows": "operating_system",
    "linux": "operating_system",
    "macos": "operating_system",
    "mac os": "operating_system",
    "ubuntu": "operating_system",
    "fedora": "operating_system",
    "vscode": "editor",
    "vs code": "editor",
    "vim": "editor",
    "neovim": "editor",
    "pycharm": "editor",
    "sublime": "editor",
    "intellij": "editor",
    "emacs": "editor",
    "python": "primary_language",
    "javascript": "primary_language",
    "typescript": "primary_language",
    "rust": "primary_language",
    "golang": "primary_language",
    "java": "primary_language",
    "c++": "primary_language",
    "c#": "primary_language",
}


@dataclass
class FactCandidate:
    text: str
    subject: Optional[str]
    confidence: float
    source: str = "inferred"


def _category_for(phrase: str) -> Optional[str]:
    lowered = phrase.lower()
    for keyword, category in _CATEGORY_KEYWORDS.items():
        if keyword in lowered:
            return category
    return None


def _clean(phrase: str) -> str:
    return phrase.strip(" .,!?")


def _gerund(phrase: str) -> str:
    """"switch to Linux" -> "switching to Linux" - just enough naive
    verb-to-gerund handling to reproduce spec section 8's exact phrasing
    example; not a general grammar engine."""
    words = phrase.split(" ", 1)
    verb, rest = words[0], (f" {words[1]}" if len(words) > 1 else "")
    if verb.lower() == "be":
        return f"being{rest}"
    if verb.endswith("ing"):
        return f"{verb}{rest}"
    if verb.endswith("e") and not verb.endswith("ee"):
        verb = verb[:-1]
    return f"{verb}ing{rest}"


# Ordered: patterns are tried top-to-bottom and the first match wins, so
# more specific/safety-relevant ones (allergies) come before broader,
# looser ones (generic "I like ...") that could otherwise match first on
# an overlapping phrase.
_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bi(?:'m| am) allergic to (.+)", re.IGNORECASE), "allergic_to"),
    (re.compile(r"\bi(?:'m| am) vegetarian\b", re.IGNORECASE), "diet"),
    (re.compile(r"\bi(?:'m| am) vegan\b", re.IGNORECASE), "diet"),
    (re.compile(r"\bi live in (.+)", re.IGNORECASE), "lives_in"),
    (re.compile(r"\bi(?:'ve| have) moved to (.+)", re.IGNORECASE), "lives_in"),
    (re.compile(r"\bi work at (.+)", re.IGNORECASE), "works_at"),
    (re.compile(r"\bi work as (?:an?|the) ?(.+)", re.IGNORECASE), "job_role"),
    (re.compile(r"\bmy favorite (\w+) is (.+)", re.IGNORECASE), "favorite"),
    (re.compile(r"\bi(?:'m| am) thinking about (.+)", re.IGNORECASE), "considering"),
    (re.compile(r"\bi might (.+)", re.IGNORECASE), "considering"),
    (re.compile(r"\bi may (.+)", re.IGNORECASE), "considering"),
    (re.compile(r"\bi switched to (.+)", re.IGNORECASE), "switched_to"),
    (re.compile(r"\bi(?:'m| am) using (.+)", re.IGNORECASE), "uses"),
    (re.compile(r"\bi use (.+)", re.IGNORECASE), "uses"),
    (re.compile(r"\bi don'?t like (.+)", re.IGNORECASE), "dislikes"),
    (re.compile(r"\bi hate (.+)", re.IGNORECASE), "dislikes"),
    (re.compile(r"\bi prefer (.+)", re.IGNORECASE), "preference"),
    (re.compile(r"\bi like (.+)", re.IGNORECASE), "likes"),
]

_MAX_PHRASE_WORDS = 8  # a long tail is a sign the pattern grabbed a whole clause, not a thing


def extract(text: str) -> Optional[FactCandidate]:
    """Returns the first pattern match as a FactCandidate, or None if
    `text` doesn't look like a self-disclosure at all - the
    overwhelmingly common case for an ordinary chat message."""
    for pattern, kind in _PATTERNS:
        match = pattern.search(text)
        if match:
            candidate = _build_candidate(kind, match, text)
            if candidate is not None:
                return candidate
    return None


def _build_candidate(kind: str, match: "re.Match[str]", original: str) -> Optional[FactCandidate]:
    groups = match.groups()

    if kind == "allergic_to":
        thing = _clean(groups[0])
        if not thing:
            return None
        return FactCandidate(
            text=f"User is allergic to {thing}.",
            subject=f"allergic_to:{thing.lower()}",
            confidence=0.9,
        )

    if kind == "diet":
        diet = "vegetarian" if "vegetarian" in original.lower() else "vegan"
        return FactCandidate(text=f"User is {diet}.", subject="diet", confidence=0.9)

    if kind == "lives_in":
        place = _clean(groups[0])
        if not place or len(place.split()) > _MAX_PHRASE_WORDS:
            return None
        return FactCandidate(text=f"User lives in {place}.", subject="lives_in", confidence=0.85)

    if kind == "works_at":
        place = _clean(groups[0])
        if not place or len(place.split()) > _MAX_PHRASE_WORDS:
            return None
        return FactCandidate(text=f"User works at {place}.", subject="works_at", confidence=0.85)

    if kind == "job_role":
        role = _clean(groups[0])
        if not role or len(role.split()) > 6:
            # A long tail ("...as hard as I can to finish this") is a
            # figure of speech, not a job title - bail rather than
            # store nonsense.
            return None
        return FactCandidate(text=f"User works as {role}.", subject="job_role", confidence=0.75)

    if kind == "favorite":
        category, value = _clean(groups[0]), _clean(groups[1])
        if not category or not value:
            return None
        return FactCandidate(
            text=f"User's favorite {category} is {value}.",
            subject=f"favorite:{category.lower()}",
            confidence=0.85,
        )

    if kind == "considering":
        activity = _clean(groups[0])
        if not activity or len(activity.split()) > _MAX_PHRASE_WORDS:
            return None
        # Spec section 8's exact example: "I might switch to Linux" is
        # stored hedged, low-confidence - never as a confident
        # current-state fact.
        return FactCandidate(
            text=f"User is considering {_gerund(activity)}.",
            subject=None,
            confidence=0.4,
        )

    if kind in ("switched_to", "uses"):
        thing = _clean(groups[0])
        if not thing or len(thing.split()) > _MAX_PHRASE_WORDS:
            return None
        category = _category_for(thing)
        verb = "switched to" if kind == "switched_to" else "uses"
        return FactCandidate(
            text=f"User {verb} {thing}.",
            subject=f"uses:{category}" if category else None,
            confidence=0.8 if category else 0.55,
        )

    if kind == "dislikes":
        thing = _clean(groups[0])
        if not thing or len(thing.split()) > _MAX_PHRASE_WORDS:
            return None
        return FactCandidate(
            text=f"User dislikes {thing}.",
            subject=f"dislikes:{thing.lower()}",
            confidence=0.75,
        )

    if kind == "preference":
        thing = _clean(groups[0])
        if not thing or len(thing.split()) > _MAX_PHRASE_WORDS:
            return None
        return FactCandidate(
            text=f"User prefers {thing}.",
            subject=f"preference:{thing.lower()}",
            confidence=0.75,
        )

    if kind == "likes":
        thing = _clean(groups[0])
        if not thing or len(thing.split()) > _MAX_PHRASE_WORDS:
            return None
        return FactCandidate(
            text=f"User likes {thing}.",
            subject=f"likes:{thing.lower()}",
            confidence=0.7,
        )

    return None
