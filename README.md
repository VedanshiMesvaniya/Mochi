# 🐱 Mochi

Mochi is a small, local-first desktop companion: a black rounded "screen"
with cat ears and whiskers that sits on your desktop and shows an
expressive pixel face — no body, no walking, just a face that
reacts to you. You can type to it, and it can create local reminders,
tasks, and timers, answer open-ended questions through an optional local
LLM, and remembers roughly how often you've talked to it.

Nothing leaves your machine unless you explicitly turn on an integration
that needs the network (there currently isn't one wired up — chat's
optional LLM step talks to a locally-running Ollama, not the cloud).

---

## What Mochi looks like

A ~180×180px frameless, translucent window: a dark rounded square with
small triangular ears and a few whiskers, glowing pixel eyes and mouth in
your choice of four color themes. It blinks on its own, has a faint idle
"breathing" glow, and its pupils drift toward your mouse cursor while
idle. Every expression change eases and bounces into place through a
small spring-physics layer rather than snapping instantly — that's the
main thing that makes it read as alive rather than a slideshow of static
faces.

**Glow theme:** Purple (default), Blue, Mint, or Rose — pick one from
right-click → Theme. Purely cosmetic (expression geometry never changes
between themes, only the color), persisted locally in SQLite so it's
remembered across restarts.

![Mochi's four glow themes: Purple, Blue, Mint, and Rose](./assets/readme/themes.png)

**Expressions (16):** idle, happy, sad, angry, confused, surprised,
thinking, sleepy, sleeping, talking, excited, alert, blush, shy, heart,
wink — all drawn programmatically (no image assets), so every expression
is just numbers (eye openness, pupil offset, mouth shape, brow angle,
blush/heart-eyes flags), not artwork. Chat reactions pick between these
organically — a mild compliment gets a shy blush, "I love you" gets
heart-eyes, and an ignored-too-long attention ping alternates between an
alert pulse and a playful wink.

![All 16 of Mochi's expressions](./assets/readme/expressions.png)

**Sleep:** eyes close and a small cartoon-style "Zzz" floats up and fades
near the ear, looping, always contained within the window's own bounds.

**Personality:** a playful kitten that wants attention. Left alone, it
doesn't sit static — it occasionally perks up (alert or wink), gets
sleepy, and falls asleep; any interaction wakes it back up. It shows
"thinking" the instant you send a chat message, gets happy when you
complete a reminder or task, and gets annoyed if a reminder sits ignored
for a while.

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
character itself. It stays pinned on top of other windows while open
(toggle via the green dot) so it doesn't get buried mid-conversation, and
only goes away when you actually close it. Messages are handled in two
layers:

1. **Deterministic, local, no AI required** — reminders, tasks, timers,
   greetings, and common small talk are recognized by a rule-based
   matcher and handled directly. This is intentional: things that create
   or delete data should never depend on a language model's judgement.
2. **Local LLM fallback** — anything that bucket doesn't recognize is
   sent to a locally-running [Ollama](https://ollama.com) model (default
   `qwen3:0.6b`, configurable) for a real conversational reply, on a
   background thread so a slow reply never freezes the chat window. If
   Ollama isn't installed, isn't running, or the model hasn't been
   pulled, Mochi says so directly ("my brain's offline right now...")
   rather than giving the same generic "I'm not sure what you mean" line
   a genuinely-unrecognized message gets — the LLM is a nice-to-have
   layered on top of a fully working local app, not a requirement, but
   it shouldn't be a mystery when it's not active.

Mochi also keeps a lightweight local interaction counter (not any kind of
learned model) that shifts its greeting and tone a little as you talk to
it more — new, getting-to-know, and familiar.

---

## Reminders, tasks & timers

All three are fully local, stored in SQLite, and handled entirely through
chat — both creating them ("remind me to call mom at 7pm", "set a timer
for 10 minutes", "remember that I need to buy milk") and checking them
("do I have any tasks?", "what reminders do I have?", both answered from
the real database, never guessed by the LLM). There's deliberately no
separate right-click menu for any of this anymore — one consistent way in
via chat, rather than a manual window duplicating what chat already does
end to end.

- **Reminders** — one-off or repeating (`DAILY`/`WEEKLY`/`MONTHLY`), with
  a background scheduler that checks for due reminders and surfaces them
  with an animation, sound, speech bubble, and OS notification. If a
  reminder is still pending several minutes later, Mochi reacts annoyed.
- **Tasks** — a simple open/done checklist, no due date.
- **Timers** — short countdowns that persist across restarts.

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
[Ollama](https://ollama.com), run `ollama pull qwen3:0.6b` (or your
preferred model, set via `MOCHI_LLM_MODEL` in `.env`), and make sure
Ollama is running — Mochi will start using it automatically.

### Running tests

```bash
pip install -r requirements-dev.txt
pytest
```

---

## Project layout

See [`PROJECT_ARCHITECTURE.md`](./PROJECT_ARCHITECTURE.md) for the full
module map and data flow. Quick orientation:

```text
app/
├── main.py          application entry point / wiring
├── core/             config, logging, event bus, exceptions
├── character/        the pixel face, its behavior/personality, the window
├── ai/                intent detection, local-LLM fallback, chat orchestration
├── memory/            SQLite connection + schema, familiarity tracking
├── reminders/         local reminder engine (manager, scheduler, notifications)
├── tasks/             local to-do list
├── timers/            local countdown timers (manager, scheduler, notifications)
├── tools/             JSON-in/JSON-out functions chat's intent layer calls
└── ui/                chat/reminders/tasks/timers windows, tray icon

data/               local SQLite database (gitignored)
tests/              pytest suite (268 tests)
```

---

## Privacy

- No cloud AI, no conversation upload, no remote database.
- The only network-capable feature is the optional local-LLM chat
  fallback, and that talks to Ollama on `localhost` — not a hosted API.
- Chat can never directly execute an action: the LLM/intent layer only
  proposes an action, and Python validates and runs it (see
  `PROJECT_ARCHITECTURE.md §5`).
- No paid APIs, no subscriptions, no API keys required for normal use.

---

## License

MIT — see [`LICENSE`](./LICENSE). Third-party dependencies (PySide6/Qt,
Ollama-served models) carry their own licenses — check before
distributing a packaged build.
