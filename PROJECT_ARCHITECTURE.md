# Mochi — Project Architecture

**Current status: V1.0 — Correct Assistant.** See
[`docs/ROADMAP.md`](./docs/ROADMAP.md) for the full versioned roadmap and
what changes at each later version. Everything in this document describes
the codebase as it exists in V1.0.

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
│   ├── llm.py                    Optional local LLM backend - talks to
│                                 Ollama's HTTP API directly (stdlib only,
│                                 no extra dependency); every prompt
│                                 includes the actual current local date/
│                                 time so "what time is it"/relative
│                                 phrasing never has to be guessed; 30s
│                                 bounded timeout (generous enough for a
│                                 cold local model); raises LLMUnavailable
│                                 on any failure so the caller can fall
│                                 back gracefully; also exposes
│                                 phrase_data_answer() - phrases an
│                                 already-fetched, ground-truth DB result
│                                 (see db_glossary.py below) rather than
│                                 answering freely
│   └── db_glossary.py            Synonym glossary + live-schema reader for
│                                 natural-language "what's done"/"what's
│                                 remaining" questions - maps words like
│                                 "remaining"/"left"/"done"/"history" onto
│                                 a plain (entity, status) QueryPlan;
│                                 chat_engine.py runs that through the
│                                 existing safe manager functions (never
│                                 raw SQL) and only hands llm.py the
│                                 real result to phrase, not the query
│                                 itself
│
├── memory/                     SQLite access layer
│   ├── database.py              Connection management + schema (reminders,
│   │                            tasks, timers + their `_done` archive
│   │                            tables, relationship, app_settings) plus
│   │                            archive_row()/restore_row()/list_done() -
│   │                            the move-to-archive machinery every
│   │                            manager's complete/cancel path uses (§6)
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
app/ai/intent.py — rule-based (keyword/regex) match
      │
      ├─ actionable (reminder/task/timer) ─────► app/tools/*.py
      │                                            │
      │                                    schema/permission-checked,
      │                                    executes against SQLite
      │
      └─ "unknown" ─────► app/ai/semantic_intent.py — semantic (meaning-based) match
                                │                              │ Ollama (localhost)
                                │                              │
                    confident (≥0.75) ──────────────┐          │
                    guess + entities found      build_semantic_intent()
                    (app/ai/intent.py)  ─────────────┘  reuses the SAME regex
                                │                        entity parsing as the
                                │                        keyword path above
                                ▼
                    ├─ actionable ─────► app/tools/*.py  (same validated path)
                    │
                    ├─ unsure (0.50-0.75) ─► ask a clarifying question, act on nothing
                    │
                    └─ low/unavailable (<0.50, or Ollama unreachable) ─► app/ai/llm.py
                                                                              │ Ollama (localhost)
                                                                        success: {response, emotion}
                                                                        failure: canned fallback reply
```

The rule-based matcher is intentionally the *first and cheapest* filter for
triggering data changes, but it only recognizes phrasing it was explicitly
written to expect. Section "5a" below covers what happens when it finds
nothing at all - a second, semantic pass that understands paraphrases by
meaning rather than exact wording, still gated by the exact same
schema/permission-checked tool layer, never given direct write access
itself. Either way, this means a bad or hallucinated model guess is, at
worst, a wrong sentence (or a clarifying question) in the chat window,
never a wrong reminder silently created from a misread.

That said, "at worst a wrong sentence" was itself a real bug: phrasing
like "check on my aunt" or "mark it as done" (no literal "task"/
"reminder" keyword) used to fall all the way through to `unknown` and get
sent to the LLM - which, having no database access, would confidently
invent a plausible-sounding reply ("I'll remind you...", "Okay, I'll take
care of it") without anything actually being checked or changed. Two
fixes for this, in order of preference:

1. `check_on`/`complete_ambiguous`/`cancel_ambiguous` intents (see
   `CHECK_ON_TRIGGER`/`AMBIGUOUS_DONE_TRIGGER`/`AMBIGUOUS_CANCEL_TRIGGER`
   in `app/ai/intent.py`) catch the common phrasings for this *before*
   they'd ever reach `unknown`, and answer from the real DB the same way
   the keyword-requiring complete/cancel intents do.
   `cancel_ambiguous` ("cancel it" / "delete it" / "scratch that") is
   `complete_ambiguous`'s cancellation counterpart, resolving "it"
   against whatever's open across tasks, reminders, *and* running
   timers - added because it had the exact same "quietly falls through
   to a DB-blind LLM that claims success anyway" gap as the completion
   case, just for cancelling instead of completing. Deliberately
   excludes bare "never mind" - too common as a plain conversational
   dismissal to safely treat as "cancel the last thing" - only phrasing
   that names an explicit cancel action is matched.

   A closely related bug: complaint/accusation phrasing like "you forgot
   to remind me" or "did you forget to remind me" contains the literal
   substring "remind me", so it was matching `REMINDER_TRIGGER` and being
   read as a request to *create* a brand-new reminder titled after the
   complaint itself (e.g. "Got it - \"You dumb cat you forgot to\" - but
   when?"). `REMINDER_ACCUSATION_TRIGGER` is a narrow, separate pattern
   checked *before* `REMINDER_TRIGGER` specifically for this "you [never/
   didn't] remind..." / "did you forget to remind..." phrasing, routing
   it to `check_on` instead - while leaving `CHECK_ON_TRIGGER` itself at
   its original later position so legitimate creation requests like
   "remind me to check on my aunt at 7pm" still create a reminder.

   A sibling bug in the *creation* triggers rather than the
   check-status ones: reminder/timer/task creation used to be three
   independent `if REMINDER_TRIGGER.search(...)` /
   `if TIMER_TRIGGER.search(...)` / `if TASK_TRIGGER.search(...)` blocks
   checked in that fixed order, so a message containing a
   reminder-trigger phrase *anywhere* in it always won regardless of
   what else was said - "start a 10 minute timer and remind me when
   it's done" matched `REMINDER_TRIGGER` first (on "remind me") and
   asked "but when?" for a reminder instead of starting the clearly-
   requested timer at all; "add task buy milk, remind me later" would
   have hit the same problem in the other direction. Fixed by computing
   all three triggers' matches up front in `detect_intent()` and routing
   to whichever one's match *starts earliest* in the actual message text
   - the phrase the person said first is what they meant, rather than a
   fixed reminder > timer > task priority baked into the code's read
   order. See `tests/test_intent.py`'s
   `test_timer_request_is_not_shadowed_by_an_incidental_remind_me` /
   `test_task_request_is_not_shadowed_by_a_later_remind_me` /
   `test_reminder_still_wins_when_it_is_said_first` for the exact cases
   this covers (including confirming the fix isn't "timer/task always
   beat reminder" - it's genuinely position-based both ways).

2. Defense in depth for whatever still isn't caught: `app/ai/llm.py`'s
   `SYSTEM_PROMPT` explicitly forbids the model from claiming to have
   created/checked/completed/cancelled anything, telling it to say
   plainly it didn't recognize the message as a command instead.

A sibling bug in the same family: "what time is it" / "what day is it"
had no rule-based handler at all, so it fell through to the open-ended
LLM bucket - meaning if Ollama isn't installed/running (which is
explicitly optional, see section 5 above), asking Mochi the time got a
generic "not sure what you mean" or "install Ollama" message instead of
an actual answer, even though reading the system clock needs no AI
whatsoever. `TIME_QUERY_TRIGGER`/`DATE_QUERY_TRIGGER` in `app/ai/intent.py`
answer this directly from `now` (the same injectable real-clock value
every other date/time parsing in this file already uses), so it always
works with zero setup.

---

## 5a. Hybrid keyword + semantic intent understanding

Problem this fixes: `app/ai/intent.py`'s matcher only recognizes a message
if it contains one of a fixed set of literal trigger phrases ("remind me",
"set a timer", ...). A paraphrase with none of those exact words - "don't
let this slip my mind, dentist thing at 4" or "yeah that's sorted now,
thanks" - always fell all the way through to `unknown` and got sent to the
open-ended chat model, which has no database access and would just
converse about it instead of actually doing anything. Widening the keyword
list forever doesn't scale and never actually understands *meaning* - it
just adds more exact strings to match.

`app/ai/semantic_intent.py` is a second pass, only ever consulted when the
keyword pass found nothing (never overrides an actual keyword match, so it
can only recognize *more* messages, never change how an already-recognized
one is handled):

```text
detect_intent(text) == "unknown"
        │
        ▼
semantic_intent.classify(text)  ── asks Ollama to pick ONE of a fixed,
        │                          closed taxonomy (ALLOWED_INTENTS) by
        │                          MEANING, plus a confidence 0.0-1.0
        ▼
confidence ≥ 0.75 (CONFIDENCE_ACT)
        │
        ▼
build_semantic_intent(guessed_name, text)   ← app/ai/intent.py
        │
        │  reuses the EXACT SAME deterministic regex helpers the keyword
        │  path already uses (_parse_absolute_time / _parse_relative_minutes /
        │  _parse_duration_seconds / _title_from) to pull the actual time/
        │  title/duration back out of the ORIGINAL text
        ▼
a normal DetectedIntent, routed through chat_engine.py's existing
_LIST_HANDLERS / _ACTION_HANDLERS / tool-dispatch code - identical
downstream path to a keyword match, same schema/permission validation
```

This is a deliberate split, matching the roadmap's core rule ("the LLM
should reason about actions; it should not be trusted to directly perform
actions" - see `MOCHI_VERSIONED_ROADMAP.md` section 60): the model is only
ever trusted to answer "which of these ~9 fixed buckets does this message
belong to, and how sure am I" - never to invent the title, time, or
duration that actually gets written to SQLite. Those still go through the
same regex parsing that's been validated for years on the keyword path.

Confidence controls autonomy, per the roadmap's section 6 confidence-band
table (`CONFIDENCE_LOW` / `CONFIDENCE_ACT` in `semantic_intent.py`):

| Confidence | Behavior |
|---|---|
| < 0.50 | Treated exactly like a keyword miss - stays `unknown`, falls through to the open-chat LLM reply, same as before this feature existed. |
| 0.50 - 0.75 | "Soft suggestion" - Mochi asks a clarifying, rephrase-it question naming what it thinks was meant (`chat_engine._semantic_clarify_intent`), but creates/changes nothing. |
| ≥ 0.75 | Acts - but the entities still have to actually be found in the text (e.g. a real time for a reminder); if they aren't, the same `*_needs_time`/`*_needs_duration` clarifying-question shape the keyword path already uses is returned instead of guessing a default. |
| Ollama unreachable | `SemanticUnavailable` - identical to a low-confidence miss; chat degrades to exactly the pre-existing keyword-only behavior with zero setup required, same philosophy as `app/ai/llm.py`. |

`small_talk` is in the taxonomy but is never acted on regardless of
confidence - it's how the model says "I don't think this is one of the
actionable categories," and just falls through to the normal open-chat
reply.

See `tests/test_semantic_intent.py` (the model-call layer in isolation)
and `tests/test_semantic_chat_engine.py` (the routing behavior: act / ask
/ ignore, and proof a keyword match is never reached by this code at all).

---

## 5b. Crawled reference data (`app/humor/subreddit_crawler.py`)

A separate, much simpler local-storage feature: given a markdown file full
of `[title](url)` links (e.g. a curated subreddit/reference list), crawl
each link and store its page text in SQLite for later use, without ever
needing a live network call again for URLs already fetched.

```text
markdown file
      │  extract_links() - regex over [title](url) syntax
      ▼
list of (title, url)
      │  _already_crawled() - one SELECT ... WHERE url IN (...)
      ▼
skip anything already in crawled_sources ──► done, no network call made
      │
      ▼  (only genuinely new URLs reach here)
_fetch_page(url)
      ├─ reddit.com/r/xxx ──► Reddit .json endpoint: real post titles/text
      └─ anything else ─────► stdlib urllib + small dependency-free
                               HTML→text reduction (_html_to_text)
      │
      ▼
summarize_page_content(content) ── best-effort, Ollama (localhost)
      │                             success: clean summary stored too
      │                             unavailable: summary left NULL
      ▼
INSERT OR IGNORE INTO crawled_sources (..., content, summary, ...)
```

Deliberately the opposite lifecycle from `trend_cache`/`meme_cache`
(section 3's other `app/humor/` tables), which are small rolling windows
wiped and replaced on every fetch: `crawled_sources` is **append-only** -
`url` is `UNIQUE`, there is no delete/refresh function for this table at
all, and a URL that already has a row is never re-fetched (checked before
any network call, not just deduped afterward - see
`app/humor/subreddit_crawler.crawl_links`). A failed fetch (network error,
timeout, 404) is logged and skipped, never stored, so it stays eligible to
be retried on a later run - only a *successful* fetch counts as "already
stored." Run it via `python scripts/crawl_sources.py path/to/list.md`.

**Stores the link's actual content, not just the link.** A subreddit's own
HTML page is mostly an empty client-side-rendered shell over a plain GET,
so for `reddit.com` URLs the crawler instead hits Reddit's public
read-only `.json` endpoint (same no-login approach `meme_fetcher.py`
already uses) and pulls in the subreddit's real current top post
titles/text as the stored `content` - genuine content, not page chrome.
Non-Reddit URLs fall back to a small dependency-free HTML-to-text
reduction of the fetched page.

**Optional model read-through.** After the raw content is extracted, a
local Ollama model (`app/ai/llm.summarize_page_content`) gets a chance to
read it and write a clean, factual summary, stored in a separate
`summary` column right alongside the raw `content` - never in place of
it, so a bad or unavailable summarization pass can never lose the
underlying ground truth. Same fully-optional philosophy as the rest of
`app/ai/llm.py`: if Ollama isn't running, `summary` is simply left `NULL`
and the raw extracted `content` is still stored either way.

**Triggering it.** Two ways, both call the exact same
`crawl_markdown_file()`:

1. `python scripts/crawl_sources.py path/to/list.md` - manual CLI, any
   time.
2. Mochi's right-click **"Refresh trends & memes"** menu action
   (`app/character/pet.py::_RefreshTrendsWorker`) - if
   `settings.crawl_sources_path` is set (`MOCHI_CRAWL_SOURCES_PATH` in
   `.env`), the same click that refreshes `trend_fetcher`/`meme_fetcher`
   also crawls that file, off the UI thread, and the resulting speech
   bubble reports a combined count ("...plus N new page(s) crawled").
   Deliberately reuses `trend_awareness_enabled` as its gate rather than
   introducing a third on/off flag (it's the same category of
   "reaches the open internet on your behalf" feature), but a path still
   has to be explicitly configured on top of that for anything to
   actually crawl - turning trend awareness on alone does not start
   crawling anything. A crawl failure is caught and logged separately
   from the trend/meme fetch, so it can never suppress an otherwise-
   successful refresh's results (see `_RefreshTrendsWorker.run()`).

---

## 5c. Conversational reference resolution (`app/ai/conversation_state.py`)

Problem this fixes (security review "I1/I3" - the biggest remaining
intelligence gap at the time): Mochi had no memory of "the thing we were
just talking about". Creating a task and then saying "actually delete it"
fell back to the same fuzzy title search a completely fresh command uses,
which only resolves cleanly when there's exactly one open item in the
whole app - with two or more open tasks/reminders/timers around, Mochi
always asked "which one?", even immediately after creating the very thing
being referred to.

```text
handle_message(text, conversation_state=...)
        │
        ▼
complete/cancel/reschedule handler runs its normal fuzzy title search first
        │
        ├─ real title match (or a tie) ──► same as before this feature existed
        │
        └─ no title match at all ──► conversation_state.resolve(query, state, candidates)
                                            │
                                path 1: query is a bare reference
                                ("it"/"that"/"this one"/...) ──► look up
                                state's remembered entity_id among the
                                REAL, currently-valid candidates
                                            │
                                path 2: query is an ordinal ("the second
                                one") ──► look up state's remembered
                                candidate list (from the most recent
                                list_* query) at that index, then confirm
                                that id is still among the real candidates
                                            │
                                            ▼
                                found in both the remembered state AND the
                                real candidate list ──► act on it
                                            │
                                not found (stale - already completed/
                                deleted through some other path since it
                                was remembered) ──► fall through to "which
                                one?" exactly as if conversation_state
                                didn't exist - NEVER silently act on some
                                other item instead
```

Deliberately **not** the model doing entity resolution - `conversation_state`
is a small, plain dict (`{"entity_type", "entity_id", "entity_title",
"candidates"}`) threaded between `handle_message()` calls by the caller,
the exact same ownership convention `pending_action` already uses (see
section 5's `app/ui/chat_window.py` - `_conversation_state` lives and
resets alongside `_pending_action`). The model is still only ever asked
*which intent* a message maps to; this module is what lets the
deterministic layer know *which entity*, without the model ever touching
a real database id.

**Where it's written (remembered):**
- Every successful `create_reminder` / `create_task` / `start_timer` call.
- Every unambiguous complete/cancel/check_on resolution (including one
  resolved via this same module - so completing something by reference,
  then referring to it again, keeps working).
- Every `list_tasks` / `list_reminders` / `list_timers` query, as an
  ordered candidate list (`remember_candidates`) - this is what "the
  second one" resolves against, in the exact order actually shown.

**Where it's read:** `_complete_task_reaction`, `_cancel_task_reaction`,
`_complete_reminder_reaction`, `_cancel_reminder_reaction`,
`_cancel_timer_reaction`, `_complete_ambiguous_reaction`,
`_cancel_ambiguous_reaction`, and the new `_reschedule_reference_reaction`
(below) - always as a fallback *after* the normal fuzzy title search
finds nothing, never overriding a real title match.

**Rescheduling by reference** ("make it 8" / "change it to 9am" / "move
that in 20 minutes") is a new capability this made possible - previously
there was no way to adjust a reminder/task's time without repeating its
title verbatim in a brand-new `remind me...` command. `RESCHEDULE_TRIGGER`
in `app/ai/intent.py` recognizes the phrasing and reuses the exact same
`_parse_absolute_time` / `_parse_bare_time` / `_parse_relative_minutes`
deterministic time parsing the creation triggers already use;
`_reschedule_reference_reaction` in `chat_engine.py` resolves "it"/"that"
against `conversation_state` and calls `update_reminder`/`set_due_date` -
never guesses at a target, and refuses (asking instead) if
`conversation_state` has nothing to point at, or if the referenced entity
is no longer real.

**Why this is safe to carry forward across unrelated turns, unlike
`pending_action`:** `conversation_state` is purely referential memory - it
never itself performs a write. The actual mutation always goes through
the same deterministic, schema-validated manager functions every other
action does, and only ever succeeds when the remembered id still points
at something real *right now*. So unlike a stale calendar confirmation
(which is itself an unconfirmed write waiting to fire), an unrelated
message in between doesn't need to expire this - at worst a stale
reference just fails closed and asks, exactly like it always did before.

See `tests/test_conversation_state.py` (the resolution module in
isolation) and `tests/test_conversation_state_integration.py`
(end-to-end: create → reference by pronoun/ordinal → correct item
acted on, plus the stale-reference-fails-closed case).

## 5d. Multi-target conversational selection

Problem this fixes (conversational-issues report P0 - "Add Multi-Target
Conversational Entity Resolution"): section 5c above only ever resolved a
reference to ONE entity. A real request like "three of them check as
done" or "mark all of them as done" after a `list_tasks` query had no
path to act on more than one item at once.

`conversation_state.py` gains `parse_selection()`, `resolve_selection()`,
and `resolve_selection_typed()`, sitting alongside `resolve()`/
`resolve_typed()` from 5c:

```text
"three of them" / "all of them" / "both" / "the first three" /
"the last two"
        │
        ▼
parse_selection(query) - which shape is this?
        │
        ├─ not a multi-target reference at all ──► None (falls through
        │  to the existing singular resolve()/resolve_typed() path,
        │  completely unaffected - "the first one" still resolves to
        │  exactly one entity, never a one-item selection)
        │
        └─ recognized shape ──► resolve_selection()/resolve_selection_typed()
              applies it against state's remembered candidate list
              (from the most recent list_* query), restricted to
              candidates that are STILL real right now
                    │
                    ├─ quantity/range fits the remembered list AND at
                    │  least one remembered candidate is still real
                    │  ──► the matched real entities, in remembered order
                    │
                    └─ doesn't fit (asked for more than were ever shown,
                       or "both" against anything other than exactly two
                       remembered candidates) ──► None, never clamped or
                       guessed - falls through to "which one?" the same
                       way an unresolvable singular reference does
```

`MULTI_REFERENCE_SRC` (a regex *source string*, not compiled) is defined
once in `conversation_state.py` and imported by `app/ai/intent.py` to
extend `AMBIGUOUS_DONE_TRIGGER`/`AMBIGUOUS_CANCEL_TRIGGER` - one
definition shared by the trigger (does this message even mention a
multi-target reference?) and the resolver (what does that reference
actually pick out?), so they can never recognize different phrasing.

Execution goes through a new `_multi_action_reaction()` in
`chat_engine.py`, wired into all 5 single-type reaction handlers plus
both ambiguous handlers, always checked *before* the existing fuzzy
title search and singular resolution (so it never intercepts a real
title match or a singular "it"/"that"/ordinal reference). It runs the
real complete/cancel manager call against every resolved entity
individually and reports exactly how many actually succeeded - if one
item in the middle of a batch fails, the response says so rather than
claiming the whole batch went through.

See `tests/test_multi_target_selection.py` for the end-to-end cases
(all/both/first-N/last-N/N-of-them, out-of-range quantities refusing to
guess, and a stale candidate being skipped rather than reprocessed).

## 5e. Timer purpose preservation

Problem this fixes (conversational-issues report P0 - "Preserve Timer
Purpose/Label Information"): `start_timer` requests like "set 10 second
timer to remind me to pick my columns" always parsed the duration
correctly but discarded everything else, landing on the generic "Timer"
label regardless of what the person actually said the timer was for.

`_timer_label_from()` in `app/ai/intent.py` strips the duration phrase
(via a boundary-safe `_DURATION_STRIP`, kept separate from the existing
`DURATION_ONLY` used for actual duration parsing - `DURATION_ONLY`'s
alternation order can leave a stray trailing "s" behind, harmless for
extracting the number but not for extracting a label) and a small set of
filler words ("can you", "please", "set", "a", "for", "remind me to",
...), then capitalizes whatever's left. If nothing's left - e.g. "timer
for 10 minutes" - it returns `None` and the caller falls back to the
existing generic "Timer" label, so no purpose is ever invented. Wired
into both the rule-based (`detect_intent`) and semantic
(`build_semantic_intent`) timer-creation paths.

See `tests/test_intent.py`'s `test_timer_preserves_stated_purpose` /
`test_timer_without_stated_purpose_keeps_generic_label` /
`test_timer_purpose_survives_plural_duration_unit`.



All three follow the same shape: a `manager.py` doing CRUD against
SQLite, an optional `scheduler.py` (reminders/timers only — tasks have no
due date, so nothing to poll for) checking for due items on a `QTimer`,
and a `notifications.py` that turns "this became due" into a character
reaction + sound + speech bubble + OS notification.

```text
data/mochi.db
├── reminders       id, title, due_at, repeat_rule, status, created_at, completed_at, notified_at
├── reminders_done  same columns + archived_at  (finished reminders land here - see below)
├── tasks           id, title, status ('open'|'done'|'cancelled'), created_at, completed_at, due_at (nullable)
├── tasks_done      same columns + archived_at  (finished tasks land here)
├── timers          id, label, duration_seconds, started_at, due_at, status, notified_at
├── timers_done     same columns + archived_at  (finished timers land here)
└── relationship    id (single row), interaction_count, first_seen, last_seen
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
  Chat can also list active ones ("what timers do I have?") - see
  `LIST_TIMERS_TRIGGER` in `app/ai/intent.py`.
- **`check_on <query>`** and **`mark it as done` / "that's done" (no
  keyword)** search across *both* tasks and reminders by fuzzy title
  match rather than requiring the caller to know which store something
  lives in - see `_check_on_reaction`/`_complete_ambiguous_reaction` in
  `chat_engine.py`. `complete_ambiguous` only auto-resolves when exactly
  one open task+reminder exists across both; otherwise it lists them and
  asks which one, the same "ask rather than guess" rule the
  keyword-requiring complete/cancel intents already follow.

### Active vs. archived: where finished items go

Completing/cancelling a reminder or task, or a timer firing its due
notification, doesn't just flip `status` in place — the row is moved
wholesale out of its active table into a parallel `_done` archive table
(`app/memory/database.py`'s `archive_row()`), stamped with `archived_at`.
`restore_row()` is the inverse, used by `reopen_task()`. This is a
deliberate product rule, not an implementation detail: **the active
tables only ever hold things still outstanding**, so any "what's
remaining" read — `list_tasks()`, `list_reminders()`, `list_active_timers()`
— never has to filter finished rows out by hand; it's just everything
still in the table. `get_task_any()` / `get_reminder_any()` (check active,
then archive) exist for the few call sites that need to look up an item
regardless of which side of that split it's currently on (e.g. the task
checklist UI toggling a recently-completed item back open, or deleting an
already-archived record for good).

### Asking about finished items: the synonym glossary

`app/ai/db_glossary.py` is a small, deliberately "universal" synonym
table mapping the words someone might actually use — "remaining" / "left"
/ "pending" / "open" all mean *active*; "done" / "completed" / "finished"
/ "history" / "archive" all mean the *archive*; "cancelled" / "canceled"
/ "called off"; "task" / "todo" / "chore" / "assignment" all mean the
same table, and so on for reminders/timers — onto a plain
`QueryPlan(entity, status)`. `LIST_DONE_TRIGGER` in `intent.py` routes
matching chat messages ("what tasks are done", "show completed
reminders") to `_query_done_reaction()` in `chat_engine.py`, which:

1. Resolves the message to a `QueryPlan` via the glossary.
2. Reads the real archive (or active/all) table through the *same*
   already-safe manager functions everything else uses —
   `list_archived_tasks()`, `list_archived_reminders()`,
   `list_archived_timers()` — never a hand-built SQL string.
3. Builds a short, deterministic plain-text summary of the real result
   ("facts").
4. Passes those facts to `app/ai/llm.py`'s `phrase_data_answer()` so a
   local model, if one's running, can phrase the answer naturally -
   explicitly instructed to state only what's in the facts and never
   invent a title/count/time beyond them. Falls back to the same facts
   formatted plainly in Python if no LLM is available.

This is a deliberate departure from "just let the LLM write the SQL":
principle 3 above ("deterministic where it matters") already rules that
out for anything touching real data, and a small local model generating
live SQL against someone's own database is exactly that kind of
untrusted "LLM performs the action" step, with real injection/
hallucination risk on top. The glossary keeps the actual query
deterministic and safe while still answering natural-language questions
about it — the LLM only ever touches the *wording* of an answer that's
already been computed correctly.

All three stores are handled entirely through chat — there's deliberately no
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

**Test isolation from ambient machine state.** Two tests used to pass or
fail depending on facts about the machine running them rather than the
code under test - a real bug in the tests themselves, not just flaky CI:

- `test_ask_raises_llmunavailable_when_unreachable` (`test_llm.py`)
  originally just called `ask()` and relied on there being no real
  Ollama server reachable in the test environment. On a machine that
  *does* have Ollama installed and running (a completely normal
  development setup for this project), the call would actually succeed
  and the test would fail with "DID NOT RAISE LLMUnavailable" - not
  because anything was broken, but because the test's premise depended
  on an absence it never controlled for. Fixed by monkeypatching
  `urllib.request.urlopen` to always raise, so the test's result depends
  only on `app/ai/llm.py`'s error handling, never on whether Ollama
  happens to be running on whatever machine runs the suite.
- `test_start_is_a_safe_noop_off_windows` (`test_lock_watcher.py`)
  asserted the off-Windows no-op branch of `LockWatcher.start()` without
  controlling which branch actually runs - `_IS_WINDOWS` is computed
  once from the real `sys.platform` at import time, so on an actual
  Windows machine `start()` correctly takes the *other* branch (it
  really does poll on Windows) and the assertion fails, guaranteed,
  every time, on the OS this project primarily targets. Fixed by
  monkeypatching the module's `_IS_WINDOWS` flag directly instead of
  trusting the host OS, and added a companion
  `test_start_actually_polls_on_windows` so both branches are always
  exercised regardless of which platform the suite happens to run on.

**Windows temp-directory permission errors.** Separately from the two
tests above, a Windows-only failure mode was reported where the *entire*
suite failed at collection with a wall of
`PermissionError: [WinError 5] Access is denied:
'C:\Users\<user>\AppData\Local\Temp\pytest-of-<user>'` - before a single
test body even ran. `tmp_path`'s default base lives under the OS temp
directory, a location pytest itself doesn't fully own the ACLs of; if
that directory was ever touched by a different process/user context (a
previous run under another account, antivirus, OneDrive, etc.), every
later run can fail trying to `os.scandir()` it. `pytest.ini` now sets
`addopts = --basetemp=.pytest_tmp`, pointing all `tmp_path`/`tmp_path_factory`
usage at a plain folder inside the repo that pytest creates and fully
owns itself, sidestepping the ACL problem entirely rather than asking
users to manually fix permissions on a system directory. `.pytest_tmp/`
is gitignored and always safe to delete.

There is no CI pipeline in this repo. Before opening a PR, run
`ruff check .` locally (a narrow pyflakes/syntax-error rule set - see
`pyproject.toml`'s `[tool.ruff]` comment for why it's not a full style
enforcer) plus this entire test suite, ideally once with
`requirements-calendar.txt` installed so the "optional Google libraries
present" path gets exercised too, and once without. No live network
calls or real Google/Ollama services are needed for any of it.

---

## 8a. Security review

A source-level security review was done covering everything in `app/`;
findings, exact fixes, and what was checked and found *not* to be a
problem are all recorded permanently in
[`docs/VULNERABILITIES.md`](../docs/VULNERABILITIES.md) - kept even
after every finding is fixed, as a running record rather than a
throwaway checklist. Summary of what was fixed:

1. **SQL identifier interpolation** in the schema migration runner
   (`app/memory/database.py`) - table/column names were f-string'd into
   `PRAGMA`/`ALTER TABLE` rather than parameter-bound (SQLite's `?`
   binding only covers values, never identifiers). Not exploitable today
   since the migration list is a hardcoded literal, but now guarded by a
   strict identifier allow-list regardless, so it can't become one.
2. **OAuth token file permissions** (`app/calendar/google_calendar.py`)
   - `config/token.json` (a live Google access + refresh token) is now
   `chmod 0600`'d immediately after every write, and `config/` itself is
   restricted the same way in `Settings.ensure_directories()`.
3. **Config-driven path traversal** (`app/core/config.py`) - the
   `MOCHI_GOOGLE_CLIENT_SECRET_FILENAME`/`MOCHI_GOOGLE_TOKEN_FILENAME`
   `.env` values are now validated to be bare filenames before being
   joined onto `config_dir`, since `Path.__truediv__` doesn't sandbox
   `..` segments or (worse) absolute paths on the right-hand side.

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
