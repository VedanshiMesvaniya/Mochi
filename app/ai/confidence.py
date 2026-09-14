"""
General confidence system (Cognitive Upgrade spec section 13,
"Confidence System" / MOCHI_VERSIONED_ROADMAP.md section 6, "Confidence
controls autonomy").

The spec's rule:

    HIGH confidence   -> act or answer
    MEDIUM confidence -> ask a clarifying question, never guess
    LOW confidence    -> stay uncertain (treat as if nothing was found)

Before this module, that exact three-way rule already existed, but only
inline inside app/ai/chat_engine.py's semantic-intent handling, as two
bare threshold constants (app/ai/semantic_intent.CONFIDENCE_LOW/
CONFIDENCE_ACT) compared by hand in an if/elif chain. This module pulls
the THRESHOLDS and the resulting BAND out into one small, named,
independently-testable piece - the single source of truth for "what do
these two numbers mean" - so any future decision point that produces a
0.0-1.0 confidence score (not just semantic intent classification) can
reuse the exact same rule instead of re-deriving its own thresholds.

Deliberately narrow: this is the classification rule itself, not a
framework that goes and finds a confidence score for every kind of
decision Mochi makes. Most of Mochi's decisions are already fully
deterministic (a keyword trigger either matched or it didn't) and have
no numeric confidence to band in the first place - spec section 60's
own rule ("can a normal function do it? -> tool, not a model guess")
means most decisions never reach this module at all, by design. Today's
one real caller is app/ai/semantic_intent.py's model-produced score;
app/ai/chat_engine.py imports `band()` directly for that call site so
the act/ask/ignore choice reads as an explicit named rule rather than
inline `>=` comparisons.

Semantic memory's fact-confidence numbers (app/memory/semantic_memory.py)
are deliberately NOT run through this module - a stored fact isn't an
action to gate (act/ask/ignore doesn't apply to "remembering something
in a hedged way"), it already has its own, differently-shaped handling
(hedged wording for an uncertain statement, ranking for retrieval - see
that module's docstring and docs/ROADMAP_COGNITIVE_UPGRADE.md section 8-9).
"""

from __future__ import annotations

from enum import Enum

# Same bands as MOCHI_VERSIONED_ROADMAP.md section 6: 0-50% observe
# only, 50-75% soft suggestion/ask, 75%+ act. There is no 90%+ "fully
# automatic, no safety net" tier here - every HIGH-confidence action
# still goes through the exact same tool validation a deterministic
# keyword match would (ToolValidationError etc.), so a wrong guess can
# still be rejected safely even at the top band.
CONFIDENCE_LOW = 0.50   # below this: treat as if nothing was found
CONFIDENCE_ACT = 0.75   # at/above this: safe to act or answer directly


class Confidence(Enum):
    """The three-way rule spec section 13 describes, named so a caller
    can branch on `Confidence.HIGH`/`MEDIUM`/`LOW` instead of repeating
    raw threshold comparisons."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


def band(score: float) -> Confidence:
    """Classify a 0.0-1.0 confidence score into HIGH/MEDIUM/LOW using
    Mochi's one shared set of thresholds (CONFIDENCE_LOW/CONFIDENCE_ACT
    above). Out-of-range input is clamped rather than raising - a
    caller passing a slightly-off score (e.g. a model that returned
    1.2) should degrade to the nearest valid band, not crash chat."""
    score = max(0.0, min(1.0, score))
    if score >= CONFIDENCE_ACT:
        return Confidence.HIGH
    if score >= CONFIDENCE_LOW:
        return Confidence.MEDIUM
    return Confidence.LOW
