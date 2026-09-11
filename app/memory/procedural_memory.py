"""
Procedural memory (Cognitive Upgrade spec section 7 "Procedural Memory" /
section 24 "Learning From Failure") - behavioral rules and lessons
learned from real tool failures. Spec section 24's own worked example is
exactly the case this implements: a Google Calendar auth failure should
be generalized into "calendar authentication expired; future calendar
operations should check authentication state" - not just reported once
and forgotten.

Scope (deliberate - see app/ai/chat_engine._FAILURE_LESSONS): only a
small, fixed set of KNOWN failure types ever produce a rule here. Most
MochiError subclasses (a bad title, a malformed date) are one-off input
problems with no reusable lesson - turning every failure into a "rule"
would produce noise, not procedural knowledge. This mirrors
app/ai/fact_extraction.py's own philosophy: a missed lesson is safe
(nothing happens), a wrong or over-general one stored with confidence is
not, so this stays narrow on purpose.

A rule identified by (rule text, scope) is deduplicated rather than
inserted again on every repeat failure - `learn_rule` bumps
`trigger_count`/`last_triggered_at` on the existing matching row
instead, so ten identical calendar-auth failures produce one rule Mochi
has "noticed ten times", not ten separate rows. `has_recurring_issue`
uses that count to distinguish a one-off blip from something worth
actually mentioning to the user (see app/ai/chat_engine._failure_reaction,
which appends a "this has come up before" hint using the rule's own
wording once a scope has failed the same way more than once).

Gated by settings.memory_enabled - the same flag semantic/episodic
memory use.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from app.core.config import settings
from app.core.exceptions import MemoryDisabled
from app.core.logger import get_logger
from app.memory.database import get_connection, initialize_schema

logger = get_logger("mochi.memory.procedural")

ISO_FORMAT = "%Y-%m-%d %H:%M:%S"

SOURCE_FAILURE = "failure"
SOURCE_STATED = "stated"


@dataclass
class Rule:
    id: int
    rule: str
    scope: str
    confidence: float
    source: str
    trigger_count: int
    created_at: str
    updated_at: str
    last_triggered_at: str

    @classmethod
    def from_row(cls, row) -> "Rule":
        return cls(
            id=row["id"],
            rule=row["rule"],
            scope=row["scope"],
            confidence=row["confidence"],
            source=row["source"],
            trigger_count=row["trigger_count"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_triggered_at=row["last_triggered_at"],
        )


def _require_enabled() -> None:
    if not settings.memory_enabled:
        raise MemoryDisabled(
            "Procedural memory is turned off. Set MOCHI_MEMORY_ENABLED=true "
            "in .env to enable it."
        )


def ensure_ready() -> None:
    initialize_schema()


def learn_rule(
    rule: str, scope: str, confidence: float = 0.7, source: str = SOURCE_FAILURE
) -> Optional[Rule]:
    """Store a lesson, or bump an existing identical one's trigger count
    (see module docstring on deduplication). Best-effort and NEVER
    raises - every real call site (app/ai/chat_engine._learn_from_failure)
    is a side effect alongside a failure that's already being reported
    to the user, and a logging problem here must never compound that
    into a second, unrelated failure. Returns None when memory is
    disabled or the write itself fails for any reason."""
    if not settings.memory_enabled:
        return None
    try:
        ensure_ready()
        now = datetime.now().strftime(ISO_FORMAT)
        with get_connection() as conn:
            existing = conn.execute(
                "SELECT id, trigger_count FROM procedural_rules WHERE rule = ? AND scope = ?;",
                (rule, scope),
            ).fetchone()
            if existing is not None:
                row_id = existing["id"]
                conn.execute(
                    """
                    UPDATE procedural_rules
                    SET trigger_count = ?, last_triggered_at = ?, updated_at = ?
                    WHERE id = ?;
                    """,
                    (existing["trigger_count"] + 1, now, now, row_id),
                )
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO procedural_rules
                        (rule, scope, confidence, source, trigger_count, created_at, updated_at, last_triggered_at)
                    VALUES (?, ?, ?, ?, 1, ?, ?, ?);
                    """,
                    (rule, scope, confidence, source, now, now, now),
                )
                row_id = cursor.lastrowid
            row = conn.execute(
                "SELECT * FROM procedural_rules WHERE id = ?;", (row_id,)
            ).fetchone()
        return Rule.from_row(row)
    except Exception as exc:  # noqa: BLE001 - see docstring above: never raise from here
        logger.debug("Procedural lesson not recorded: %s", exc)
        return None


def relevant_rules(scope: str, limit: int = 5) -> list[Rule]:
    """Rules for `scope`, most frequently triggered first. Raises
    MemoryDisabled if memory is off - a caller explicitly asking to READ
    should know why it came back empty, same as
    semantic_memory.list_facts / episodic_memory.recent_events."""
    _require_enabled()
    ensure_ready()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM procedural_rules WHERE scope = ? ORDER BY trigger_count DESC LIMIT ?;",
            (scope, limit),
        ).fetchall()
    return [Rule.from_row(r) for r in rows]


def has_recurring_issue(scope: str, min_triggers: int = 2) -> Optional[Rule]:
    """The single most-triggered rule for `scope` if it's recurred at
    least `min_triggers` times, else None - used to decide whether a
    fresh failure is worth mentioning as "this has come up before"
    rather than reporting it as a first-time surprise. Best-effort like
    learn_rule - typically called from inside an error-handling path
    that must not itself fail, so this never raises, unlike
    relevant_rules above."""
    if not settings.memory_enabled:
        return None
    try:
        rules = relevant_rules(scope, limit=1)
    except Exception as exc:  # noqa: BLE001 - see docstring above
        logger.debug("Could not check for a recurring issue: %s", exc)
        return None
    if rules and rules[0].trigger_count >= min_triggers:
        return rules[0]
    return None
