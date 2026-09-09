# 🐱 Mochi

A small, local-first desktop companion. A black rounded "screen" with cat
ears and whiskers sits on your desktop with an expressive pixel face —
no body, no walking, just a face that reacts to you. Type to it, and it
creates local reminders/tasks/timers, answers open-ended questions
through an optional local LLM, and remembers roughly how often you talk
to it.

**Status:** V1.0 (Correct Assistant) is complete, plus a first opt-in
slice of V1.1 (Web Knowledge & Context Engine) is implemented and
partially wired up. V1.0's focus is making Mochi *reliable* before
making it clever: deterministic tools for anything that touches real
data, a local LLM only for open-ended chat, never the other way around.
Full versioned roadmap: [`docs/ROADMAP.md`](./docs/ROADMAP.md)
(V1.0 -> V1.1 -> V1.2 -> V2.0 -> V2.1 -> V3.0 -> V3.1). Web Knowledge
specifics and known limitations: [`docs/ROADMAP_V1_1_WEB_KNOWLEDGE.md`](./docs/ROADMAP_V1_1_WEB_KNOWLEDGE.md).

**Privacy in one line:** nothing leaves your machine unless you turn on
an integration that needs the network. Chat's optional LLM step talks to
a locally-running Ollama, not the cloud. The one exception is the fully
opt-in Google Calendar connection, which is off by default. (Full
details in [Privacy](#privacy).)

## Contents

- [What Mochi looks like](#what-mochi-looks-like)
- [Chat](#chat)
- [Reminders, tasks & timers](#reminders-tasks--timers)
- [Chat memory & humor](#chat-memory--humor)
- [Calendar](#calendar)
- [Running it](#running-it)
- [Project layout](#project-layout)
- [Privacy](#privacy)
- [License](#license)

---

## What Mochi looks like

**The window:**
- ~180×180px, frameless, translucent.
- Dark rounded square, small triangular ears, a few whiskers, a glowing
  pixel face on top.
- "Pixel" means the deliberately blocky, geometric shape language
  (rounded-rect eyes, hard-edged mouths — LED-matrix-icon grammar), not
  literal jagged rasterization. The face is fully antialiased at the
  widget's real resolution, so edges stay smooth.
- One unified casing look — no user-selectable palette. Instead, **the
  glow color changes per expression**, like an LED status light: idle is
  calm violet, happy is emerald green, angry is deep crimson, and so on
  — easing into its new hue over a fraction of a second rather than
  cutting instantly.
- Blinks on its own, has a faint idle "breathing" glow, pupils drift
  toward your mouse cursor while idle.
- Every expression change eases and bounces into place through a small
  spring-physics layer instead of snapping instantly.
- Each reaction holds long enough to actually register — tuned per
  emotion in `REACTION_HOLD_MS` (surprised ≈2.5s, alert runs its full
  multi-second pulse, a sulk lingers past 4s). This is the main thing
  that makes it read as alive rather than a slideshow of static faces.

**Expressions (16):** idle, happy, sad, angry, confused, surprised,
thinking, sleepy, sleeping, talking, excited, alert, blush, shy, heart,
wink.

- All drawn programmatically (no image assets) — every expression is
  just numbers (eye openness, pupil offset, mouth shape, brow angle,
  color) plus a few dedicated shapes:
  - angry → sharp downward furrowed brows (steeper/thicker than sad)
  - confused → small floating "?"
  - shy → eyes close into a soft upward curve
  - sad → small falling tear
  - excited → tiny sparkle star beside each eye
  - alert → six-phase detect → flash-on → peak → flash-off → flash-on →
    return pulse, with a tiny vibration at its peak
- Chat reactions pick between these organically: a mild compliment gets
  a shy blush, "I love you" gets heart-eyes, an ignored-too-long
  attention ping alternates between an alert pulse and a playful wink.

![All 16 of Mochi's expressions](./assets/readme/expressions.png)

**LED color map** — one canonical color per expression, tuned by
arousal/valence (see `app/character/theme.py`):

| Expression | Hex | Meaning |
|---|---|---|
| Happy | `#22C55E` | joy, warmth, optimism |
| Excited | `#F97316` | energy, enthusiasm |
| Wink | `#FFEA00` | playful, cheerful mischief |
| Idle | `#C8C8D2` | neutral, resting |
| Sad | `#3B82F6` | melancholy, tears |
| Sleepy | `#C4B5FD` | twilight comfort, drowsiness |
| Sleeping | `#312E81` | deep night, stillness |
| Angry | `#E53935` | fury, danger |
| Alert | `#FFC107` | warning light, heightened awareness |
| Heart | `#EC4899` | affection, love |
| Blush | `#F8A6C0` | embarrassment |
| Shy | `#FFAB91` | modest, self-conscious |
| Confused | `#14B8A6` | disorientation, mental fog |
| Thinking | `#1E3A8A` | logic, structured analysis |
| Surprised | `#C6FF00` | sudden shock, unpredictable |
| Talking | `#22D3EE` | steady communication, flow |

**Sleep:** eyes close, a small cartoon "Zzz" floats up and fades near
the ear, looping, always inside the window's own bounds.

**Personality:** a playful kitten that wants attention.

- Left alone, it doesn't sit static: it occasionally perks up (alert or
  wink), gets sleepy, and falls asleep.
- Any real interaction wakes it back up — opening chat, clicking/
  dragging it, a reminder firing.
- You can also put it to sleep yourself from the right-click menu, which
  toggles to **"Wake up"** once it's asleep. Sleep sticks reliably
  whether triggered by boredom or by you, instead of getting silently
  undone by the very next autonomous tick (see
  `BehaviorEngine.manual_sleep` in `app/character/behavior.py`).
- Either way, it says so with a speech bubble the moment it actually
  falls asleep ("Zzz... I'm going to sleep now. Wake me up if you need
  me!") rather than just going quiet.
- Shows "thinking" the instant you send a chat message.
- Gets happy when you complete a reminder or task.
- Gets annoyed if a reminder sits ignored for a while.
- It's meant to be an easygoing companion, not a moderator: the LLM
  system prompt (`app/ai/llm.py`) explicitly tells it to treat ordinary
  topics — relationships, fictional pairings, opinions, personal
  choices — as normal conversation rather than lecturing about
  "boundaries." It still declines anything genuinely harmful; it just
  doesn't moralize about everyday, non-harmful things.

**Lock-screen easter egg (Windows only):**
- Lock your PC → Mochi closes its eyes, peeks one eye open every couple
  of seconds, closes it again.
- Unlocking wakes it up excited.
- Purely cosmetic — no password handling of any kind, it just detects
  the OS lock state (`app/character/lock_watcher.py`).
- No-ops safely on non-Windows platforms.

**Shake-it easter egg:**
- Grab Mochi and shake it (rapid back-and-forth drag) → eyes spin
  dizzily for a moment, then it gets properly annoyed (angry face +
  scolding speech bubble) before settling back down.
- Detected purely from cursor movement while dragging
  (`app/character/shake_detector.py`) — no accelerometer/OS hooks.

**Expression timing:**
- Every reaction — chat replies, task/reminder completions, unlock,
  shake — holds for a duration tuned to that specific emotion (quick
  surprise, lingering sulk) before settling back to idle, instead of
  being interrupted by the next autonomous-behavior tick a couple of
  seconds later.
- The floating speech bubble and the face expression are timed together
  so they appear and clear as one reaction.

---

## Chat

Double-click Mochi (or right-click → Chat) to open a small translucent
chat popup.

- Messages render as rounded speech bubbles — yours on the right,
  Mochi's on the left — like the floating bubble above the character
  itself.
- Bubble width is measured against the chat log's own actual on-screen
  width (not a fixed guess), so wrapping stays correct no matter how the
  window is resized or what DPI/font scale it's running at — see
  `ChatLogWidget` in `app/ui/chat_window.py`.
- Opens anchored next to wherever the character currently is (the
  character's own speech bubble does too), clamped so it always stays
  fully on-screen even when docked at a screen edge/corner.
- Stays pinned on top of other windows while open (toggle via the green
  dot), and only goes away when you actually close it.

### How a message gets handled — three layers

1. **Deterministic, local, no AI required.** Reminders, tasks, timers,
   greetings, common small talk, and `what time is it`/`what day is it`
   are recognized by a rule-based (keyword/regex) matcher and handled
   directly, reading straight from the system clock. Anything that
   creates/deletes data, or basic facts like the current time, should
   never depend on whether an optional language model happens to be
   installed and running.
2. **Semantic fallback — understands paraphrases, not just exact
   phrasing.** If the rule-based matcher finds nothing, a second local-
   model pass asks "which of a fixed, small set of intents does this
   message mean, and how sure am I" (`app/ai/semantic_intent.py`).
   - Example: "don't let this slip my mind, dentist thing at 4" has none
     of the keyword matcher's exact trigger words but is clearly a
     reminder request — this layer catches that.
   - The model only picks *which* category a message belongs to; the
     actual time/title/duration is still pulled out by the same regex
     parsing the keyword layer uses, never invented by the model.
   - Confidence decides what happens next: confident → act (through the
     same validated path as a keyword match); somewhat confident → asks
     a clarifying "did you mean...?" question; not confident (or Ollama
     isn't running) → falls through to layer 3, exactly as before.
   - Full design + confidence bands: `PROJECT_ARCHITECTURE.md` §5a.
3. **Local LLM fallback (open-ended chat).** Anything neither layer
   above recognizes goes to a locally-running [Ollama](https://ollama.com)
   model (default `qwen2.5:1.5b`, configurable), on a background thread
   so a slow reply never freezes the chat window.
   - The prompt always includes the actual current local date/time, so
     "remind me tonight" or "is it late" has real ground truth instead
     of the model guessing.
   - If Ollama isn't installed/running or the model isn't pulled, Mochi
     says so directly ("my brain's offline right now...") instead of
     the generic "not sure what you mean" line — the LLM is a nice-to-
     have on top of a fully working local app, never a requirement, but
     it shouldn't be a mystery when it's inactive.
   - The system prompt explicitly forbids claiming to have created,
     checked, completed, or cancelled a reminder/task/timer — it has no
     access to do any of that, and the deterministic layer above always
     handles real commands first. Without this, a small local model
     asked "did you check on X?" will happily improvise a confident
     "yes, I'll remind you..." — a lie the person has no way to catch
     until it quietly never happens.

**Other things chat handles:**

- **Interaction counter.** A lightweight local counter (not a learned
  model) shifts greeting/tone a little as you talk to it more — new,
  getting-to-know, familiar.
- **Live "thinking" state.** While a reply is pending, both the chat
  window and the character show it — Mochi's face holds a THINKING
  expression for the whole wait (up to ~30s), instead of the autonomous
  idle/happy cycling stomping it a couple seconds in (see
  `BehaviorEngine.enter_busy()`/`exit_busy()`).
- **Counting.** `count to 10` (or `count from 3 to 7`) actually counts it
  out with excitement, capped at 30 numbers.
- **Follow-up references** ("it", "that", "the second one"). Right after
  creating/listing a reminder/task/timer: "actually delete it", "mark
  that done", "the second one" resolves to the thing you just talked
  about, even with other items around. Also works for rescheduling:
  "remind me to call mom at 7" → "make it 8". Fully deterministic — a
  remembered pointer to a real database row, not a guess. If that thing
  was since completed/deleted another way, Mochi asks instead of acting
  on something else. (`PROJECT_ARCHITECTURE.md` §5c)
- **Multiple at once** ("all of them", "the first two", "three of
  them"). Same deterministic guarantee — if the quantity doesn't match
  what was shown, Mochi asks rather than guessing. (§5d)
- **Timer purpose.** "set 10 second timer to remind me to pick my
  columns" starts a 10-second timer labeled "Pick my columns" instead of
  a generic "Timer". (§5e)
- **Clearer clarifications.** A numbered list instead of a run-on
  sentence when Mochi can't tell which item you mean — remembered, so
  "the second one" or "both" finishes the thought, even across mixed
  types (a task and a reminder that both matched). (§5f)
- **Naming the type directly** ("that timer", "the task I just added").
  Only resolved when that kind is actually what's remembered — "that
  reminder" right after creating a timer gets asked about instead of
  guessed. (§5g)
- **Social conversation** ("did you miss me", "I'm back"). A warm,
  personality-appropriate reply flavored by familiarity tier, never a
  database action, never a claim to remember something specific that
  didn't happen. (§5h)

---

## Reminders, tasks & timers

All three are fully local (SQLite) and handled entirely through chat —
no separate right-click form. One consistent way in, rather than a
manual window duplicating what chat already does.

- **Create:** "remind me to call mom at 7pm", "set a reminder to water
  plants at 8am", "set a timer for 10 minutes", "5 minute timer", "add
  task buy milk", "remember that I need to buy milk"
- **Check:** "do I have any tasks?", "what reminders do I have?" —
  answered from the real database, never guessed by the LLM
- **Act on existing ones:** "mark my task to call aunt as done", "cancel
  my reminder to call mom", "cancel the timer" — matched against what's
  actually open/active by title (so "call aunt" matches a task titled
  "Call aunt" without an exact-string match). If nothing (or more than
  one thing) matches, Mochi asks which one you mean.

**Phrasing coverage:**
- Recognizes a wide range of everyday phrasing (`TASK_TRIGGER`/
  `REMINDER_TRIGGER`/`TIMER_TRIGGER` in `app/ai/intent.py`), including
  spelled-out numbers alongside digits — "in one minute" works like "in
  1 minute".
- Every message's detected intent is logged (`mochi.ai.chat_engine`), so
  an unmatched phrasing is visible in the log rather than silently doing
  nothing.
- If a message could match more than one of reminder/timer/task creation,
  Mochi goes with whichever trigger phrase appears *first* in what you
  typed — "start a 10 minute timer and remind me when it's done" starts
  the timer rather than getting stuck asking a reminder "but when?".

**Two phrasings that skip the literal word "task"/"reminder":**
- "check on `<something>`" reports the real status from either store
  ("Yep — 'message my aunt' is set for 19:00", or "I don't have
  anything like that saved") — never invents an answer.
- "mark it as done" / "that's done" / "I finished it" resolves "it"
  against whatever's currently open across tasks and reminders,
  auto-completing if there's exactly one match, asking if there's more
  than one. Its cancel counterpart ("cancel it" / "delete it" /
  "scratch that") works the same way across tasks, reminders, *and*
  active timers.
  - Both exist because falling through to the local LLM for phrasing
    like this used to produce a hallucinated reply ("I'll remind you...",
    "Okay, cancelled!") that sounded plausible but hadn't actually
    checked or changed anything.
  - Deliberately narrow: a bare "never mind" is *not* treated as cancel
    — it's just as often a plain conversational dismissal. Only phrasing
    that names an explicit cancel action triggers this.

**The three types:**

| Type | Behavior |
|---|---|
| **Reminders** | One-off or repeating (`DAILY`/`WEEKLY`/`MONTHLY`). A background scheduler checks for due ones and surfaces them with animation, sound, speech bubble, and OS notification. Still pending several minutes later → Mochi reacts annoyed. |
| **Tasks** | Simple open/done checklist. Due date is optional — undated tasks just sit in the list; a task with a deadline sorts to the front, soonest first. Unlike reminders, a dated task gets no notification/scheduler — it's a checklist entry with a date attached, not a timed alert. |
| **Timers** | Short countdowns that persist across restarts. Chat can list them: "what timers do I have?", "any timers running?" |

### Where finished items go

- Completing, cancelling, or a timer firing moves the record out of its
  active table (`reminders`/`tasks`/`timers`) into a matching archive
  table (`reminders_done`/`tasks_done`/`timers_done` — see
  `app/memory/database.py`'s `archive_row()`/`restore_row()`).
- Active tables only ever hold things you still need to deal with, so
  "what's remaining" is just everything still in the table — no filtering
  needed. Reopening a task (`manager.reopen_task()`) moves it back out.
- You can still ask about what's finished — "what tasks are done",
  "show completed reminders", "which timers got cancelled". Mochi
  recognizes a broad synonym glossary for this (`app/ai/db_glossary.py`:
  "remaining"/"left"/"pending" → active; "done"/"completed"/"finished"/
  "history"/"archive" → archive; "cancelled"/"canceled"/"called off";
  "task"/"todo"/"chore"/"assignment" → same table).
- Reads the real archive table, and — if a local LLM is available —
  phrases the answer naturally, strictly grounded in those real rows
  (never allowed to invent a title, count, or time beyond what was
  fetched; see `app/ai/llm.py`'s `phrase_data_answer()`). No LLM →
  same real data, phrased plainly instead.
- The query itself is never handed to the LLM to write — see that
  module's docstring for why a small local model generating live SQL
  against your own database is exactly the "LLM performs the action"
  step Mochi's design rule (spec §1) exists to prevent.

---

## Chat memory & humor

- Each chat window remembers the whole conversation for as long as it
  stays open — every LLM-fallback reply gets the recent conversation as
  context, not just your latest message in isolation.
- Closing the chat window clears that session; opening it again starts
  fresh.

**Jokes (on by default):**
- Once in a while, if Mochi's been idle long enough to get bored, it
  cracks a joke unprompted.
- Fetches a fresh one from a small no-auth joke API by default, falling
  back to a built-in offline list if unreachable/disabled
  (`MOCHI_HUMOR_ENABLED=false` in `.env` for a fully offline Mochi).

**Trend awareness (off by default):** `MOCHI_TREND_AWARENESS_ENABLED=true`
- Pulls two things on a slow background timer: general headlines
  (`app/humor/trend_fetcher.py`) and real current meme post premises from
  general-audience meme subreddits (`app/humor/meme_fetcher.py`, no
  login/API key needed).
- Both get reduced to a short paraphrased label/premise before caching —
  Mochi never stores or repeats the actual headline/caption text, and
  never fetches meme images.
- LLM chat replies may riff on a cached one in its own voice if it
  naturally fits, preferring the meme premise over a generic headline
  when both are available.
- Talks to the open internet (Google News' RSS feed + Reddit's public
  JSON endpoints) — why it's opt-in rather than on by default.

### Crawling a reference list into permanent local storage

A different job from the two rolling caches above:
`app/humor/subreddit_crawler.py` takes a markdown file full of
`[title](url)` links (a curated subreddit list, a reference page, etc.),
fetches the actual **content** behind each link once, and keeps it
permanently in a dedicated `crawled_sources` SQLite table.

Run it manually:

```bash
python scripts/crawl_sources.py path/to/list.md
```

Or wire it into the right-click **"Refresh trends & memes"** menu action
so it runs alongside the trend/meme fetches, on the same click:
- Set `MOCHI_CRAWL_SOURCES_PATH` in `.env` to the markdown file's path
  (optionally `MOCHI_CRAWL_SOURCE_LIST_NAME` to label rows something
  other than the file's own name).
- Left unset (the default), the menu action behaves exactly as before
  and never touches the crawler. This reuses trend-awareness's opt-in
  gate (`MOCHI_TREND_AWARENESS_ENABLED`), but only actually crawls once
  a source file is configured on top of that.
- When it runs, the speech bubble reports how many new pages it found
  (e.g. *"All caught up! Got 3 trend(s) and 2 meme(s) fresh, plus 4 new
  page(s) crawled."*).

Details worth knowing:
- A subreddit's own page is mostly an empty JS-rendered shell, so
  `reddit.com` links go through Reddit's public read-only `.json`
  endpoint instead (no login needed) and store its actual current top
  post titles/text. Other links get plain HTML-to-text extraction.
- If a local Ollama model is running, it also gets a pass at reading
  that raw content and writing a clean summary, stored alongside it in a
  `summary` column — purely additive, never replacing the raw text, and
  skipped (left `NULL`) if no model is available.
- **Append-only, unlike the rolling caches above** — once a URL is
  crawled successfully, it's kept forever and never re-fetched. Running
  the command again is always safe and cheap: every already-stored URL
  is skipped before any network call. A failed fetch (offline, timeout,
  404) is logged and not stored, so it's still eligible on a later run.

### Web knowledge (V1.1, opt-in)

Separately again, and **off by default**
(`MOCHI_WEB_KNOWLEDGE_ENABLED=true` in `.env`), Mochi can keep a small,
freshness-aware cache of evidence from a fixed set of sources (currently
one news RSS feed and one subreddit) and reference it when a chat
message looks like it's asking about something current, such as "what's
trending today" or "what's the latest on X," instead of only its
general/local knowledge. Unlike the trend/meme flavor cache above, this
evidence keeps full provenance (source URL, published/retrieved/
last-verified timestamps, authority, freshness label, confidence) and is
meant to actually ground the answer with a real excerpt, not just season
its tone with a headline.

Fetching happens automatically in the background (`KnowledgeScheduler`,
wired into `app/main.py`, wakes on `MOCHI_WEB_KNOWLEDGE_FETCH_INTERVAL_HOURS`
and checks which sources are actually due per their own frequency), or on
a manual refresh (the same right-click "Refresh trends & memes" action,
or `python scripts/run_knowledge_ingestion.py`). Either way it never runs
synchronously while you're mid-conversation, so chat stays just as fast
whether this is on or off. If a page at an already-stored URL comes back
with different content on a later fetch, the existing entry is updated in
place (its revision count goes up) rather than the old content being kept
forever. Each cached item ages out on its own schedule (a trending Reddit
post expires in a day; a documentation-style item doesn't), and older or
lower-authority evidence is ranked below fresher, more authoritative
evidence rather than presented as equally certain. See
`docs/ROADMAP_V1_1_WEB_KNOWLEDGE.md` for the full design, known
limitations, and what's still deferred, and `PROJECT_ARCHITECTURE.md`
section 5i for the implementation.

---

## Calendar

Say *"what's on my calendar today?"*, *"anything tomorrow?"*, or *"what's
coming up?"* and Mochi answers from your real Google Calendar. This is
the one integration that's off by default and reaches an actual external
service rather than your own machine — entirely opt-in.

**Setup:**

1. `pip install -r requirements-calendar.txt`
2. In [Google Cloud Console](https://console.cloud.google.com/), create
   an OAuth client ID of type **Desktop app** and download its client
   secret JSON. Save it as `config/google_credentials.json`.
3. Set `MOCHI_GOOGLE_CALENDAR_ENABLED=true` in `.env`.
4. Say **"connect my calendar"** to Mochi. A browser window opens for a
   one-time Google consent screen; the resulting token is cached locally
   at `config/token.json` so this only happens once.
   - **"disconnect my calendar"** deletes that local token (does *not*
     revoke the grant on your Google account — do that from
     [Google's own permissions page](https://myaccount.google.com/permissions)
     to fully revoke it).

**Read vs. write access:**
- By default only the narrow `calendar.readonly` scope is requested —
  Mochi cannot create, edit, or delete anything on your calendar.
- Set `MOCHI_GOOGLE_CALENDAR_WRITE_ENABLED=true` to also let Mochi create
  and cancel events (widens the scope to `calendar.events`, which still
  can't touch calendars themselves, only events on your primary one).
- Already connected in read-only mode? Say "connect my calendar" again
  so the reconnect grants the wider permission — Google enforces
  whatever scope was granted at consent time, regardless of `.env`.

**With write access on, nothing happens immediately** — every request is
proposed first and only acted on after you explicitly confirm:

```
You:   schedule a meeting with Devika tomorrow at 5pm
Mochi: I found this:

       Meeting with Devika
       Tue Aug 25 at 17:00

       Add it to your Google Calendar? (yes/no)
You:   yes
Mochi: Done! Added "Meeting with Devika" to your calendar.
```

```
You:   cancel my 5 PM meeting
Mochi: I found: Standup at 17:00.

       Cancel this event? (yes/no)
You:   yes
Mochi: Done! Cancelled "Standup".
```

If it's disabled, not set up yet, or the sign-in has expired/lacks the
right permission, Mochi says so directly (e.g. *"say 'connect my
calendar' to reconnect with edit access"*) instead of guessing.

---

## Google Tasks

Say *"what's on my google tasks?"* and Mochi answers from your real
Google Tasks default list. Off by default, same as Calendar, and shares
Calendar's sign-in — there is only ever **one** Google consent screen for
the whole app, never a separate one per feature.

**Setup** (on top of Calendar's setup above):

1. Do the same `pip install -r requirements-calendar.txt` and client
   secret steps as Calendar (same file, same client).
2. Set `MOCHI_GOOGLE_CALENDAR_ENABLED=true` **and**
   `MOCHI_GOOGLE_TASKS_ENABLED=true` in `.env`.
3. Say **"connect my calendar"** to Mochi. The one consent screen now
   also asks for Tasks permission, and the resulting token (still just
   `config/token.json`) covers both — no second sign-in, ever.
   - Already connected before turning Tasks on? Say "connect my
     calendar" again once to pick up the added permission.

**Read vs. write access** works the same way as Calendar's:
`MOCHI_GOOGLE_TASKS_WRITE_ENABLED=true` widens the scope so Mochi can
create, complete, and delete Google Tasks — again, only after you
explicitly confirm each one:

```
You:   add buy milk to my google tasks
Mochi: Add "buy milk" to your Google Tasks? (yes/no)
You:   yes
Mochi: Done! Added "buy milk" to your Google Tasks.
```

```
You:   complete my google task buy milk
Mochi: Mark "buy milk" as done on Google Tasks? (yes/no)
You:   yes
Mochi: Done! Marked "buy milk" as complete.
```

Every trigger phrase requires the word **"google"** before "task(s)" —
"add a task" still creates Mochi's own local to-do list, entirely
separate from this and requiring no internet or Google account. Say
"google task" specifically when you mean the synced one.

---

## Running it

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
python -m app.main
```

Chat, reminders, tasks, and timers all work out of the box, no further
setup. For richer open-ended chat replies, install
[Ollama](https://ollama.com), run `ollama pull qwen2.5:1.5b` (or your
preferred model, set via `MOCHI_LLM_MODEL` in `.env`), and make sure
Ollama is running — Mochi starts using it automatically.

### Running tests

```bash
pip install -r requirements-dev.txt
pytest
```

- Also install `requirements-calendar.txt` first if you want the
  Google Calendar-specific tests exercised against the real optional
  client libraries too (they pass either way — see
  `tests/test_google_calendar.py`).
- Test temp files are created under a repo-local `.pytest_tmp/` (see
  `pytest.ini`'s `--basetemp`, gitignored) rather than the OS default
  temp directory. On Windows, `%TEMP%\pytest-of-<user>` isn't always
  fully owned by the account running tests (a previous run under
  another account, antivirus, OneDrive, etc. can leave stale
  permissions), which surfaces as a wall of `PermissionError:
  [WinError 5] Access is denied` at collection time. If you hit that on
  a first run, delete `.pytest_tmp/` and re-run — always safe to
  delete, pytest recreates it.

### Linting

```bash
pip install -r requirements-dev.txt
ruff check .
```

There is no CI pipeline for this project — run `ruff check .` and the
test suite locally before opening a PR, ideally once with the optional
calendar dependencies installed and once without, so both a normal
(fully-local) install and a calendar-enabled one stay green.

---

## Project layout

See [`PROJECT_ARCHITECTURE.md`](./PROJECT_ARCHITECTURE.md) for the full
module map and data flow. Quick orientation:

```text
app/
├── main.py          application entry point / wiring
├── core/             config, logging, event bus, exceptions
├── character/        the pixel face, its behavior/personality, the window
├── ai/                intent detection (keyword + semantic hybrid), local-LLM fallback, chat orchestration
├── memory/            SQLite connection + schema, familiarity tracking
├── reminders/         local reminder engine (manager, scheduler, notifications)
├── tasks/             local to-do list
├── timers/            local countdown timers (manager, scheduler, notifications)
├── calendar/          Google Calendar integration (read-only, opt-in)
├── humor/             joke/trend caches + the permanent link crawler (subreddit_crawler.py)
├── tools/             JSON-in/JSON-out functions chat's intent layer calls
└── ui/                chat/reminders/tasks/timers windows, tray icon

config/             OAuth client secret + token cache (gitignored, calendar only)
data/               local SQLite database (gitignored)
scripts/            one-off CLI utilities (e.g. crawl_sources.py)
tests/              pytest suite
```

---

## Privacy

- No cloud AI, no conversation upload, no remote database.
- Only two network-capable features; everything else is fully offline:
  - the optional local-LLM chat fallback (talks to Ollama on
    `localhost` — not a hosted API)
  - the optional, off-by-default Google Calendar integration (see
    [Calendar](#calendar))
- Chat can never directly execute an action — the LLM/intent layer only
  proposes an action, and Python validates and runs it (see
  `PROJECT_ARCHITECTURE.md` §5). Calendar reads/connects go through the
  same deterministic path, never left to the LLM.
- No paid APIs, no subscriptions, no API keys required for normal use —
  Google Calendar needs a one-time free OAuth client ID, not a key or a
  subscription.
- The Google OAuth token cached at `config/token.json` is restricted to
  the current OS user (`0600`) immediately after it's written, and
  `config/` itself is restricted the same way — see
  [`docs/VULNERABILITIES.md`](./docs/VULNERABILITIES.md) for the full
  security review this and a couple of other hardening fixes came out
  of.

---

## License

Custom attribution-required license — see [`LICENSE`](./LICENSE).
Copyright stays with Vedanshi Mesvaniya as the original author; others
may use, modify, and distribute the project provided they credit the
original author and don't claim it or a derivative as their own
original work. Third-party dependencies (PySide6/Qt, Ollama-served
models, Google API client libraries) carry their own licenses — check
before distributing a packaged build.
