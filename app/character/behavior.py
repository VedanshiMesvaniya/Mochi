"""
Deterministic autonomous behavior engine (spec section 12 / 31), rewritten
for the pixel-face design: Mochi no longer walks/jumps/plays around the
desktop (see docs - "no walking, no jumping, just Mochi's little pixel
face reacting to you"), so autonomous behavior is now purely about which
*expression* is showing, driven by how long it's been since the user last
interacted.

IMPORTANT: This engine must NEVER call the LLM - it's plain timer/RNG
logic so idle behavior costs effectively zero CPU (spec section 12).

Personality tiers, based purely on seconds since the last interaction:

    0 ... happy_hold ... bored_after ... sleepy_after ... sleep_after
    HAPPY   IDLE          BORED           SLEEPY          SLEEP
    (brief, (calm,        (plays through  (getting        (fully
     warm)   waiting)      its own faces   drowsy)          asleep)
                            on its own)

Left alone, Mochi doesn't just sit static forever: past bored_after it
starts entertaining itself, cycling through a curated set of expressions
on its own like a kitten poking around a room with nothing else to do,
before eventually winding down to sleepy and then asleep. Interacting at
any point resets the clock and brings it back to HAPPY.

Note WINK is deliberately excluded from the self-play pool (BORED_EXPRESSIONS)
- it now reads as a quick, deliberate, friendly expression (a chat
reaction, or shown on demand via a chat command like "make wink for me" -
see app/ai/intent.py), not something that should just sit there while
nobody's interacting.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable, Optional

from app.character.state_machine import CharacterState

# Expressions Mochi plays through on its own once bored (spec: "it plays
# on its own with different faces"). See module docstring for why WINK
# isn't in this pool.
BORED_EXPRESSIONS: tuple[CharacterState, ...] = (
    CharacterState.ALERT,
    CharacterState.EXCITED,
    CharacterState.THINKING,
    CharacterState.SURPRISED,
    CharacterState.PLAY,
    CharacterState.STRETCH,
    CharacterState.YAWN,
)


@dataclass
class BehaviorEngine:
    enabled: bool = True
    tick_interval_seconds: float = 2.0

    # Inactivity thresholds (seconds), spec: "make default happy if not
    # interact for a while then go idle, then if we don't interact around
    # 5 to 10 minutes it starts to get bored and plays with its own faces".
    happy_hold_seconds: float = 15.0
    bored_after_seconds: float = 300.0  # 5 min - start of the "5 to 10 min" window
    sleepy_after_seconds: float = 480.0  # 8 min
    sleep_after_seconds: float = 600.0  # 10 min

    # While bored, hold each self-play expression for a randomized number
    # of ticks rather than switching every single tick (which at the
    # default 2s tick would flicker distractingly fast).
    bored_hold_ticks_min: int = 2
    bored_hold_ticks_max: int = 4

    has_interacted: bool = False
    _idle_seconds: float = field(default=0.0, init=False)
    _happy_pending: bool = field(default=False, init=False)
    _bored_ticks_remaining: int = field(default=0, init=False)
    _last_bored_state: Optional[CharacterState] = field(default=None, init=False)
    _rng: random.Random = field(default_factory=random.Random)

    # ------------------------------------------------------------------
    def mark_interacted(self) -> None:
        """Call whenever the user clicks, drags, or chats with Mochi -
        resets the inactivity clock, cancels any in-progress bored
        cycling, and queues up the brief default-happy acknowledgment (see
        `_choose_state`), so a real interaction always takes priority."""
        self.has_interacted = True
        self._idle_seconds = 0.0
        self._happy_pending = True
        self._bored_ticks_remaining = 0
        self._last_bored_state = None

    def default_expression(self) -> CharacterState:
        """The face Mochi should rest on whenever nothing else is actively
        being shown (see PetWindow._on_expression_hold_expired, called once
        a reaction's hold timer expires) - HAPPY right after an
        interaction, settling to a calm IDLE once that brief window has
        passed. Doesn't account for bored/sleepy/sleep - tick() sets those
        directly, they're never left for a caller to fall back into."""
        if self.has_interacted and self._idle_seconds < self.happy_hold_seconds:
            return CharacterState.HAPPY
        return CharacterState.IDLE

    def next_interval(self) -> float:
        """Kept for API compatibility with callers that schedule a timer
        off this value; the engine itself now expects a fixed-cadence tick
        (see tick_interval_seconds) rather than a randomized one."""
        return self.tick_interval_seconds

    # ------------------------------------------------------------------
    def _choose_bored_state(self) -> Optional[CharacterState]:
        if self._bored_ticks_remaining > 0:
            self._bored_ticks_remaining -= 1
            return None  # keep showing the current bored expression
        self._bored_ticks_remaining = self._rng.randint(
            self.bored_hold_ticks_min, self.bored_hold_ticks_max
        )
        choices = [s for s in BORED_EXPRESSIONS if s != self._last_bored_state]
        next_state = self._rng.choice(choices or list(BORED_EXPRESSIONS))
        self._last_bored_state = next_state
        return next_state

    def _choose_state(self) -> Optional[CharacterState]:
        """Returns the state to apply, or None to leave the current state
        alone (e.g. mid-bored-hold, nothing has changed, or a more
        specific reaction - like a chat reply's expression - is already
        showing and shouldn't be interrupted by this engine)."""
        if not self.has_interacted:
            return CharacterState.IDLE

        if self._idle_seconds >= self.sleep_after_seconds:
            self._bored_ticks_remaining = 0
            return CharacterState.SLEEP
        if self._idle_seconds >= self.sleepy_after_seconds:
            self._bored_ticks_remaining = 0
            return CharacterState.SLEEPY
        if self._idle_seconds >= self.bored_after_seconds:
            return self._choose_bored_state()

        if self._happy_pending:
            self._happy_pending = False
            return CharacterState.HAPPY

        if self._idle_seconds >= self.happy_hold_seconds:
            # Past the brief "just interacted" window and not yet bored -
            # settle explicitly to IDLE. Safe to apply on every tick here
            # (unlike the old unconditional-every-tick IDLE forcing this
            # replaced): by the time idle_seconds has climbed past
            # happy_hold_seconds, any reaction's own hold timer (the
            # longest of which is under 5s - see pet.py REACTION_HOLD_MS)
            # has already expired on its own, so there's nothing left to
            # stomp on.
            return CharacterState.IDLE

        # Still within the brief "just interacted" window and nothing new
        # to apply this tick - leave whatever's currently showing alone (a
        # chat reaction, click reaction, etc. with its own hold timer; see
        # PetWindow._show_reaction) rather than stomping over it.
        return None

    def tick(self, apply_state: Callable[[CharacterState], None]) -> None:
        """Call on a fixed-interval QTimer. `apply_state` is a callback
        that actually applies the chosen state to the character's state
        machine - only called when this tick actually wants a change."""
        if not self.enabled:
            return
        self._idle_seconds += self.tick_interval_seconds
        next_state = self._choose_state()
        if next_state is not None:
            apply_state(next_state)
