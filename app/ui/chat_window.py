"""
Mochi's chat popup (spec section 14).

Deliberately small and compact rather than a full chat-app window - this is
what opens when you double-click or right-click -> Chat on the character.
Every message you send is run through app/ai/chat_engine.py, which detects
an intent and hands back a reaction; this window renders the reply text
into the log while the caller (PetWindow) is told to actually play the
animation/sound/speech-bubble, since the character owns that state, not
the chat window.

Voice input (spec section 15) isn't wired up yet - the mic button is
present but disabled, and degrades gracefully to typing-only per spec
section 36 (graceful degradation), rather than pretending it works.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
)

from app.ai.chat_engine import ChatReaction, handle_message
from app.character.state_machine import CharacterState, Emotion
from app.core.logger import get_logger
from app.ui.base_window import TranslucentDialog

logger = get_logger("mochi.ui.chat")

# Type alias for the reaction callback PetWindow supplies: called with the
# ChatReaction so the character can actually animate/speak/show a bubble.
ReactionCallback = Callable[[ChatReaction], None]


class ChatWindow(TranslucentDialog):
    def __init__(self, on_reaction: Optional[ReactionCallback] = None, parent=None) -> None:
        super().__init__("Mochi", parent)
        self.setMinimumSize(320, 380)
        self._on_reaction = on_reaction

        self._build_ui()
        self._append("Mochi", "Hehe, hi! What are we up to?")

    def _build_ui(self) -> None:
        self.message_log = QListWidget()
        self.message_log.setWordWrap(True)
        self.message_log.setFocusPolicy(Qt.NoFocus)
        self.content_layout.addWidget(self.message_log, stretch=1)

        input_row = QHBoxLayout()
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("Type something...")
        self.input_field.returnPressed.connect(self._on_send_clicked)
        input_row.addWidget(self.input_field, stretch=1)

        # Voice input (spec section 15/16) lands in a later phase; kept
        # visible-but-disabled so the UI already has a slot for it and the
        # user can see it's coming, rather than it silently not existing.
        self.mic_button = QPushButton("🎤")
        self.mic_button.setFixedWidth(36)
        self.mic_button.setEnabled(False)
        self.mic_button.setToolTip("Voice input isn't wired up yet - type for now!")
        input_row.addWidget(self.mic_button)

        self.send_button = QPushButton("Send")
        self.send_button.clicked.connect(self._on_send_clicked)
        input_row.addWidget(self.send_button)

        self.content_layout.addLayout(input_row)
        self.input_field.setFocus()

    # ------------------------------------------------------------------
    def _append(self, sender: str, text: str) -> None:
        item = QListWidgetItem(f"{sender}: {text}")
        if sender == "You":
            item.setForeground(Qt.lightGray)
        self.message_log.addItem(item)
        self.message_log.scrollToBottom()

    def _on_send_clicked(self) -> None:
        text = self.input_field.text().strip()
        if not text:
            return
        self.input_field.clear()
        self._append("You", text)

        try:
            reaction = handle_message(text)
        except Exception:  # noqa: BLE001 - chat must never crash the app
            logger.exception("Chat engine failed on message: %s", text)
            reaction = ChatReaction(
                text="Sorry, my brain hiccuped there. Try again?",
                emotion=Emotion.CONFUSED,
                animation=CharacterState.CONFUSED,
            )

        self._append("Mochi", reaction.text)
        if self._on_reaction is not None:
            self._on_reaction(reaction)
