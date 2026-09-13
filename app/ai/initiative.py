"""
Initiative scoring (Cognitive Upgrade spec section 23, "Initiative").

Mochi may proactively communicate only when an initiative policy
permits it - never randomly interrupt the user. The spec's own formula:

    initiative score = importance + timeliness + user_benefit
                        (discounted by recent interruptions, focus
                        mode, and notification fatigue)

This module is that scoring function: given a read on how important,
timely, and useful a proactive message would be, plus how much Mochi
has already been interrupting lately, decide whether speaking up
unprompted is justified right now.

Deliberately NOT wired to any actual proactive channel yet - this is
the decision function a channel would call, not a channel itself.
Two candidate channels exist in the spec (section 23's own examples),
and they have very different risk profiles:

  - A due reminder (app/reminders/notifications.py's ReminderNotifier)
    is something the user explicitly asked for at creation time -
    silently suppressing it because of "notification fatigue" would
    break a reliability promise (spec section 27: never let a
    heuristic override correctness), so that notifier is intentionally
    left exactly as it was, always firing. Gating an already-reliable,
    user-requested notification behind a new fuzziness score would be
    a regression, not an improvement.
  - "An important calendar event is approaching" is a genuinely new
    proactive behavior that does not exist in Mochi at all yet -
    nothing currently polls the calendar for what's coming up and
    decides whether to say something about it. Building that (polling
    cadence, what counts as "important", whether it respects focus
    mode, how it's surfaced) is a product decision, not something to
    guess at silently while closing out a phase of an engineering spec.
    This module is the piece such a feature would call once that
    decision is made - see docs/ROADMAP_COGNITIVE_UPGRADE.md for the
    open item this leaves.
"""

from __future__ import annotations

from dataclasses import dataclass

# Below this combined score, staying quiet is the safer default ("do
# not randomly interrupt the user") - same conservative-by-default
# philosophy as app/ai/confidence.py's CONFIDENCE_ACT threshold. A
# borderline signal defaults to NOT speaking up, not to speaking up.
INITIATIVE_THRESHOLD = 0.6

# How much each recent interruption (in whatever window the caller
# considers "recent") knocks off the score - a handful of consecutive
# proactive messages should make Mochi noticeably more reluctant to
# send another one.
FATIGUE_PENALTY_PER_INTERRUPTION = 0.15

# Focus mode is a hard, large penalty rather than a soft one - "the
# user turned notifications down" should take real justification to
# override, not just a slightly-above-average importance score.
FOCUS_MODE_PENALTY = 0.4


@dataclass(frozen=True)
class InitiativeSignal:
    """Inputs to the initiative decision. importance/timeliness/
    user_benefit are each 0.0-1.0 so no one factor can dominate purely
    from being on a different scale than the others - the caller is
    responsible for that honest read of the situation; this module only
    combines and discounts it."""

    importance: float
    timeliness: float
    user_benefit: float
    recent_interruptions: int = 0
    focus_mode: bool = False


def score(signal: InitiativeSignal) -> float:
    """Combined initiative score, clamped to 0.0-1.0. Higher means more
    justified in speaking up unprompted."""
    raw = (signal.importance + signal.timeliness + signal.user_benefit) / 3
    raw -= signal.recent_interruptions * FATIGUE_PENALTY_PER_INTERRUPTION
    if signal.focus_mode:
        raw -= FOCUS_MODE_PENALTY
    return max(0.0, min(1.0, raw))


def should_initiate(signal: InitiativeSignal) -> bool:
    """True when Mochi is justified in proactively speaking up given
    this signal - False means stay quiet."""
    return score(signal) >= INITIATIVE_THRESHOLD
