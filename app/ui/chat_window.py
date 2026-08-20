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

`handle_message()` can fall through to a local LLM call bounded by a
30-second timeout (see app/ai/llm.py) - that's too long to run on the UI
thread without freezing the whole window, so it's run on a background
`ChatWorker` QThread instead; the result comes back via a Qt signal and is
applied on the UI thread as usual.

While a reply is pending, a "thinking..." bubble is shown directly in this
window's own log (see `_start_typing_indicator`/`_stop_typing_indicator`).
Without it, the only feedback during a slow reply was the character's face
changing state elsewhere on the desktop - easy to miss if you're looking at
this window, and it reads as the chat having silently stalled or closed.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QThread, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from app.ai.chat_engine import ChatReaction, handle_message
from app.character.state_machine import CharacterState, Emotion
from app.core.logger import get_logger
from app.ui.base_window import TranslucentDialog

logger = get_logger("mochi.ui.chat")

# Type alias for the reaction callback PetWindow supplies: called with the
# ChatReaction so the character can actually animate/speak/show a bubble.
ReactionCallback = Callable[[ChatReaction], None]

_BUBBLE_MAX_WIDTH = 230
_MOCHI_BUBBLE_STYLE = (
    "background-color: rgba(255, 255, 255, 225);"
    "color: #3a3350;"
    "border-radius: 14px;"
    "padding: 7px 12px;"
)
_USER_BUBBLE_STYLE = (
    "background-color: rgba(150, 120, 220, 210);"
    "color: #ffffff;"
    "border-radius: 14px;"
    "padding: 7px 12px;"
)


class ChatBubble(QWidget):
    """One message rendered as a rounded speech-bubble row - Mochi's
    replies left-aligned, the user's messages right-aligned, matching how
    the floating speech bubble above the character itself looks (spec:
    'make chat bubble for answer'), rather than plain list-row text."""

    def __init__(self, sender: str, text: str, parent=None) -> None:
        super().__init__(parent)
        self._is_user = sender == "You"

        label = QLabel(text)
        label.setWordWrap(True)
        label.setMaximumWidth(_BUBBLE_MAX_WIDTH)
        label.setStyleSheet(_USER_BUBBLE_STYLE if self._is_user else _MOCHI_BUBBLE_STYLE)
        label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self.label = label  # kept accessible so the typing indicator can
        # update this bubble's text in place instead of adding/removing rows

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        if self._is_user:
            layout.addStretch(1)
            layout.addWidget(label)
        else:
            layout.addWidget(label)
            layout.addStretch(1)


class ChatWorker(QThread):
    """Runs one `handle_message()` call off the UI thread so a slow local
    LLM reply (up to the 30s bound in app/ai/llm.py) never freezes the
    chat window or the rest of the app. One-shot: create, start, let it
    emit `finished_reaction`, then let it get garbage collected."""

    finished_reaction = Signal(object)  # emits a ChatReaction

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(parent)
        self._text = text

    def run(self) -> None:  # noqa: D102 - QThread override
        try:
            reaction = handle_message(self._text)
        except Exception:  # noqa: BLE001 - chat must never crash the app
            logger.exception("Chat engine failed on message: %s", self._text)
            reaction = ChatReaction(
                text="Sorry, my brain hiccuped there. Try again?",
                emotion=Emotion.CONFUSED,
                animation=CharacterState.CONFUSED,
            )
        self.finished_reaction.emit(reaction)


class ChatWindow(TranslucentDialog):
    def __init__(
        self,
        on_reaction: Optional[ReactionCallback] = None,
        on_thinking: Optional[Callable[[], None]] = None,
        parent=None,
    ) -> None:
        super().__init__("Mochi", parent, pinned_by_default=True)
        self.setMinimumSize(320, 380)
        self._on_reaction = on_reaction
        self._on_thinking = on_thinking

        self._worker: Optional[ChatWorker] = None

        # Typing indicator (spec: "chat looks closed/frozen while waiting").
        # The pet's face already changes state while a reply is pending,
        # but that's easy to miss when you're focused on this window, not
        # the character - so give feedback right here in the log too,
        # rather than the log just sitting still for up to 30s.
        self._typing_item: Optional[QListWidgetItem] = None
        self._typing_bubble: Optional[ChatBubble] = None
        self._typing_frame = 0
        self._typing_timer = QTimer(self)
        self._typing_timer.setInterval(450)
        self._typing_timer.timeout.connect(self._advance_typing_indicator)

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

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        # Avoid "QThread destroyed while still running" if the window is
        # closed mid-reply; the LLM call itself can't be cancelled, but we
        # can at least wait briefly for the worker to notice and finish.
        self._typing_timer.stop()
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(200)
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Typing indicator
    # ------------------------------------------------------------------
    def _start_typing_indicator(self) -> None:
        self._typing_frame = 0
        self._typing_bubble = ChatBubble("Mochi", "thinking")
        item = QListWidgetItem()
        item.setFlags(Qt.NoItemFlags)
        item.setSizeHint(self._typing_bubble.sizeHint())
        self.message_log.addItem(item)
        self.message_log.setItemWidget(item, self._typing_bubble)
        self.message_log.scrollToBottom()
        self._typing_item = item
        self._typing_timer.start()

    def _advance_typing_indicator(self) -> None:
        if self._typing_bubble is None:
            return
        self._typing_frame = (self._typing_frame + 1) % 3
        self._typing_bubble.label.setText("thinking" + "." * (self._typing_frame + 1))

    def _stop_typing_indicator(self) -> None:
        self._typing_timer.stop()
        if self._typing_item is not None:
            row = self.message_log.row(self._typing_item)
            if row != -1:
                self.message_log.takeItem(row)
        self._typing_item = None
        self._typing_bubble = None

    # ------------------------------------------------------------------
    def _append(self, sender: str, text: str) -> None:
        bubble = ChatBubble(sender, text)
        item = QListWidgetItem()
        item.setFlags(Qt.NoItemFlags)  # display-only, not selectable/clickable
        item.setSizeHint(bubble.sizeHint())
        self.message_log.addItem(item)
        self.message_log.setItemWidget(item, bubble)
        self.message_log.scrollToBottom()

    def _on_send_clicked(self) -> None:
        if self._worker is not None:
            return  # a reply is already in flight - ignore double-sends

        text = self.input_field.text().strip()
        if not text:
            return
        self.input_field.clear()
        self._append("You", text)

        if self._on_thinking is not None:
            self._on_thinking()

        # "Busy" while waiting: read-only (not fully disabled) so the field
        # keeps keyboard focus instead of yanking it elsewhere the instant
        # a reply is requested. A widget losing focus right as a Qt.Tool
        # popup like this one is mid-interaction is exactly the kind of
        # thing that can make the window drop out of view on some
        # platforms (spec: "chat window closes, I have to open it again") -
        # keeping focus inside the dialog avoids that trigger outright.
        self.input_field.setReadOnly(True)
        self.send_button.setEnabled(False)
        self._start_typing_indicator()

        self._worker = ChatWorker(text, self)
        self._worker.finished_reaction.connect(self._on_reaction_ready)
        self._worker.start()

    def _on_reaction_ready(self, reaction: ChatReaction) -> None:
        self._stop_typing_indicator()
        self._append("Mochi", reaction.text)
        if self._on_reaction is not None:
            self._on_reaction(reaction)

        self.input_field.setReadOnly(False)
        self.send_button.setEnabled(True)
        self.input_field.setFocus()

        # Bring the window back to front for the reply. show() first,
        # deliberately, not just raise_()/activateWindow(): those only
        # reorder/refocus a window that's already visible - they do
        # nothing if it somehow ended up hidden while the reply was
        # pending, which is exactly the failure mode behind "chat window
        # closes, I have to reopen it". show() is a harmless no-op if it
        # was visible the whole time, and a real fix if it wasn't.
        self.show()
        self.raise_()
        self.activateWindow()

        # Let the finished thread be cleaned up before the next message.
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
