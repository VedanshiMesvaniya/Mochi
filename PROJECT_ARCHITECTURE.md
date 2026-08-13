# Mochi — Project Architecture

This document describes how Project Mochi is structured internally: the
module layout, data flow, and the core design principles that constrain
every future change. Read this before adding new subsystems.

---

## 1. Guiding principles

1. **Character first, assistant second.** Mochi must feel like a small
   creature living on the desktop, not a chat window. Every feature is
   built to support that illusion (presence, idle behavior, sound,
   animation) before it's built to be "smart."
2. **Local-first, privacy-first.** No cloud AI, no data upload, no remote
   database by default. The only optional network calls are for Google
   Calendar (explicit opt-in) and model/software updates.
3. **The LLM proposes, Python disposes.** The language model never directly
   executes an action. It returns structured JSON; Python validates it
   against a schema and permission rules before anything happens
   (see §5).
4. **Deterministic where possible.** Idle wandering, walking, sitting, etc.
   are driven by a weighted-random behavior engine and QTimers — never by
   calling the LLM. The LLM is reserved for actual language understanding.
5. **Graceful degradation.** If Ollama, the mic, TTS, or Google Calendar are
   unavailable, Mochi keeps working in a reduced mode instead of crashing
   (see §7).

---

## 2. High-level data flow

```text
                     ┌─────────────────────────┐
                     │        User Input         │
                     │   (typing / microphone)   │
                     └─────────────┬─────────────┘
                                   │
                     ┌─────────────▼─────────────┐
                     │     Speech-to-Text (STT)   │   (Phase 4)
                     │        faster-whisper      │
                     └─────────────┬─────────────┘
                                   │ text
                     ┌─────────────▼─────────────┐
                     │        AI / Intent Layer    │   (Phase 2+)
                     │   app/ai/  →  Qwen3 0.6B     │
                     │   (Ollama, local inference)  │
                     └─────────────┬─────────────┘
                                   │ structured JSON
                     ┌─────────────▼─────────────┐
                     │   Schema + Permission Check  │
                     │      (app/ai/structured_     │
                     │        output.py)            │
                     └──┬─────┬─────┬─────┬────────┘
                        │     │     │     │
                 ┌──────▼┐ ┌──▼───┐ ┌▼────┐ ┌▼─────────┐
                 │ Chat  │ │Memory│ │Remind│ │ Calendar  │
                 │(TTS)  │ │SQLite│ │ SQLite│ │Local/Google│
                 └───┬───┘ └──────┘ └───┬──┘ └──────────┘
                     │                  │
                     ▼                  ▼
              Mochi Character   Reminder Scheduler
              (animation, sound,  (background QTimer,
               speech bubble)      fires due reminders)
```

For Phase 1/1.5 (current state of this repo), only the bottom-right/left
loop exists: the **Character** and the **Reminder** subsystem. Chat/AI/
voice/calendar land in later phases per §8 below, but the architecture
already reserves their place so adding them later doesn't require
reshaping existing code.

---

## 3. Module map

```text
app/
├── main.py                 Application entry point / wiring
│
├── core/                   Cross-cutting concerns, no Qt/AI dependencies
│   ├── config.py           .env-driven Settings singleton
│   ├── logger.py           Rotating file + console logger factory
│   ├── events.py           In-process pub/sub event bus (Events constants)
│   └── exceptions.py       Typed exception hierarchy
│
├── character/               Everything about Mochi's on-screen presence
│   ├── pet.py               PySide6 transparent/frameless/draggable window
│   ├── animator.py          Loads sprite frames from assets/animations/*
│   ├── movement.py          Pure-Python walk/bounds math (unit-testable)
│   ├── behavior.py          Weighted-random autonomous behavior engine
│   └── state_machine.py     CharacterState + Emotion enums, event publisher
│
├── ai/                      (Phase 2+) LLM access, prompts, intent parsing
├── memory/                  SQLite access layer
│   └── database.py          Connection management + schema (reminders now;
│                            conversations/memories/mood/relationship/
│                            calendar_cache tables land in later phases)
├── voice/                   (Phase 4+) mic capture, STT, TTS, sound effects
├── tools/                   Python functions the LLM's intents map to
│   └── reminder_tools.py    ✅ create/list/complete/cancel/snooze/delete -
│                            JSON-in/JSON-out wrapper around reminders/manager.py,
│                            ready to be the AI layer's execution target
├── reminders/                ✅ Local reminder engine (V1)
│   ├── manager.py           CRUD over the `reminders` table + repeat rules
│   ├── scheduler.py         QTimer polling for due reminders (no AI calls)
│   └── notifications.py     Turns a due reminder into character events
│                            (wake animation, sound, speech, OS notification)
├── calendar/                (Phase 7/8) local + Google Calendar
└── ui/                      Qt windows/dialogs
    ├── tray.py               ✅ System tray icon + menu
    ├── reminder_window.py    ✅ Create/list/complete/snooze/delete reminders UI
    ├── chat_window.py         (Phase 2+)
    ├── settings_window.py     (Phase 10)
    └── calendar_window.py     (Phase 7/8)
```

### Dependency direction

`core` depends on nothing else in the app. `character`, `memory`,
`reminders`, `voice`, `calendar` depend only on `core` (+ each other where
listed below), never on `ui`. `ui` and `tools` are the only layers allowed
to import from multiple subsystems and wire them together. `ai` depends on
`core` and calls into `tools`, never the reverse.

```text
core  ←  character, memory, reminders, voice, calendar, ai
core + memory  ←  reminders
core + memory + reminders + calendar  ←  tools
core + character + memory + reminders + tools + ai  ←  ui, main.py
```

This keeps every subsystem testable in isolation (see `tests/`) without
needing a running Qt application or a live Ollama server.

---

## 4. The event bus

`app/core/events.py` provides a tiny synchronous pub/sub bus
(`event_bus.publish(name, payload)` / `event_bus.subscribe(name, handler)`).
It exists so subsystems don't need to import each other's Qt widgets
directly. For example:

- The reminder scheduler publishes `Events.REMINDER_DUE` when a reminder
  fires. It does not know or care that a PySide6 window is listening.
- `CharacterStateMachine.set_emotion()` publishes `Events.EMOTION_CHANGED`
  and, if the emotion has an associated sound, `Events.SOUND_REQUESTED`.
  The animator and sound player subscribe independently.

This is deliberately simple (in-process, synchronous, no async/threading
guarantees beyond "don't let one bad handler break the others"). It is not
meant to scale beyond a single-process desktop app.

---

## 5. Structured AI output & validation (Phase 2+)

Once the AI layer lands, every LLM response follows this shape
(see main spec §7):

```json
{
  "response": "Okay! I'll remind you at 7 PM.",
  "intent": "create_reminder",
  "emotion": "happy",
  "animation": "happy",
  "sound": "chirp",
  "action": {
    "type": "create_reminder",
    "title": "Call Mom",
    "datetime": "2026-08-12T19:00:00"
  }
}
```

The flow from raw model output to an executed action is always:

```text
LLM output (untrusted)
      │
      ▼
Schema validation      → reject if shape/types are wrong
      │
      ▼
Permission check        → e.g. calendar write needs the write scope enabled
      │
      ▼
User confirmation        → required for calendar create/update/delete
   (if required)
      │
      ▼
Tool execution (app/tools/*.py + app/reminders, app/calendar)
```

The LLM is **never** allowed to run shell commands, hit the filesystem
directly, or call the Google Calendar API itself. It only ever proposes a
`(type, args)` action; a Python tool function executes it.

---

## 6. Local reminders (V1 — implemented)

Reminders are fully local and do not require the AI layer to function —
V1 supports creating/listing/completing/snoozing/deleting reminders
directly through a UI dialog (`app/ui/reminder_window.py`), reachable from
Mochi's right-click menu. Once Phase 2 (AI) lands, the same
`app/tools/reminder_tools.py` functions become the execution target for
LLM-parsed natural language requests like *"remind me at 7pm to call
mom"* — the storage/scheduling layer does not change.

Layering (bottom = storage, top = what a caller uses):

```text
app/reminders/manager.py        raw CRUD + repeat-rule math, datetime in/out
        │
        ├── app/reminders/scheduler.py     polls for due reminders (QTimer)
        │         │
        │         └── app/reminders/notifications.py   due → wake/sound/speech/OS toast
        │
        ├── app/ui/reminder_window.py       manual create/list/complete/snooze/delete
        │
        └── app/tools/reminder_tools.py     JSON-in/JSON-out wrapper (Phase 2's target)
```

```text
data/mochi.db
└── reminders
    ├── id            INTEGER PRIMARY KEY
    ├── title         TEXT
    ├── due_at         TEXT (ISO 8601, local timezone)
    ├── repeat_rule    TEXT NULL  ("DAILY", "WEEKLY:MON", ...)
    ├── status         TEXT  ("pending" | "completed" | "cancelled")
    ├── created_at      TEXT (ISO 8601)
    └── completed_at    TEXT NULL
```

`app/reminders/scheduler.py` polls (via QTimer, default every 15s) for
reminders whose `due_at` has passed and `status = 'pending'`. For each due
reminder it publishes `Events.REMINDER_DUE`, which:

1. Wakes the character (`CharacterState.WAKE`)
2. Plays a notification sound
3. Shows a speech bubble with the reminder title
4. Triggers an OS-level desktop notification (via `QSystemTrayIcon`)

If the app was closed when a reminder became due, the scheduler checks for
missed reminders on the next startup and surfaces them then (spec §20's
"one limitation" note).

---

## 7. Error handling philosophy

Each subsystem raises a specific exception from `app/core/exceptions.py`
(`LLMUnavailableError`, `TTSUnavailableError`, `ReminderError`, etc.).
Callers at the `ui`/`main` layer catch these and degrade instead of
crashing:

| Failure                  | Fallback behavior                              |
|---------------------------|-------------------------------------------------|
| Ollama not running         | Chat shows "My brain isn't responding right now…" |
| TTS engine unavailable     | Response text still shown, just not spoken       |
| Microphone unavailable     | Typing still works                                |
| Google Calendar unavailable| Local reminders/calendar keep working             |
| Animation frames missing   | Window stays transparent instead of erroring      |
| Log file not writable      | Falls back to console-only logging                |

---

## 8. Phase roadmap (for context)

This repo is built incrementally. See `README.md` for the up-to-date
checklist of what's implemented so far. The target phase order:

1. **Desktop Mochi** — transparent character, idle/walk/drag, tray, exit
2. **Chat** — Ollama + Qwen3 0.6B, personality prompt, text responses
3. **Character reactions** — emotion → animation/sound mapping
4. **Voice** — faster-whisper (STT) + Piper (TTS)
5. **Local memory** — SQLite conversations + explicit long-term memories
6. **Reminders** — create/list/update/delete/repeat/snooze/notify
7. **Local calendar** — offline events, natural-language date parsing
8. **Google Calendar** — OAuth, read/search/create/update/delete + confirm
9. **Personality / relationship** — mood, familiarity, affection over time
10. **Polish** — settings UI, onboarding, installer, crash handling

`V1` (this repo's near-term target) = Phase 1 (desktop character) + a local
reminder system, ahead of the full chat/AI layer — see the phased rollout
discussion that motivated building reminders before Google Calendar
integration.
