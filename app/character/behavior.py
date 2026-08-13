"""
Deterministic autonomous behavior engine (spec section 12 / 31).

IMPORTANT: This engine must NEVER call the LLM. Idle wandering behavior is
purely probabilistic/timer-driven Python logic, so Mochi can move around the
desktop with effectively zero CPU/AI cost. The LLM is reserved for actual
conversation (see app/ai/).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable

from app.character.state_machine import CharacterState


@dataclass
class BehaviorOption:
    state: CharacterState
    weight: float


DEFAULT_BEHAVIOR_WEIGHTS: list[BehaviorOption] = [
    BehaviorOption(CharacterState.IDLE, 0.35),
    BehaviorOption(CharacterState.WALK_LEFT, 0.12),
    BehaviorOption(CharacterState.WALK_RIGHT, 0.12),
    BehaviorOption(CharacterState.LOOK_LEFT, 0.06),
    BehaviorOption(CharacterState.LOOK_RIGHT, 0.06),
    BehaviorOption(CharacterState.SLEEP, 0.10),
    BehaviorOption(CharacterState.STRETCH, 0.08),
    BehaviorOption(CharacterState.YAWN, 0.08),
    BehaviorOption(CharacterState.PLAY, 0.07),
]


@dataclass
class BehaviorEngine:
    """
    Picks the next autonomous behavior using weighted random choice, on a
    timer. Movement frequency / enabled-ness are user-configurable
    (spec section 29 - Settings > Behavior).
    """

    enabled: bool = True
    min_interval_seconds: float = 4.0
    max_interval_seconds: float = 12.0
    weights: list[BehaviorOption] = field(
        default_factory=lambda: list(DEFAULT_BEHAVIOR_WEIGHTS)
    )
    _rng: random.Random = field(default_factory=random.Random)

    def next_interval(self) -> float:
        return self._rng.uniform(self.min_interval_seconds, self.max_interval_seconds)

    def choose_behavior(self) -> CharacterState:
        options = [w.state for w in self.weights]
        weights = [w.weight for w in self.weights]
        return self._rng.choices(options, weights=weights, k=1)[0]

    def tick(self, apply_state: Callable[[CharacterState], None]) -> None:
        """Call periodically (e.g. from a QTimer) to potentially trigger a
        new autonomous behavior. `apply_state` is a callback that actually
        applies the chosen state to the character's state machine."""
        if not self.enabled:
            return
        apply_state(self.choose_behavior())
