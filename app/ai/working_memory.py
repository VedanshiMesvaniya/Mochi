"""
Working memory (Cognitive Upgrade spec section 7, "Working Memory") - a
single named container for everything app/ai/chat_engine.handle_message()
carries from one message to the next about the CURRENT, short-lived
interaction:

* `pending_action` - an unconfirmed proposal awaiting a yes/no (calendar/
  Google Tasks writes - see app/ai/chat_engine.py's own docstring on it
  and `_resolve_pending_action`).
* `reference` - a reference-resolution hint for "it"/"that"/"the second
  one" (app/ai/conversation_state.py).
* `goal` - an unanswered clarifying question awaiting a slot value, e.g.
  "but when?" (Cognitive Upgrade phase 1's active-goal state,
  app/ai/goal_state.py).

The spec describes working memory as a bundle of several pieces
(current conversation, active goal, unresolved questions, recent tool
results, current state) rather than a single algorithm - this module IS
that bundle: a `WorkingMemory` bundled straight from a `ChatReaction` and
unpacked back into `handle_message`'s next call, so there's one place to
look for "what does Mochi currently remember about this exact
conversation, right now", while the actual reference-resolution and
goal-completion LOGIC stays exactly where it already lived and was
already tested (app/ai/conversation_state.py, app/ai/goal_state.py)
rather than being rewritten wholesale just to live under one import.

Deliberately NOT a replacement for `ChatReaction`'s own
`pending_action`/`conversation_state`/`active_goal` fields, or for
app/ui/chat_window.py's `_pending_action`/`_conversation_state`/
`_active_goal` attributes - renaming those would touch every test file
that already asserts against them directly, for zero behavior change.
This module is a convenience view over the same three fields, used by
app/ui/chat_window.py to build the next `handle_message()` call's
keyword arguments and to unpack a `ChatReaction` in one step, without
duplicating the "these three things travel together" idea in three
separate places.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids a real import cycle
    from app.ai.chat_engine import ChatReaction


@dataclass(frozen=True)
class WorkingMemory:
    """One turn's worth of short-lived conversational state - see module
    docstring for what each field means and where its logic actually
    lives."""

    pending_action: Optional[dict] = None
    reference: Optional[dict] = None
    goal: Optional[dict] = None

    @classmethod
    def from_reaction(cls, reaction: "ChatReaction") -> "WorkingMemory":
        """Bundle the three fields off a ChatReaction that
        handle_message() just returned, ready to store and thread into
        the next call."""
        return cls(
            pending_action=reaction.pending_action,
            reference=reaction.conversation_state,
            goal=reaction.active_goal,
        )

    def as_kwargs(self) -> dict:
        """`handle_message(text, **working_memory.as_kwargs())` for the
        next message in the same conversation."""
        return {
            "pending_action": self.pending_action,
            "conversation_state": self.reference,
            "active_goal": self.goal,
        }

    def is_empty(self) -> bool:
        """True when Mochi isn't in the middle of anything right now -
        no unconfirmed proposal, no reference to resolve, no open
        clarifying question. Useful for UI/logging that only cares
        whether there's *some* carried-over state, not which kind."""
        return self.pending_action is None and self.reference is None and self.goal is None
