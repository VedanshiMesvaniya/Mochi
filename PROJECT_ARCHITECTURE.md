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
│   │                           reactions and the lock-screen watcher into
│   │                           visible state changes
│   ├── pixel_face.py           Programmatic EMO-style face renderer - eyes,
│   │                           mouth, brows, ears, whiskers, blink/pulse/
│   │                           talk-cycle animation, cursor-following pupils,
│   │                           a spring-physics smoothing layer so expression
│   │                           and color changes ease into place instead of
│   │                           snapping, a cartoon multi-glyph "Zzz" while
│   │                           asleep, and a few dedicated draw paths layered
│   │                           on top of the usual recolor: angry's sharply
│   │                           furrowed frown brows, confused's floating "?",
│   │                           shy's closed upward-curved eyes, sad's small
│   │                           falling tear, excited's twinkling sparkle, and
│   │                           alert's six-phase detect/flash/peak/flash/
│   │                           return pulse (with a small vibration at its
│   │                           peak, held long enough via pet.py's
│   │                           REACTION_HOLD_MS to actually finish before
│   │                           reverting). No image assets; every expression
│   │                           is a small dataclass of numbers + flags
│   │                           (FACE_EXPRESSIONS table, 16 expressions
│   │                           total). Rendered fully antialiased at the
│   │                           widget's real resolution - "pixel" describes
│   │                           the blocky/geometric shape language (rounded-
│   │                           rect eyes, hard-edged mouths), not literal
│   │                           rasterization (an earlier revision tried
│   │                           rendering to a tiny buffer and nearest-
│   │                           neighbor-upscaling it; that just looked
│   │                           jagged and was reverted)
│   ├── theme.py                 One unified casing look (CASING) plus
│   │                            EXPRESSION_COLORS: a single canonical LED
│   │                            hex per expression (angry = crimson, happy =
│   │                            green, ...) - not user-selectable, and never
│   │                            touches expression geometry, only color
│   ├── lock_watcher.py           Windows-only (ctypes) OS lock-state polling
│   │                             for the lock-screen easter egg; injectable
│   │                             probe for testing, safe no-op elsewhere
│   ├── shake_detector.py          Plain-arithmetic gesture detector (no Qt) -
│   │                             flags a "shake" from rapid direction
│   │                             reversals in dragged cursor x-position;
│   │                             feeds the dizzy-then-angry easter egg
│   ├── state_machine.py        CharacterState + Emotion enums, event publisher
│   ├── behavior.py             Inactivity-tiered autonomous behavior (idle →
│   │                           occasional "alert"/"wink" attention ping →
│   │                           sleepy → sleep); fixed-interval QTimer, no LLM
│   │                           calls. Also exposes enter_busy()/exit_busy() -
│   │                           a full no-op mode for tick(), used while a
│   │                           chat reply is pending so the THINKING
│   │                           expression pet.py sets isn't stomped by this
│   │                           engine's own next scheduled tick a couple
│   │                           seconds later (see pet.py's _on_chat_thinking/
│   │                           _on_chat_reaction)
│   └── movement.py             Screen-bounds math used when dragging the window
│
├── ai/                         Chat brain
│   ├── intent.py                Rule-based matcher: greeting/farewell/thanks/
│   │                            compliment (mild → blush, strong → heart)/
│   │                            insult/sleepy/bored/reminder/timer/task/
│   │                            complete-or-cancel-an-existing-task/
│   │                            reminder/timer/check_on/complete_ambiguous/
│   │                            count/unknown, each mapped to a response +
│   │                            emotion + animation (+ a tool call, for
│   │                            actionable intents). The complete/cancel/
│   │                            check_on/complete_ambiguous intents don't
│   │                            fix a response text - they only extract a
│   │                            free-text query ("mark my task to call
│   │                            aunt as done" -> "call aunt", "check on
│   │                            messeging my aunt" -> "messeging my aunt";
│   │                            complete_ambiguous - "mark it as done" -
│   │                            has no keyword to extract a query from at
│   │                            all) for chat_engine.py to fuzzy-match
│   │                            against whatever's actually open/active in
│   │                            the DB
│   ├── chat_engine.py            Orchestrates a message end-to-end: runs
│   │                            intent detection, executes any tool call
│   │                            (with validation), fuzzy-matches
│   │                            complete/cancel intents against the real
│   │                            task/reminder/timer tables (asks rather
│   │                            than guessing when nothing/multiple
│   │                            things match), falls through to the local
│   │                            LLM for "unknown" messages, records the
│   │                            interaction for familiarity tracking
│   └── llm.py                    Optional local LLM backend - talks to
│                                 Ollama's HTTP API directly (stdlib only,
│                                 no extra dependency); every prompt
│                                 includes the actual current local date/
│                                 time so "what time is it"/relative
│                                 phrasing never has to be guessed; 30s
│                                 bounded timeout (generous enough for a
│                                 cold local model);
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
│                                 persisted preferences
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
├── calendar/                   Google Calendar integration - read-only,
│   └── google_calendar.py       opt-in (spec §22/23, V3). OAuth "installed
│                                 app" flow via google-auth-oauthlib,
│                                 read-only `calendar.readonly` scope,
│                                 cached token at config/token.json. No
│                                 scheduler/polling - purely on-demand,
│                                 queried straight from chat. Optional
│                                 client libraries are imported lazily
│                                 (never at module import time) so nothing
│                                 else in the app pays for their absence.
│
├── tools/                      JSON-in/JSON-out functions the intent layer
│   ├── reminder_tools.py        calls to actually create/modify data -
│   ├── task_tools.py            this is the validation boundary between
│   ├── timer_tools.py           "the chat layer said so" and "it happened"
│   └── calendar_tools.py         (Google Calendar: read/connect/disconnect
│                                 + confirmed writes - create/update/delete
│                                 events, all gated by a required
│                                 `confirmed=True` - see §9)
│
└── ui/                          Qt windows/dialogs
    ├── base_window.py            Shared frameless/translucent/rounded dialog
    │                             base (frosted-glass style, draggable,
    │                             macOS-style close/minimize/pin dots)
    ├── chat_window.py             The chat popup; runs the LLM call on a
    │                             background ChatWorker(QThread) so a slow
    │                             reply never freezes the UI
    ├── reminder_window.py          Create/list/complete/snooze/delete reminders
    ├── task_window.py              Add (with optional deadline)/toggle-done/delete tasks
    ├── timer_window.py             Start/view/extend/cancel timers
    └── tray.py                     System tray icon + menu
```

### Dependency direction

`core` depends on nothing else in the app. `character`, `memory`,
`reminders`, `tasks`, `timers`, `calendar` depend only on `core`. `tools`
depends on `core` + the relevant subsystem (`reminders`/`tasks`/`timers`/
`calendar`). `ai` depends on `core`, `memory`, `tools`, and `calendar`
(for its own read-only chat handlers — see §9), and calls into
`character`'s state/emotion types to describe a reaction — it never
imports Qt directly. `ui` and `main.py` are the only layers allowed to
wire multiple subsystems together.

```text
core  ←  character, memory, reminders, tasks, timers, calendar, ai
core + memory  ←  reminders, tasks, timers
core  ←  calendar
core + memory + reminders/tasks/timers/calendar  ←  tools
core + memory + tools + calendar + character(types only)  ←  ai
core + character + ai + reminders + tasks + timers + calendar  ←  ui, main.py
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

That said, "at worst a wrong sentence" was itself a real bug: phrasing
like "check on my aunt" or "mark it as done" (no literal "task"/
"reminder" keyword) used to fall all the way through to `unknown` and get
sent to the LLM - which, having no database access, would confidently
invent a plausible-sounding reply ("I'll remind you...", "Okay, I'll take
care of it") without anything actually being checked or changed. Two
fixes for this, in order of preference:

1. `check_on`/`complete_ambiguous` intents (see `CHECK_ON_TRIGGER`/
   `AMBIGUOUS_DONE_TRIGGER` in `app/ai/intent.py`) catch the common
   phrasings for this *before* they'd ever reach `unknown`, and answer
   from the real DB the same way the keyword-requiring complete/cancel
   intents do.
2. Defense in depth for whatever still isn't caught: `app/ai/llm.py`'s
   `SYSTEM_PROMPT` explicitly forbids the model from claiming to have
   created/checked/completed/cancelled anything, telling it to say
   plainly it didn't recognize the message as a command instead.

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
├── tasks       id, title, status ('open'|'done'|'cancelled'), created_at, completed_at, due_at (nullable)
├── timers      id, label, duration_seconds, started_at, due_at, status, notified_at
└── relationship  id (single row), interaction_count, first_seen, last_seen
```

- **Reminders** poll every ~15s, support `DAILY`/`WEEKLY`/`MONTHLY`
  repeat rules, and catch up on anything missed while the app was closed.
  If a reminder is still `pending` several minutes after being surfaced,
  the notifier checks back once and reacts annoyed (`CharacterState.ANGRY`).
- **Tasks** have no scheduler — they're a plain open/done checklist,
  created and toggled through chat ("remember that I need to...", "add
  task buy milk", "mark my task to call aunt as done"). `due_at`
  is optional and purely informational: it changes `list_tasks()`
  ordering (dated tasks sort first, soonest due first, ahead of undated
  ones) but never triggers a notification the way a reminder does — if
  you want an actual alert, that's what reminders are for. `due_at` was
  added after the `tasks` table already shipped, so `database.py` runs a
  small idempotent `ALTER TABLE ... ADD COLUMN` migration (guarded by a
  `PRAGMA table_info` check) on startup for anyone with an existing
  `data/mochi.db` from before the column existed.
- **Timers** poll every ~1s (a countdown finishing is something the user
  is actively waiting on, unlike a reminder) and persist across restarts.
- **`check_on <query>`** and **`mark it as done` / "that's done" (no
  keyword)** search across *both* tasks and reminders by fuzzy title
  match rather than requiring the caller to know which store something
  lives in - see `_check_on_reaction`/`_complete_ambiguous_reaction` in
  `chat_engine.py`. `complete_ambiguous` only auto-resolves when exactly
  one open task+reminder exists across both; otherwise it lists them and
  asks which one, the same "ask rather than guess" rule the
  keyword-requiring complete/cancel intents already follow.

All three are handled entirely through chat — there's deliberately no
right-click menu item for any of them (the underlying `app/ui/
reminder_window.py` / `task_window.py` / `timer_window.py` classes still
exist and are tested, just not wired into the UI, in case a future
feature wants to reuse them). The alternative to a strict chat-first
interface is the user filling in a form for something a companion app is
supposed to just understand when asked, which defeats the point of
Mochi. This means the regex triggers in `app/ai/intent.py`
(`TASK_TRIGGER`/`REMINDER_TRIGGER`/`TIMER_TRIGGER`, etc.) are the whole
interface and need to cover realistic everyday phrasing, not just one
canonical form per action — and `chat_engine.py` logs every message's
detected intent/tool/args plus each tool call's outcome, so a phrasing
gap shows up in the log instead of silently doing nothing.

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
- `test_google_calendar.py` / `test_calendar_tools.py` — OAuth/token
  state handling and event listing/creation/deletion against a fake
  `googleapiclient` service object (no real Google API/network call is
  ever made in tests). These mock `google_calendar._import_google_libraries()`
  directly rather than the real Google client libraries, so they run
  identically whether or not `requirements-calendar.txt` is installed -
  a contributor without those optional packages still gets full
  coverage locally.
- `test_movement.py` — screen-bounds math used when dragging the window

`pixel_face.py` and `chat_window.py` tests that need a real `QWidget` use
a session-scoped `qapp` fixture (`tests/conftest.py`) running Qt in
offscreen mode.

There is no CI pipeline in this repo. Before opening a PR, run
`ruff check .` locally (a narrow pyflakes/syntax-error rule set - see
`pyproject.toml`'s `[tool.ruff]` comment for why it's not a full style
enforcer) plus this entire test suite, ideally once with
`requirements-calendar.txt` installed so the "optional Google libraries
present" path gets exercised too, and once without. No live network
calls or real Google/Ollama services are needed for any of it.

---

## 9. Calendar: Google Calendar (V3 read + V4 confirmed writes)

```text
"what's on my calendar today?"
      │
      ▼
app/ai/intent.py — CALENDAR_TODAY_TRIGGER (deterministic, no LLM)
      │
      ▼
app/ai/chat_engine.py — _calendar_today_reaction()
      │
      ▼
app/calendar/google_calendar.py — get_today_events()
      │
      ├─ not enabled/configured ──► GoogleCalendarNotConfigured
      ├─ not connected yet ───────► GoogleCalendarNotConnected
      └─ configured + connected ──► loads cached OAuth token
                                       (config/token.json), refreshing
                                       it if expired, then calls
                                       Google's Calendar API
```

Same "deterministic first" principle as §5: every calendar query/action
Mochi can perform (today/tomorrow/upcoming/search, connect, disconnect,
create, cancel) is matched by a fixed regex in `app/ai/intent.py`, never
inferred by the LLM — a hallucinated calendar answer is worse than a
hallucinated reminder, since it's about the user's real external
commitments. The LLM chat path can talk *about* calendars in casual
conversation, but it can never be the thing that actually reads or
touches one.

Three things make read access (V3) safe to ship as its own smaller step:

1. **Narrowest possible scope by default.** Only `calendar.readonly` is
   requested unless write access (below) is explicitly turned on.
2. **Optional at every layer.** `MOCHI_GOOGLE_CALENDAR_ENABLED=false` by
   default; the Google client libraries live in `requirements-calendar.txt`,
   not `requirements.txt`, and are imported lazily inside
   `google_calendar.py` (see `_import_google_libraries()`) so their
   absence never affects any other subsystem, including at import time.
3. **Specific, actionable failures.** `GoogleCalendarNotConfigured` /
   `GoogleCalendarNotConnected` (both subclasses of the existing
   `CalendarError`) each carry a message that tells the user exactly what
   to do next (enable the setting / install the package / say "connect
   my calendar"), rather than a generic error.

Connecting (`connect()`) blocks on a local OAuth callback server while
the user completes Google's consent screen in their browser, bounded by
a 5-minute timeout — safe to do off the UI thread because, per §5's
chat_window.py note, every chat message (not just LLM-fallback ones)
already runs on a background `ChatWorker` thread.

### V4: write access (create/cancel events), gated by explicit confirmation

`MOCHI_GOOGLE_CALENDAR_WRITE_ENABLED=true` widens the requested OAuth
scope from `calendar.readonly` to `calendar.events` ("view and edit
events on all your calendars" — deliberately narrower than the full
`calendar` scope, which also covers creating/deleting calendars
themselves). `google_calendar.py` tracks two capability levels (0 = none,
1 = read, 2 = read+write) and compares the *actually granted* scope on
the saved token against what the current setting requires
(`_capability_level`/`_required_level`) — so turning write access on
doesn't retroactively grant it to an already-connected read-only token;
Google enforces the real grant regardless of `.env`, and this module
mirrors that check locally so a stale-scope token fails fast with a
"reconnect with edit access" message instead of a confusing live 403.

Every write is proposed, then confirmed, then executed — never in one
step:

```text
"schedule a meeting with Devika tomorrow at 5pm"
      │
      ▼
app/ai/intent.py — CALENDAR_CREATE_TRIGGER extracts a title + time,
                    returns DetectedIntent("calendar_create_event",
                    tool_args={title, start_iso}) — no write yet
      │
      ▼
app/ai/chat_engine.py — _calendar_create_proposal() builds a
                          human-readable summary and returns it as
                          ChatReaction.pending_action, e.g.
                          {"kind": "calendar_create", "title": ...,
                           "start_iso": ...}
      │
      ▼
app/ui/chat_window.py stores pending_action, passes it back into the
*next* handle_message() call
      │
      ▼
User replies "yes"
      │
      ▼
app/ai/chat_engine.py — _classify_confirmation() recognizes it,
                          _resolve_pending_action() calls
                          app/tools/calendar_tools.create_event(...,
                          confirmed=True) — the ONLY call site in the
                          app that ever passes confirmed=True
      │
      ▼
app/calendar/google_calendar.py — create_event() actually calls
                                    Google's events().insert()
```

`app/ai/chat_engine.ChatReaction.pending_action` is how this confirmation
state survives across the two chat turns — `app/ui/chat_window.py` owns
it exactly the same way it already owns `_history` (spec's "remember
whole chat until closed"): read back into the next `handle_message()`
call, reset to `None` on window close. A reply that isn't a clear
yes/no (see `_classify_confirmation`'s exact-phrase matching) leaves the
proposal alive rather than silently dropping it, so the user can ask an
unrelated question in between and still say "yes" afterward.

Cancelling an existing event ("cancel my 5 PM meeting") goes through the
same propose-then-confirm shape, but the "propose" step first has to
*find* the right event: `google_calendar.find_event()` does a
client-side time-of-day match (Google's API has no "same time of day,
any date" filter) or an exact-title text search over the next couple of
days, and only the best match becomes the proposal.

**Confirmation is enforced at two independent layers**, not just by
chat_engine's own calling convention:

- `app/tools/calendar_tools.py`'s `create_event`/`update_event`/
  `delete_event` all require `confirmed=True` and raise
  `ConfirmationRequiredError` (see app/core/exceptions.py) otherwise —
  this holds regardless of what calls into that module, not only the
  chat flow above.
- `google_calendar.py`'s capability check (above) independently blocks
  any write attempt whose underlying token doesn't actually carry write
  scope, regardless of what the local `confirmed` flag says.

`update_event` (rescheduling/retitling an existing event) exists at the
`google_calendar`/`calendar_tools` layer with the same confirmation
requirement, but isn't yet wired to a chat trigger in `app/ai/intent.py`
— only create and cancel are reachable from chat today, matching the two
literal examples in the spec ("Mochi, add a meeting..." / "Cancel my 5
PM meeting").
