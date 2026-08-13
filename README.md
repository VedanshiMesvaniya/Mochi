# 🐱 Mochi

A local-first, privacy-focused desktop AI companion — a small animated cat
that lives on your desktop, talks to you, remembers what you tell it, and
reminds you about the things that matter. No cloud AI required for normal
use.

> "There is a tiny cat living on my computer." — not "I opened another AI
> application."

See [`PROJECT_ARCHITECTURE.md`](./PROJECT_ARCHITECTURE.md) for the full
system design, module map, and data flow.

---

## ✅ Current status

**In progress — building toward V1 (desktop character + local reminders).**

### Implemented so far

- [x] Project scaffolding (folder layout, `.gitignore`, `requirements.txt`,
      `.env.example`, `LICENSE`)
- [x] Core services: config loader, rotating logger, typed exceptions,
      in-process event bus
- [x] Character subsystem:
  - [x] State machine (`CharacterState`, `Emotion`)
  - [x] Deterministic autonomous behavior engine (no LLM calls)
  - [x] Desktop movement / screen-bounds math
  - [x] Dynamic sprite animation loader
  - [x] Transparent, frameless, always-on-top, draggable PySide6 window
  - [x] Right-click menu (Chat / Reminders / Calendar / Memories /
        Settings / Sleep / Exit)
- [x] System tray icon with show/hide/exit
- [x] Application entry point (`app/main.py`)
- [x] Unit tests for movement, behavior engine, and state machine

### Not yet implemented

- [ ] Local reminders (SQLite storage, scheduler, notifications, UI) — *in
      progress*
- [ ] Local AI chat (Ollama + Qwen3 0.6B)
- [ ] Voice input/output (faster-whisper + Piper)
- [ ] Local long-term memory
- [ ] Local + Google Calendar integration
- [ ] Relationship/personality system
- [ ] Settings window
- [ ] Original character artwork/sounds (currently no sprite assets — the
      window renders transparently until artwork is added under
      `assets/animations/*`)

---

## 🧭 Roadmap

| Version | Scope |
|---|---|
| **V1** *(current target)* | Desktop character + local reminders |
| V2 | Timers + tasks |
| V3 | Google Calendar (read-only) |
| V4 | Google Calendar (create/edit, with confirmation) |
| V5 | Long-term memory + proactive reminders |

Full phase breakdown lives in `PROJECT_ARCHITECTURE.md §8`.

---

## 🛠 Tech stack

| Concern | Technology |
|---|---|
| Desktop UI | Python + PySide6 (transparent/frameless window) |
| Local LLM | Qwen3 0.6B via Ollama (configurable via `MOCHI_LLM_MODEL`) |
| Speech-to-text | faster-whisper (planned) |
| Text-to-speech | Piper (planned) |
| Storage | SQLite (`data/mochi.db`) — no server, no cloud |
| Calendar (optional) | Google Calendar API, OAuth, explicit opt-in |

No paid APIs. No subscriptions. No data leaves your machine unless you
explicitly enable Google Calendar.

---

## 🚀 Running it (Phase 1 — character only, no AI yet)

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
python -m app.main
```

Note: there's no sprite artwork checked in yet, so Mochi currently renders
as an (invisible) transparent window — the tray icon and right-click menu
still work. Drop PNG frame sequences into `assets/animations/<state>/` to
see the character.

### Running tests

```bash
pip install -r requirements-dev.txt
pytest
```

---

## 📁 Project layout

See `PROJECT_ARCHITECTURE.md §3` for the full annotated module map.

```text
app/
├── main.py
├── core/          config, logging, events, exceptions
├── character/     on-screen presence, animation, behavior, movement
├── ai/            (planned) LLM access, prompts, intent parsing
├── memory/        (planned) SQLite conversations & long-term memory
├── voice/         (planned) mic capture, STT, TTS, sound effects
├── tools/         (planned) LLM-callable Python functions
├── reminders/     (in progress) local reminder engine
├── calendar/      (planned) local + Google Calendar
└── ui/            tray, chat/reminder/settings/calendar windows

assets/            animations, sounds, icons (bring your own — see below)
data/              local SQLite database (gitignored)
config/            personality.json, etc.
tests/             unit tests for pure-Python logic
```

---

## 🔐 Privacy & security principles

- No cloud AI, no conversation upload, no remote database by default.
- Microphone audio is only captured while actively listening, processed
  locally, and discarded after transcription.
- The LLM never executes actions directly — every proposed action is
  schema-validated and permission-checked in Python before it runs, and
  calendar create/update/delete always requires explicit user confirmation.
- OAuth tokens and credentials are never committed to Git (`.gitignore`
  excludes `config/google_credentials.json`, `config/token.json`, `.env`).

See `PROJECT_ARCHITECTURE.md §5` and §7 for the full validation/error-
handling design, and the original project spec for the complete privacy
policy.

---

## 🎨 Character artwork

Mochi is an **original character** — not a clone of any existing desktop
pet's art, voice, or animations. Sprite frames go in
`assets/animations/<state>/` (e.g. `idle/`, `walk_left/`, `happy/`) as
sequential transparent PNGs; the animator discovers them automatically, no
code changes required.

---

## 📄 License

MIT — see [`LICENSE`](./LICENSE). Third-party dependencies (PySide6/Qt,
Ollama-served models, Piper voices, etc.) carry their own licenses — check
before distributing a packaged build.
