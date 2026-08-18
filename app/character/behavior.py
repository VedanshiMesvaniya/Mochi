"""
Deterministic autonomous behavior engine (spec section 12 / 31), rewritten
for the pixel-face design: Mochi no longer walks/jumps/plays around the
desktop (see docs - "no walking, no jumping, just Mochi's little pixel
face reacting to you"), so autonomous behavior is now purely about which
*expression* is showing, driven by how long it's been since the user last
interacted.

IMPORTANT: This engine must NEVER call the LLM - it's plain timer/RNG
logic so idle behavior costs effectively zero CPU (spec section 12).

Personality: a playful kitten that wants attention. Left alone, Mochi
doesn't just sit static - it occasionally perks up (ALERT) like a cat
noticing something, gets sleepy, and eventually falls asleep; interacting
at any point resets the clock and wakes it back up.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable, Optional

from app.character.state_machine import CharacterState


@dataclass
class BehaviorEngine:
    """
    Ticked on a regular short interval (see PetWindow._on_behavior_tick).
    Tracks seconds since the last interaction and picks the next face
    state from that alone - no weighted random walk anymore, just tiers:

        0s ................ attention_after ...... sleepy_after ...... sleep_after
        engaged/idle        occasional ALERT        SLEEPY              SLEEP
                             "attention" pings
    """

    enabled: bool = True
    tick_interval_seconds: float = 2.0

    # Inactivity thresholds (seconds). Kept short by default so the
    # personality is noticeable without waiting minutes in normal use;
    # tune via settings later if this should be configurable.
    attention_after_seconds: float = 45.0
    sleepy_after_seconds: float = 150.0
    sleep_after_seconds: float = 300.0

    # Chance per tick, once past attention_after_seconds and still awake,
    # of a short attention-seeking ping (kitten personality). Raised
    # slightly and now alternates between ALERT and a playful WINK so an
    # ignored Mochi reads as more expressive rather than repeating the
    # exact same pulse every time.
    attention_ping_chance: float = 0.18
    attention_ping_duration_ticks: int = 2  # how many ticks the ping holds before reverting
    wink_ping_chance: float = 0.5  # of an attention ping firing, how often it's a WINK instead of ALERT

    has_interacted: bool = False
    _idle_seconds: float = field(default=0.0, init=False)
    _alert_ticks_remaining: int = field(default=0, init=False)
    _rng: random.Random = field(default_factory=random.Random)

    # ------------------------------------------------------------------
    def mark_interacted(self) -> None:
        """Call whenever the user clicks, drags, or chats with Mochi -
        resets the inactivity clock and cancels any pending attention
        ping, so a real interaction always takes priority."""
        self.has_interacted = True
        self._idle_seconds = 0.0
        self._alert_ticks_remaining = 0

    def next_interval(self) -> float:
        """Kept for API compatibility with callers that schedule a timer
        off this value; the engine itself now expects a fixed-cadence tick
        (see tick_interval_seconds) rather than a randomized one."""
        return self.tick_interval_seconds

    # ------------------------------------------------------------------
    def _choose_state(self) -> Optional[CharacterState]:
        """Returns the state to apply, or None to leave the current state
        alone (e.g. mid-attention-ping, or nothing has changed)."""
        if not self.has_interacted:
            return CharacterState.IDLE

        if self._alert_ticks_remaining > 0:
            self._alert_ticks_remaining -= 1
            return None  # keep showing ALERT until the ping finishes

        if self._idle_seconds >= self.sleep_after_seconds:
            return CharacterState.SLEEP
        if self._idle_seconds >= self.sleepy_after_seconds:
            return CharacterState.SLEEPY
        if self._idle_seconds >= self.attention_after_seconds:
            if self._rng.random() < self.attention_ping_chance:
                self._alert_ticks_remaining = self.attention_ping_duration_ticks
                if self._rng.random() < self.wink_ping_chance:
                    return CharacterState.WINK
                return CharacterState.ALERT
            return None  # stay however it currently is between pings
        return CharacterState.IDLE

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
