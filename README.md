# 🐱 Mochi

Mochi is a small, local-first desktop companion: a black rounded "screen"
with cat ears and whiskers that sits on your desktop and shows an
EMO-style pixel face — no body, no walking, just an expressive face that
reacts to you. You can type to it, and it can create local reminders,
tasks, and timers, answer open-ended questions through an optional local
LLM, and remembers roughly how often you've talked to it.

Nothing leaves your machine unless you explicitly turn on an integration
that needs the network (there currently isn't one wired up — chat's
optional LLM step talks to a locally-running Ollama, not the cloud).

---

## What Mochi looks like

A ~180×180px frameless, translucent window: a dark rounded square with
small triangular ears and a few whiskers, glowing purple pixel eyes and
mouth. It blinks on its own, has a faint idle "breathing" glow, and its
pupils drift toward your mouse cursor while idle.

**Expressions:** idle, happy, sad, angry, confused, surprised, thinking,
sleepy, sleeping, talking, excited, alert — all drawn programmatically
(no image assets), so every expression is just numbers (eye openness,
pupil offset, mouth shape, brow angle), not artwork.

**Personality:** a playful kitten that wants attention. Left alone, it
doesn't sit static — it occasionally perks up (alert), gets sleepy, and
falls asleep; any interaction wakes it back up. It shows "thinking" the
instant you send a chat message, gets happy when you complete a reminder
or task, and gets annoyed if a reminder sits ignored for a while.

---

## Chat

Double-click Mochi (or right-click → Chat) to open a small translucent
chat popup. Messages are handled in two layers:

1. **Deterministic, local, no AI required** — reminders, tasks, timers,
   greetings, and common small talk are recognized by a rule-based
   matcher and handled directly. This is intentional: things that create
   or delete data should never depend on a language model's judgement.
2. **Local LLM fallback** — anything that bucket doesn't recognize is
   sent to a locally-running [Ollama](https://ollama.com) model (default
   `qwen3:0.6b`, configurable) for a real conversational reply. If Ollama
   isn't installed or running, Mochi falls back to a friendly "not sure
   what you mean yet" reply instead of breaking — the LLM is a nice-to-have
   layered on top of a fully working local app, not a requirement.

Mochi also keeps a lightweight local interaction counter (not any kind of
learned model) that shifts its greeting and tone a little as you talk to
it more — new, getting-to-know, and familiar.

---

## Reminders, tasks & timers

All three are fully local, stored in SQLite, and manageable both by
chatting ("remind me to call mom at 7pm", "set a timer for 10 minutes",
"remember that I need to buy milk") and through dedicated windows from
the right-click menu:

- **Reminders** — one-off or repeating (`DAILY`/`WEEKLY`/`MONTHLY`), with
  a background scheduler that checks for due reminders and surfaces them
  with an animation, sound, speech bubble, and OS notification. If a
  reminder is still pending several minutes later, Mochi reacts annoyed.
- **Tasks** — a simple open/done checklist, no due date.
- **Timers** — short countdowns that persist across restarts.

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
tests/              pytest suite (134 tests)
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
