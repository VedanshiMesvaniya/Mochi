# 🐱 Mochi

**Current status: V1.0 — Correct Assistant** (see
[`docs/ROADMAP.md`](./docs/ROADMAP.md) for the full versioned roadmap:
V1.0 → V1.2 → V2.0 → V2.1 → V3.0 → V3.1). This phase is about making
Mochi *reliable* before making it clever — deterministic tools for
anything that touches real data, a local LLM only for open-ended chat,
never the other way around.

Mochi is a small, local-first desktop companion: a black rounded "screen"
with cat ears and whiskers that sits on your desktop and shows an
expressive pixel face — no body, no walking, just a face that
reacts to you. You can type to it, and it can create local reminders,
tasks, and timers, answer open-ended questions through an optional local
LLM, and remembers roughly how often you've talked to it.

Nothing leaves your machine unless you explicitly turn on an integration
that needs the network. Chat's optional LLM step talks to a
locally-running Ollama, not the cloud; the one exception is the fully
opt-in Google Calendar connection (see below), which is off by default.

---

## What Mochi looks like

A ~180×180px frameless, translucent window: a dark rounded square with
small triangular ears and a few whiskers, and a glowing pixel face on top.
"Pixel" describes the deliberately blocky, geometric shape language
(rounded-rect eyes, hard-edged mouths - the same visual grammar as an LED
matrix icon), not literal jagged rasterization: the face is drawn fully
antialiased at the widget's real resolution, so curves and edges stay
smooth. There's one unified casing look (no user-selectable palette) —
instead, the **glow color itself changes per expression**, like
an LED status light: idle is calm violet, happy is emerald green, angry is
deep crimson, and so on, easing into its new hue over a fraction of a
second rather than cutting instantly. It blinks on its own, has a faint
idle "breathing" glow, and its pupils drift toward your mouse cursor while
idle. Every expression change eases and bounces into place through a small
spring-physics layer rather than snapping instantly, and each reaction
holds long enough to actually register (tuned per-emotion in
`REACTION_HOLD_MS` - a surprised flash is quickest at ~2.5s, alert runs its
full multi-second pulse sequence, a sulk lingers past 4s; the quickest
durations were bumped up from their original ~1.8-2s since they were
reading as a flicker rather than a visible expression) — that's the main
thing that makes it read as alive rather than a slideshow of static faces.

**Expressions (16):** idle, happy, sad, angry, confused, surprised,
thinking, sleepy, sleeping, talking, excited, alert, blush, shy, heart,
wink — all drawn programmatically (no image assets), so every expression
is just numbers (eye openness, pupil offset, mouth shape, brow angle,
color) plus a handful of dedicated shapes for a few states: angry
furrows both brows into a sharp downward frown (steeper and thicker than
a mild sad brow, not just a slight squint), confused shows a small
floating "?", shy closes its eyes into a soft upward curve, sad gets a
small falling tear, excited sparkles a tiny star beside each eye, and
alert runs a six-phase detect → flash-on → peak → flash-off → flash-on →
return pulse (with a tiny vibration at its peak) instead of a static
glow. Chat reactions pick between these organically — a mild compliment
gets a shy blush, "I love you" gets heart-eyes, and an ignored-too-long
attention ping alternates between an alert pulse and a playful wink.

![All 16 of Mochi's expressions](./assets/readme/expressions.png)

**LED color map** — one canonical color per expression, tuned by
arousal/valence rather than picked arbitrarily (see
`app/character/theme.py`):

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

**Sleep:** eyes close and a small cartoon-style "Zzz" floats up and fades
near the ear, looping, always contained within the window's own bounds.

**Personality:** a playful kitten that wants attention. Left alone, it
doesn't sit static — it occasionally perks up (alert or wink), gets
sleepy, and falls asleep; any interaction wakes it back up. It shows
"thinking" the instant you send a chat message, gets happy when you
complete a reminder or task, and gets annoyed if a reminder sits ignored
for a while. It's meant to be an easygoing companion, not a moderator: the
LLM system prompt (`app/ai/llm.py`) explicitly tells it to treat ordinary
topics — relationships, fictional pairings, opinions, personal choices,
and so on — as normal conversation rather than lecturing about
"boundaries" or steering to "a different topic," since that read as
preachy rather than caring. It still declines anything genuinely harmful,
it just doesn't moralize about everyday, non-harmful things.

**Lock-screen easter egg (Windows only):** when you lock your PC, Mochi
closes its eyes; every couple of seconds it playfully peeks one eye open,
then closes it again; unlocking wakes it up excited. Purely cosmetic, no
password handling of any kind on Mochi's side — it just detects the OS
lock state (see `app/character/lock_watcher.py`). No-ops safely on
non-Windows platforms.

**Shake-it easter egg:** grab Mochi and shake it (rapid back-and-forth
drag) and its eyes spin dizzily for a moment — then it gets properly
annoyed with you (angry face + a scolding speech bubble) before settling
back down. Detected purely from cursor movement while dragging (see
`app/character/shake_detector.py`), no accelerometer/OS hooks involved.

**Expression timing:** every reaction — chat replies, task/reminder
completions, unlock, shake — holds for a duration tuned to that specific
emotion (a surprised flash is quick; a sulk lingers) before settling back
to idle, instead of being interrupted by the next autonomous-behavior
tick a couple of seconds later. The floating speech bubble and the face
expression are timed together so they appear and clear as one reaction.

---

## Chat

Double-click Mochi (or right-click → Chat) to open a small translucent
chat popup — messages render as rounded speech bubbles (yours on the
right, Mochi's on the left), like the floating bubble above the
character itself. It opens anchored next to wherever the character
currently is (and the character's own speech bubble does too), clamped
so it always stays fully on-screen even when the character is docked
right at a screen edge or corner. It stays pinned on top of other windows
while open (toggle via the green dot) so it doesn't get buried
mid-conversation, and only goes away when you actually close it. Messages
are handled in three layers:

1. **Deterministic, local, no AI required** — reminders, tasks, timers,
   greetings, common small talk, and `what time is it`/`what day is it`
   are recognized by a rule-based (keyword/regex) matcher and handled
   directly, reading straight from the system clock. This is intentional:
   things that create/delete data, and basic facts like the current time,
   should never depend on whether an optional language model happens to
   be installed and running.
2. **Semantic fallback (understands paraphrases, not just exact phrasing)**
   — if the rule-based matcher finds nothing at all, a second local-model
   pass asks "which of a fixed, small set of intents does this message
   mean, and how sure am I" (`app/ai/semantic_intent.py`). A message like
   "don't let this slip my mind, dentist thing at 4" has none of the
   keyword matcher's exact trigger words but is still clearly a reminder
   request — this layer catches that. The model only ever picks *which*
   category a message belongs to; the actual time/title/duration it acts
   on is still pulled out by the exact same regex parsing the keyword
   layer uses, never invented by the model. How confident the guess is
   decides what happens next: confident enough → act (through the same
   validated path as a keyword match); somewhat confident → Mochi asks a
   clarifying "did you mean...?" question instead of guessing; not
   confident (or Ollama isn't running) → falls through to layer 3 below,
   exactly like it always did. See `PROJECT_ARCHITECTURE.md` section 5a
   for the full design and confidence bands.
3. **Local LLM fallback (open-ended chat)** — anything neither layer above
   recognizes is sent to a locally-running [Ollama](https://ollama.com)
   model (default `qwen2.5:1.5b`, configurable) for a real conversational
   reply, on a background thread so a slow reply never freezes the chat
   window. The prompt always includes the actual current local date/time,
   so relative phrasing like "remind me tonight" or "is it late" has real
   ground truth to reason from instead of the model guessing. If Ollama
   isn't installed, isn't running, or the model hasn't been pulled, Mochi
   says so directly ("my brain's offline right now...") rather than
   giving the same generic "I'm not sure what you mean" line a
   genuinely-unrecognized message gets — the LLM is a nice-to-have
   layered on top of a fully working local app, not a requirement, but it
   shouldn't be a mystery when it's not active.

Mochi also keeps a lightweight local interaction counter (not any kind of
learned model) that shifts its greeting and tone a little as you talk to
it more — new, getting-to-know, and familiar.

While a reply is pending, both the chat window ("thinking...") and the
character itself show it — Mochi's face actually holds a THINKING
expression for the whole wait, up to the ~30s an LLM call can take,
rather than the autonomous idle/happy cycling elsewhere on the desktop
stomping it a couple seconds in (see `BehaviorEngine.enter_busy()` /
`exit_busy()` in `app/character/behavior.py`).

The LLM fallback's system prompt explicitly forbids it from claiming to
have created, checked, completed, or cancelled a reminder/task/timer —
it has no access to do any of that, and the deterministic layer above
handles anything phrased as an actual command before the LLM is ever
asked. This matters because a small local model asked "did you check on
X?" will happily improvise a confident-sounding "yes, I'll remind you..."
if not told otherwise, which is a lie the person has no way to catch
until it quietly never happens.

Also handled deterministically, no AI required: ask Mochi to
`count to 10` (or `count from 3 to 7`) and it actually counts it out with
excitement, capped at 30 numbers so a typo can't turn it into a wall of
text.

**Follow-up references ("it", "that", "the second one").** You don't have
to repeat a title every time — right after creating (or listing) a
reminder/task/timer, you can say "actually delete it", "mark that done",
or "the second one" and Mochi resolves it to the actual thing you were
just talking about, even when other tasks/reminders/timers exist too.
You can also reschedule the same way: "remind me to call mom at 7" →
"make it 8" adjusts the reminder you just created without retyping it.
This is fully deterministic (a small remembered pointer to a real
database row, not a guess) — if the thing you're referring to has since
been completed or deleted some other way, Mochi asks instead of silently
acting on something else. See `PROJECT_ARCHITECTURE.md` section 5c.

---

## Reminders, tasks & timers

All three are fully local, stored in SQLite, and handled entirely through
chat — creating them ("remind me to call mom at 7pm", "set a reminder to
water plants at 8am", "set a timer for 10 minutes", "5 minute timer",
"add task buy milk", "new task clean my room", "remember that I need to
buy milk"), checking them ("do I have any tasks?", "what reminders do I
have?", both answered from the real database, never guessed by the LLM),
and acting on ones that already exist ("mark my task to call aunt as
done", "cancel my reminder to call mom", "cancel the timer") — matched
against whatever's actually open/active by title, so it resolves "call
aunt" against a task literally titled "Call aunt" without needing an
exact-string match. If nothing (or more than one thing) matches, Mochi
asks which one you mean rather than guessing. There's deliberately no
separate right-click menu for any of this — one consistent way in via
chat, rather than a manual window/form duplicating what chat already
does end to end. Chat recognizes a fairly wide range of everyday
phrasing (see `TASK_TRIGGER`/`REMINDER_TRIGGER`/`TIMER_TRIGGER` in
`app/ai/intent.py`), including spelled-out numbers alongside digits —
"in one minute", "for five minutes", "a couple minutes" work exactly like
"in 1 minute"/"for 5 minutes"/"in 2 minutes" would; every message's
detected intent is also logged (`mochi.ai.chat_engine`), so if a phrasing
genuinely doesn't match yet it's visible in the log rather than silently
doing nothing.

Two more phrasings that don't need the literal word "task"/"reminder":
"check on <something>" reports the real status of whatever matches it in
either store ("Yep - 'message my aunt' is set for 19:00", or "I don't
have anything like that saved" if nothing matches) — it never invents an
answer. "Mark it as done" / "that's done" / "I finished it" resolves
"it" against whatever's currently open across both tasks and reminders,
auto-completing it if there's exactly one, or asking which one if there's
more than one. And its cancellation counterpart — "cancel it" / "delete
it" / "scratch that" — resolves "it" the same way against whatever's
currently open/pending/running across tasks, reminders, *and* active
timers, auto-cancelling it if there's exactly one. All three exist
specifically because falling through to the local LLM for phrasing like
this used to produce a hallucinated reply — "I'll remind you...", "Okay,
I'll take care of it", or "Okay, cancelled!" — that sounded plausible but
hadn't actually checked or changed anything. (Deliberately narrow: a bare
"never mind" on its own is *not* treated as a cancel command, since it's
just as commonly a plain conversational dismissal unrelated to any
reminder/task/timer — only phrasing that names an explicit cancel action
triggers this.)

If a single message could plausibly match more than one of
reminder/timer/task creation (e.g. it contains both "remind me" and
"timer" somewhere), Mochi resolves it to whichever trigger phrase
actually appears *first* in what you typed, not a fixed priority order —
so "start a 10 minute timer and remind me when it's done" starts the
timer rather than getting stuck asking a reminder "but when?" just
because "remind me" happens to also appear in the sentence.

- **Reminders** — one-off or repeating (`DAILY`/`WEEKLY`/`MONTHLY`), with
  a background scheduler that checks for due reminders and surfaces them
  with an animation, sound, speech bubble, and OS notification. If a
  reminder is still pending several minutes later, Mochi reacts annoyed.
- **Tasks** — a simple open/done checklist. A due date is optional — add
  a task with no deadline ("add task buy milk") and it just sits in the
  list until you mark it done; give it a deadline ("add task submit
  report at 5pm") and it sorts to the front of the list, soonest first,
  ahead of undated tasks. Unlike reminders, a task with a deadline
  doesn't get its own notification/scheduler — it's a checklist entry
  with a date attached,
  not a timed alert.
- **Timers** — short countdowns that persist across restarts. Chat can
  also list them now ("what timers do I have?", "any timers running?").

### Where finished items go

Completing, cancelling, or a timer firing doesn't just flip a status flag
in place — the record is moved out of its active table (`reminders`,
`tasks`, `timers`) into a matching archive table (`reminders_done`,
`tasks_done`, `timers_done`; see `app/memory/database.py`'s
`archive_row()`/`restore_row()`). The point: the active tables only ever
hold things you still need to deal with, so "what's remaining" never has
to filter finished rows out by hand — it's just everything still in the
table. Reopening a task (`manager.reopen_task()`) moves it back out of
the archive.

You can still ask about what's finished — "what tasks are done", "show
completed reminders", "which timers got cancelled" — Mochi recognizes a
broad synonym glossary for this (`app/ai/db_glossary.py`: "remaining" /
"left" / "pending" all mean active; "done" / "completed" / "finished" /
"history" / "archive" all mean the archive; "cancelled" / "canceled" /
"called off"; "task" / "todo" / "chore" / "assignment" all mean the same
table, and so on), reads the real archive table for whatever you asked
about, and — if a local LLM is available — has it phrase the answer
naturally, strictly grounded in those real rows (never allowed to invent
a title, count, or time beyond what was actually fetched; see
`app/ai/llm.py`'s `phrase_data_answer()`). Without a local LLM running it
falls back to the same real data, just phrased plainly instead. The
query itself is never handed to the LLM to write — see that module's
docstring for why a small local model generating live SQL against your
own database would be exactly the kind of untrusted "LLM performs the
action" step Mochi's own design rule (spec section 1) exists to prevent.

---

## Chat memory & humor

Each chat window remembers the whole conversation for as long as it stays
open — every reply that falls through to the local LLM (see below) gets
the recent conversation as context, not just your latest message in
isolation. Closing the chat window clears that session; opening it again
starts fresh.

Once in a while, if Mochi's been idle long enough to get bored (see
below), it'll also crack a joke unprompted — by default this fetches a
fresh one from a small no-auth joke API, falling back to a built-in
offline list if that's unreachable or disabled
(`MOCHI_HUMOR_ENABLED=false` in `.env` for a fully offline Mochi).

Separately, and **off by default**, Mochi can also stay lightly aware of
what's actually trending — and what's actually funny — right now
(`MOCHI_TREND_AWARENESS_ENABLED=true` in `.env`). This pulls two things on
a slow background timer: general headlines
(`app/humor/trend_fetcher.py`) and, more importantly for "meme level"
humor, real current meme post premises from general-audience meme
subreddits (`app/humor/meme_fetcher.py`, no login/API key needed). Both
get reduced to a short paraphrased label/premise before caching — Mochi
never stores or repeats the actual headline or meme caption text, and
never fetches meme images — and its LLM chat replies may riff on a cached
one in its own voice if it naturally fits, with the meme premise
preferred over a generic headline when both are available. Talks to the
open internet (Google News' RSS feed + Reddit's public JSON endpoints),
which is why it's opt-in rather than on by default.

### Crawling a reference list into permanent local storage

Separately from the two rolling caches above, `app/humor/subreddit_crawler.py`
is a tool for a different job: given a markdown file full of `[title](url)`
links (a curated subreddit list, a reference page, etc.), fetch the actual
**content** behind each link once — not just confirm the link exists — and
keep it permanently in a dedicated `crawled_sources` SQLite table.

You can run it manually:

```bash
python scripts/crawl_sources.py path/to/list.md
```

...or wire it into Mochi's right-click **"Refresh trends & memes"** menu
action so it runs alongside the trend/meme fetches above, on the same
click, without leaving the app: set `MOCHI_CRAWL_SOURCES_PATH` in `.env` to
the markdown file's path (optionally `MOCHI_CRAWL_SOURCE_LIST_NAME` to
label the rows something other than the file's own name). Left unset (the
default), the menu action behaves exactly as before and never touches the
crawler at all — this reuses trend-awareness's opt-in gate
(`MOCHI_TREND_AWARENESS_ENABLED`), but only actually crawls anything once
a source file is configured on top of that. When it does run, Mochi's
speech bubble reports how many new pages it found (e.g. *"All caught up!
Got 3 trend(s) and 2 meme(s) fresh, plus 4 new page(s) crawled."*).

A subreddit's own page is mostly an empty JS-rendered shell over a plain
fetch, so `reddit.com` links go through Reddit's public read-only `.json`
endpoint instead (no login needed) and store the subreddit's actual
current top post titles/text; other links get a plain HTML-to-text
extraction. If a local Ollama model is running, it also gets a pass at
reading that raw content and writing a clean summary, stored alongside it
in a `summary` column — purely additive, never replacing the raw text, and
simply skipped (left `NULL`) if no model is available.

This is **append-only, unlike the rolling caches above** — once a URL has
been crawled successfully, it's kept forever and never re-fetched. Running
the command again is always safe and cheap: every already-stored URL is
skipped before any network call is made, so only genuinely new links in
the file get fetched. A failed fetch (offline, timeout, 404) is logged and
simply not stored, so it's still eligible to be picked up on a later run.

---

## Calendar

Say things like *"what's on my calendar today?"*, *"anything tomorrow?"*,
or *"what's coming up?"* and Mochi answers from your real Google
Calendar. This is the one integration that's off by default and reaches
an actual external service rather than your own machine, so it's
entirely opt-in:

1. `pip install -r requirements-calendar.txt`
2. In [Google Cloud Console](https://console.cloud.google.com/), create
   an OAuth client ID of type **Desktop app** and download its client
   secret JSON. Save it as `config/google_credentials.json`.
3. Set `MOCHI_GOOGLE_CALENDAR_ENABLED=true` in `.env`.
4. Say **"connect my calendar"** to Mochi. A browser window opens for a
   one-time Google consent screen; once approved, the resulting token is
   cached locally at `config/token.json` so this only happens once.
   **"disconnect my calendar"** deletes that local token again (it does
   *not* revoke the grant on your Google account — do that from
   [Google's own permissions page](https://myaccount.google.com/permissions)
   if you want to fully revoke it).

By default only the narrow `calendar.readonly` scope is requested —
Mochi cannot create, edit, or delete anything on your calendar. Set
`MOCHI_GOOGLE_CALENDAR_WRITE_ENABLED=true` to also let Mochi create and
cancel events (widens the scope to `calendar.events`, which still can't
touch calendars themselves, only events on your primary one). If you
already connected in read-only mode, turning this on means saying
"connect my calendar" again so the reconnect actually grants the wider
permission — Google enforces whatever scope was granted at consent time,
regardless of what's in `.env`.

With write access on, Mochi never creates or cancels anything
immediately — every request is proposed first and only acted on after
you explicitly confirm:

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

If it's disabled, not yet set up, or the sign-in has expired/lacks the
right permission, Mochi says so directly (e.g. *"say 'connect my
calendar' to reconnect with edit access"*) instead of guessing or
pretending.

---

## Running it

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
python -m app.main
```

Chat, reminders, tasks, and timers all work out of the box with no
further setup. For richer open-ended chat replies, install
[Ollama](https://ollama.com), run `ollama pull qwen2.5:1.5b` (or your
preferred model, set via `MOCHI_LLM_MODEL` in `.env`), and make sure
Ollama is running — Mochi will start using it automatically.

### Running tests

```bash
pip install -r requirements-dev.txt
pytest
```

Also install `requirements-calendar.txt` first if you want the Google
Calendar-specific tests exercised against the real optional client
libraries too (they pass either way - see `tests/test_google_calendar.py`).

Test temp files are created under a repo-local `.pytest_tmp/` (see
`pytest.ini`'s `--basetemp`, gitignored) rather than the OS default temp
directory — on Windows, `%TEMP%\pytest-of-<user>` isn't always fully
owned by the account running the tests (a previous run under another
account, antivirus, OneDrive, etc. can leave it with permissions pytest
itself can't scan), which surfaces as a wall of
`PermissionError: [WinError 5] Access is denied` at collection time
before any test even runs. If you still hit that on a very first run,
delete `.pytest_tmp/` in this repo and re-run - it's always safe to
delete, pytest recreates it.

### Linting

```bash
pip install -r requirements-dev.txt
ruff check .
```

There is no CI pipeline for this project - run `ruff check .` and the
test suite locally before opening a PR, ideally once with the optional
calendar dependencies installed and once without, so a normal
(fully-local) install and a calendar-enabled one both stay green.

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
- The only network-capable features are the optional local-LLM chat
  fallback (talks to Ollama on `localhost` — not a hosted API) and the
  optional, off-by-default Google Calendar integration (see above) —
  everything else works fully offline.
- Chat can never directly execute an action: the LLM/intent layer only
  proposes an action, and Python validates and runs it (see
  `PROJECT_ARCHITECTURE.md §5`); calendar reads/connects are handled the
  same deterministic way, never left to the LLM.
- No paid APIs, no subscriptions, no API keys required for normal use —
  Google Calendar needs a one-time free OAuth client ID, not a key or a
  subscription.
- The Google OAuth token cached at `config/token.json` is restricted to
  the current OS user (`0600`) immediately after it's written, and
  `config/` itself is restricted the same way — see
  [`docs/VULNERABILITIES.md`](./docs/VULNERABILITIES.md) for the full
  security review this and a couple of other hardening fixes came out of.

---

## License

Custom attribution-required license — see [`LICENSE`](./LICENSE).
Copyright stays with Vedanshi Mesvaniya as the original author; others
may use, modify, and distribute the project provided they credit the
original author and don't claim it or a derivative as their own
original work. Third-party dependencies (PySide6/Qt, Ollama-served
models, Google API client libraries) carry their own licenses — check
before distributing a packaged build.
