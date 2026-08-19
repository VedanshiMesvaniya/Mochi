"""
Mochi's character state machine.

Two independent-but-related axes of state:

- CharacterState: what Mochi is physically doing (idle, walking, sleeping...)
- Emotion: how Mochi feels (spec section 9), which influences animation,
  sound, TTS style, and behavior.

Both are simple enums so the rest of the app (animator, behavior engine,
AI response layer) can reason about them without stringly-typed values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.core.events import Events, event_bus


class CharacterState(str, Enum):
    IDLE = "idle"
    WALK_LEFT = "walk_left"
    WALK_RIGHT = "walk_right"
    SLEEP = "sleep"
    WAKE = "wake"
    DRAGGED = "dragged"
    TALKING = "talking"
    THINKING = "thinking"
    PLAY = "play"
    JUMP = "jump"
    STRETCH = "stretch"
    YAWN = "yawn"
    LOOK_LEFT = "look_left"
    LOOK_RIGHT = "look_right"
    LOOK_UP = "look_up"
    LOOK_DOWN = "look_down"
    # Emotion-reaction states (spec section 9) - these map 1:1 onto the
    # emotion animation folders in assets/animations/, so a chat reaction
    # can actually be *seen* instead of just changing an internal enum
    # nothing reads.
    HAPPY = "happy"
    SAD = "sad"
    EXCITED = "excited"
    ANGRY = "angry"
    CONFUSED = "confused"
    SURPRISED = "surprised"
    # EMOTE-style pixel-face states (see app/character/pixel_face.py).
    # SLEEPY is a light-drowsy state distinct from SLEEP (fully asleep);
    # ALERT is the "kitten noticed something / wants attention" pulse.
    SLEEPY = "sleepy"
    ALERT = "alert"
    # Extra expressive states (spec: "give all pending expressions" - the
    # 16-expression reference sheet). Reached organically through chat
    # reactions/behavior rather than the physical-movement states above.
    BLUSH = "blush"
    SHY = "shy"
    HEART = "heart"
    WINK = "wink"
    # Windows lock-screen easter egg (spec section: "fun" lock reaction) -
    # eyes closed like SLEEP but distinct so it's never confused with
    # actually being tired, and never shows the sleepy Zzz.
    LOCKED = "locked"
    # Shake-the-window easter egg (see app/character/shake_detector.py):
    # a brief dizzy spinning-eyes reaction, followed by ANGRY.
    DIZZY = "dizzy"


class Emotion(str, Enum):
    NEUTRAL = "neutral"
    HAPPY = "happy"
    EXCITED = "excited"
    CURIOUS = "curious"
    SLEEPY = "sleepy"
    SAD = "sad"
    CONFUSED = "confused"
    ANNOYED = "annoyed"
    SURPRISED = "surprised"
    PLAYFUL = "playful"


# Emotion -> (animation folder, optional sound name) per spec section 9.
# Animation names here now point at pixel-face states (see
# app/character/pixel_face.py::FACE_EXPRESSIONS) rather than sprite folders.
EMOTION_PROFILE = {
    Emotion.NEUTRAL: {"animation": "idle", "sound": None},
    Emotion.HAPPY: {"animation": "happy", "sound": "chirp"},
    Emotion.EXCITED: {"animation": "excited", "sound": "chirp"},
    Emotion.CURIOUS: {"animation": "thinking", "sound": None},
    Emotion.SLEEPY: {"animation": "sleepy", "sound": "yawn"},
    Emotion.SAD: {"animation": "sad", "sound": None},
    Emotion.CONFUSED: {"animation": "confused", "sound": None},
    Emotion.ANNOYED: {"animation": "angry", "sound": None},
    Emotion.SURPRISED: {"animation": "surprised", "sound": "surprised"},
    Emotion.PLAYFUL: {"animation": "excited", "sound": "purr"},
}


@dataclass
class CharacterStateMachine:
    """Tracks Mochi's current physical state and emotion, and notifies
    the rest of the app via the event bus when either changes."""

    state: CharacterState = CharacterState.IDLE
    emotion: Emotion = Emotion.NEUTRAL
    _history: list = field(default_factory=list)

    def set_state(self, new_state: CharacterState) -> None:
        if new_state == self.state:
            return
        self._history.append(self.state)
        self.state = new_state
        event_bus.publish(Events.ANIMATION_REQUESTED, {"animation": new_state.value})

    def set_emotion(self, new_emotion: Emotion, *, react: bool = True) -> None:
        """Update Mochi's mood and (by default) actually play the matching
        reaction animation/sound - see EMOTION_PROFILE. Pass react=False if
        a caller only wants to track mood without interrupting whatever
        animation is currently playing.
        """
        if new_emotion == self.emotion:
            return
        self.emotion = new_emotion
        profile = EMOTION_PROFILE.get(new_emotion, {})
        event_bus.publish(
            Events.EMOTION_CHANGED,
            {"emotion": new_emotion.value, **profile},
        )
        if profile.get("sound"):
            event_bus.publish(Events.SOUND_REQUESTED, {"sound": profile["sound"]})
        animation = profile.get("animation")
        if react and animation:
            try:
                self.set_state(CharacterState(animation))
            except ValueError:
                pass  # animation name without a matching CharacterState yet

    def previous_state(self) -> CharacterState | None:
        return self._history[-1] if self._history else None
