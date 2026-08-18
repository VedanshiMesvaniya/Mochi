# Mochi — Project Architecture

How Mochi is structured internally: module layout, data flow, and the
design principles that constrain new changes. Read this before adding a
new subsystem.

---

## 1. Guiding principles

1. **Face first, assistant second.** Mochi is a character, not a chat
   window with a mascot glued on. Every feature supports that (idle
   presence, expression, personality) before it's built to be "smart."
2. **Local-first, privacy-first.** No cloud AI, no data upload, no remote
   database. The only network-capable feature is the optional local-LLM
   chat fallback, which talks to Ollama on `localhost` — never a hosted API.
3. **Deterministic where it matters.** Anything that creates, modifies, or
   deletes data (reminders, tasks, timers) is handled by a rule-based
   intent matcher, never by a language model's guess. The LLM is only
   ever used for open-ended small talk the rule-based matcher doesn't
   recognize, and even then Python validates whatever comes back before
   acting on it.
4. **Autonomous behavior is cheap.** Idle expression changes (alert pings,
   getting sleepy, falling asleep) are driven by a plain inactivity timer
   and RNG — never by calling the LLM.
5. **Graceful degradation.** If Ollama isn't running, chat still answers
   (with a canned reply instead of an LLM one) rather than breaking.

---

## 2. High-level data flow

```text
                     ┌───────────────────────────┐
                     │      User types a message   │
                     │     (app/ui/chat_window.py)  │
                     └─────────────┬───────────────┘
                                   │ text
                     ┌─────────────▼───────────────┐
                     │   app/ai/intent.py            │
                     │   rule-based matcher:          │
                     │   greeting / reminder / task /  │
                     │   timer / small talk / unknown   │
                     └─────────────┬───────────────┘
                                   │
                    ┌──────────────┴───────────────┐
                    │ recognized action?             │
                    └──────┬─────────────────┬──────┘
                       yes │                  │ no ("unknown")
                    ┌──────▼──────┐    ┌──────▼───────────────┐
                    │ app/tools/*  │    │ app/ai/llm.py          │
                    │ (validated,  │    │ local Ollama call,     │
                    │  deterministic)│    │ short timeout,         │
                    └──────┬──────┘    │ falls back to a canned │
                           │           │ reply on any failure    │
                           │           └──────┬───────────────┘
                           └────────┬──────────┘
                                    ▼
                     app/ai/chat_engine.ChatReaction
                     (text, emotion, animation, sound)
                                    │
                                    ▼
                     app/character/pet.PetWindow
                     sets the pixel face's state, plays
                     a sound, shows a speech bubble
```

Reminders/tasks/timers becoming due follows a separate, simpler loop that
doesn't involve chat at all — a background `QTimer` polls SQLite and
publishes an event when something's due (see §6).

---

## 3. Module map

```text
app/
├── main.py                  Application entry point / wiring
│
├── core/                     Cross-cutting concerns, no Qt/AI dependencies
│   ├── config.py             .env-driven Settings singleton
│   ├── logger.py             Rotating file + console logger factory
│   ├── events.py             In-process pub/sub event bus (Events constants)
│   └── exceptions.py         Typed exception hierarchy
│
├── character/                 Mochi's on-screen presence
│   ├── pet.py                 PySide6 transparent/frameless/draggable window;
│   │                           owns the state machine, behavior engine, and
│   │                           the face widget; wires chat/reminder/task
│   │                           reactions, the theme menu, and the lock-screen
│   │                           watcher into visible state changes
│   ├── pixel_face.py           Programmatic EMO-style face renderer - eyes,
│   │                           mouth, brows, ears, whiskers, blink/pulse/
│   │                           talk-cycle animation, cursor-following pupils,
│   │                           a spring-physics smoothing layer so expression
│   │                           changes bounce into place instead of snapping,
│   │                           and a cartoon multi-glyph "Zzz" while asleep.
│   │                           No image assets; every expression is a small
│   │                           dataclass of numbers (FACE_EXPRESSIONS table,
│   │                           16 expressions total)
│   ├── theme.py                 Four selectable glow-color palettes (Purple/
│   │                            Blue/Mint/Rose) - cosmetic only, never touches
│   │                            expression geometry
│   ├── lock_watcher.py           Windows-only (ctypes) OS lock-state polling
│   │                             for the lock-screen easter egg; injectable
│   │                             probe for testing, safe no-op elsewhere
│   ├── state_machine.py        CharacterState + Emotion enums, event publisher
│   ├── behavior.py             Inactivity-tiered autonomous behavior (idle →
│   │                           occasional "alert"/"wink" attention ping →
│   │                           sleepy → sleep); fixed-interval QTimer, no LLM
│   │                           calls
│   └── movement.py             Screen-bounds math used when dragging the window
│
├── ai/                         Chat brain
│   ├── intent.py                Rule-based matcher: greeting/farewell/thanks/
│   │                            compliment (mild → blush, strong → heart)/
│   │                            insult/sleepy/bored/reminder/timer/task/
│   │                            unknown, each mapped to a response + emotion
│   │                            + animation (+ a tool call, for actionable
│   │                            intents)
│   ├── chat_engine.py            Orchestrates a message end-to-end: runs
│   │                            intent detection, executes any tool call
│   │                            (with validation), falls through to the
│   │                            local LLM for "unknown" messages, records
│   │                            the interaction for familiarity tracking
│   └── llm.py                    Optional local LLM backend - talks to
│                                 Ollama's HTTP API directly (stdlib only,
│                                 no extra dependency); 30s bounded timeout
│                                 (generous enough for a cold local model);
│                                 raises LLMUnavailable on any failure so
│                                 the caller can fall back gracefully
│
├── memory/                     SQLite access layer
│   ├── database.py              Connection management + schema (reminders,
│   │                            tasks, timers, relationship, app_settings)
│   ├── relationship.py           Lightweight interaction counter (not ML) -
│   │                             flavors greetings/LLM tone as familiarity
│   │                             grows (new / getting_to_know / familiar)
│   └── settings_store.py         Tiny SQLite key-value store for small
│                                 persisted preferences (currently: glow theme)
│
├── tools/                      JSON-in/JSON-out functions the intent layer
│   ├── reminder_tools.py        calls to actually create/modify data -
│   ├── task_tools.py            this is the validation boundary between
│   └── timer_tools.py           "the chat layer said so" and "it happened"
│
├── reminders/                  Local reminder engine
│   ├── manager.py                CRUD over the `reminders` table + repeat rules
│   ├── scheduler.py               QTimer polling for due reminders (~15s)
│   └── notifications.py           Due reminder → alert/sound/speech/OS toast;
│                                  also checks back later and reacts annoyed
│                                  if the reminder is still pending
│
├── tasks/
│   └── manager.py                CRUD over the `tasks` table (open/done/cancelled)
│
├── timers/                     Local countdown timers
│   ├── manager.py                 CRUD over the `timers` table
│   ├── scheduler.py                QTimer polling for finished timers (~1s)
│   └── notifications.py            Finished timer → reaction/sound/speech/OS toast
│
└── ui/                          Qt windows/dialogs
    ├── base_window.py            Shared frameless/translucent/rounded dialog
    │                             base (frosted-glass style, draggable,
    │                             macOS-style close/minimize/pin dots)
    ├── chat_window.py             The chat popup; runs the LLM call on a
    │                             background ChatWorker(QThread) so a slow
    │                             reply never freezes the UI
    ├── reminder_window.py          Create/list/complete/snooze/delete reminders
    ├── task_window.py              Add/toggle-done/delete tasks
    ├── timer_window.py             Start/view/extend/cancel timers
    └── tray.py                     System tray icon + menu
```

### Dependency direction

`core` depends on nothing else in the app. `character`, `memory`,
`reminders`, `tasks`, `timers` depend only on `core`. `tools` depends on
`core` + the relevant subsystem (`reminders`/`tasks`/`timers`). `ai`
depends on `core`, `memory`, and `tools`, and calls into `character`'s
state/emotion types to describe a reaction — it never imports Qt directly.
`ui` and `main.py` are the only layers allowed to wire multiple subsystems
together.

```text
core  ←  character, memory, reminders, tasks, timers, ai
core + memory  ←  reminders, tasks, timers
core + memory + reminders/tasks/timers  ←  tools
core + memory + tools + character(types only)  ←  ai
core + character + ai + reminders + tasks + timers  ←  ui, main.py
```

This keeps every subsystem testable without a running Qt app or a live
Ollama server — see `tests/`, which covers all of the above except `ui`
and `main.py` directly (those are exercised via offscreen Qt smoke tests
during development, not unit tests).

---

## 4. The event bus

`app/core/events.py` is a tiny synchronous pub/sub bus
(`event_bus.publish(name, payload)` / `event_bus.subscribe(name, handler)`)
so subsystems don't need to import each other's Qt widgets directly.
Examples:

- The reminder/timer schedulers publish `Events.REMINDER_DUE` /
  `Events.TIMER_DONE` when something fires; `PetWindow` and the tray icon
  react without the scheduler knowing either exists.
- Completing a reminder or task publishes `Events.REMINDER_COMPLETED` /
  `Events.TASK_COMPLETED`; `PetWindow` subscribes and reacts happy.
- `CharacterStateMachine.set_emotion()` publishes `Events.EMOTION_CHANGED`
  and, if the emotion has an associated sound, `Events.SOUND_REQUESTED`.

Deliberately simple: in-process, synchronous, single-process only. Not
meant to scale beyond one desktop app instance.

---

## 5. Chat: from a typed message to a validated action

```text
User text
      │
      ▼
app/ai/intent.py — rule-based match
      │
      ├─ actionable (reminder/task/timer) ─────► app/tools/*.py
      │                                            │
      │                                    schema/permission-checked,
      │                                    executes against SQLite
      │
      └─ "unknown" ─────► app/ai/llm.py ─► Ollama (localhost)
                                │
                          success: structured {response, emotion}
                          failure: LLMUnavailable → canned fallback reply
```

The rule-based matcher is intentionally the only thing allowed to trigger
data changes. The LLM path only ever produces a `{response, emotion}` pair
for display — it has no tool-calling ability and cannot create, modify, or
delete anything. This means a bad or hallucinated LLM reply is, at worst,
a wrong sentence in the chat window, never a wrong reminder.

---

## 6. Reminders, tasks & timers

All three follow the same shape: a `manager.py` doing CRUD against
SQLite, an optional `scheduler.py` (reminders/timers only — tasks have no
due date, so nothing to poll for) checking for due items on a `QTimer`,
and a `notifications.py` that turns "this became due" into a character
reaction + sound + speech bubble + OS notification.

```text
data/mochi.db
├── reminders   id, title, due_at, repeat_rule, status, created_at, completed_at
├── tasks       id, title, status ('open'|'done'|'cancelled'), created_at, completed_at
├── timers      id, label, duration_seconds, started_at, due_at, status, notified_at
└── relationship  id (single row), interaction_count, first_seen, last_seen
```

- **Reminders** poll every ~15s, support `DAILY`/`WEEKLY`/`MONTHLY`
  repeat rules, and catch up on anything missed while the app was closed.
  If a reminder is still `pending` several minutes after being surfaced,
  the notifier checks back once and reacts annoyed (`CharacterState.ANGRY`).
- **Tasks** have no scheduler — they're a plain open/done checklist,
  toggled from the UI or via chat ("remember that I need to...").
- **Timers** poll every ~1s (a countdown finishing is something the user
  is actively waiting on, unlike a reminder) and persist across restarts.

All three are reachable both by chatting in natural language and through
a dedicated window from the right-click menu — the chat path and the
manual-UI path call the exact same manager functions underneath.

---

## 7. Error handling philosophy

Each subsystem raises a specific exception from `app/core/exceptions.py`.
Callers degrade instead of crashing:

| Failure                      | Fallback behavior                                    |
|-------------------------------|-------------------------------------------------------|
| Ollama not running/unreachable | Chat still answers, using the rule-based canned reply |
| A tool call fails validation   | Chat shows "Hmm, I couldn't do that: ..." and stops   |
| Any unexpected error in chat   | Chat shows a generic apology instead of crashing      |
| Tray/notification icon missing | Falls back to a built-in Qt icon                      |

---

## 8. Testing

`tests/` covers every subsystem below the UI layer without needing a
running Qt app window or a live Ollama server:

- `test_state_machine.py`, `test_pixel_face.py` — expression table
  coverage (every state actually renders without crashing) and emotion→
  animation wiring
- `test_behavior.py` — the inactivity-tiered autonomous behavior timing
- `test_intent.py` — the rule-based chat matcher, including regression
  tests for real bugs caught during manual QA (e.g. a keyword matching as
  a substring inside an unrelated word)
- `test_llm.py` — the local-LLM backend against a fake local HTTP server,
  including malformed/code-fence-wrapped output and the no-Ollama-running
  fallback path
- `test_chat_engine.py`, `test_relationship.py` — end-to-end chat
  handling and familiarity progression
- `test_reminder_manager.py` / `test_reminder_tools.py` /
  `test_reminder_notifications.py`, and the equivalent `test_task_*` /
  `test_timer_*` files — CRUD, repeat rules, the JSON tool wrappers, and
  due/ignored-reminder reactions
- `test_movement.py` — screen-bounds math used when dragging the window

`pixel_face.py` and `chat_window.py` tests that need a real `QWidget` use
a session-scoped `qapp` fixture (`tests/conftest.py`) running Qt in
offscreen mode.
