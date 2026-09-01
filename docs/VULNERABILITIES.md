# Security review — findings and fixes

This document records real vulnerabilities found during a security review
of the codebase, and exactly how each was fixed. Per project policy, this
file is kept permanently as a record even after every finding below has
been fixed — it is not deleted once resolved.

Scope: this was a source-level review of everything currently in `app/`
(Phases 1–3: character, chat, reminders/tasks/timers, local memory,
optional local LLM, optional Google Calendar). No made-up/theoretical
findings are included — every item below is a real code path in this
repository, with a file/line reference and a concrete before/after.

Reviewed and found **not** to be a problem (documented so future
reviewers don't need to re-check these):
- No `eval`/`exec`/`pickle`/`subprocess`/`os.system` anywhere in `app/`.
- Every SQL query in `app/reminders/`, `app/tasks/`, `app/timers/`,
  `app/memory/`, `app/humor/` uses parameterized `?` placeholders — the
  only place raw SQL string-building happens at all is the one migration
  helper fixed in Finding 1 below.
- Fetched network content (jokes, Google News RSS, meme titles) is never
  stored or shown verbatim — `app/humor/*.py` always reduces it to a
  short, already-paraphrased label before caching, and nothing fetched
  is ever used to build a filesystem path, shell command, or SQL string.
- `app/ai/chat_engine.py`'s calendar-write confirmation gate
  (`ConfirmationRequiredError` in `app/tools/calendar_tools.py`) cannot
  be bypassed by anything the LLM proposes — it's enforced at the tool
  boundary itself, not just by the chat flow calling it correctly.

---

## Finding 1 — SQL identifier interpolation in the migration runner

**File:** `app/memory/database.py`, `_run_migrations()`

**Issue:** SQLite's parameter binding (`?` placeholders) only works for
*values*, never for table/column names, so `PRAGMA table_info({table})`
and `ALTER TABLE {table} ADD COLUMN {column} {column_def}` were built
with an f-string. Today `_COLUMN_MIGRATIONS` is a hardcoded literal in
this same file, so there was no live injection path *yet* — but an
f-string built from a bare identifier is exactly the shape of a SQL
injection bug, and it was one accident away from becoming exploitable
the moment any future feature made a migration entry configurable or
derived from anything outside this file.

**Fix:** Added `_validate_identifier()`, which checks every table/column
name against a strict `^[A-Za-z_][A-Za-z0-9_]*$` allow-list before it is
ever interpolated into SQL, and raises rather than silently truncating
or stripping anything unexpected. Applied to both the table and column
name in `_run_migrations()`.

**Status:** Fixed. Regression coverage: existing
`tests/test_database_migrations.py` still passes unchanged (the
hardcoded migration entries are all valid identifiers), confirming the
guard doesn't break the real migration path.

---

## Finding 2 — Google OAuth token saved with default file permissions

**File:** `app/calendar/google_calendar.py`, `connect()` and
`_load_credentials()`'s refresh path

**Issue:** `config/token.json` holds a live OAuth access token *and*
refresh token for the user's actual Google account — enough to read (and,
if write access is enabled, modify/delete) their real calendar. It was
written with `Path.write_text()`, which creates the file with whatever
permissions the process' default umask gives it. On a shared or
multi-user machine, a default umask (e.g. `022`) leaves the file
group/world-readable, so any other local account could read out a live
refresh token and use it indefinitely — this is worse than typical
"local secrets on disk" exposure because a refresh token doesn't expire
the way a short-lived access token does.

**Fix:** Added `_write_token()`, a single helper both token-writing call
sites now go through, which calls `os.chmod(token_path, stat.S_IRUSR |
stat.S_IWUSR)` (owner read/write only, `0600`) immediately after writing.
Best-effort and wrapped in `try/except OSError` — `chmod`'s POSIX mode
bits are not meaningful on Windows NTFS ACLs, and this must never turn a
successful token save into a crash. Also hardened `Settings.
ensure_directories()` to restrict `config/` itself (`0700`) as a second,
defense-in-depth layer, since the containing directory's permissions
matter too.

**Status:** Fixed.

---

## Finding 3 — Unsanitized filename config could redirect credential I/O

**File:** `app/core/config.py`, `Settings.load()` /
`google_client_secret_path` / `google_token_path`

**Issue:** `MOCHI_GOOGLE_CLIENT_SECRET_FILENAME` and
`MOCHI_GOOGLE_TOKEN_FILENAME` (`.env` values) were used as-is, joined
onto `config_dir` with `Path.__truediv__`. Pathlib's `/` operator does
**not** sandbox that join: `Path("/a/b") / "../../etc/passwd"` walks
outside `config_dir` via `..` segments, and — more surprisingly —
`Path("/a/b") / "/etc/passwd"` (an absolute right-hand side) silently
**discards the left side entirely** and resolves straight to
`/etc/passwd`. Since these two values come from local `.env` config
(trusted, but not validated), a typo'd or tampered `.env` entry could
point Mochi's OAuth client-secret read or token read/write at an
arbitrary path on disk instead of the intended `config/` directory.

**Fix:** Added `_safe_filename()`, which rejects any value containing a
path separator (`/` or `\`), a bare `.`/`..`, or an absolute path, and
falls back to the documented default (`google_credentials.json` /
`token.json`) rather than silently using a mangled or dangerous value.

**Status:** Fixed.

---

## Notes for future review

- If a Mode A (fully local, non-Google) calendar is added per spec
  section 22, re-check Finding 1's identifier-validation pattern applies
  to any new schema/migration code it introduces.
- If voice input (Phase 4, `faster-whisper`) lands, audio capture must
  keep following spec section 27/15 (only listen when explicitly
  triggered, discard raw audio after transcription) — worth a follow-up
  pass at that point rather than assuming this document's scope covers it.

---

## Follow-up review — chat/AI intelligence & privacy (`app/ai/`)

A second, separate review covered `app/ai/chat_engine.py` and
`app/ai/intent.py` specifically — correctness bugs and privacy leaks in
the deterministic chat/tool layer, rather than the storage/credential
findings above. Same permanent-record policy applies.

## Finding 4 — Wrong item silently acted on when two titles tie

**File:** `app/ai/chat_engine.py`, `_fuzzy_find()`

**Issue:** When two open tasks/reminders/timers scored equally against a
query (e.g. "Call Mom" and "Call Dad" both matching "call" in "mark my
task call as done"), the matcher silently kept whichever was encountered
first, rather than recognizing the tie at all.

**Fix:** `_fuzzy_find()` now returns an `Ambiguous` sentinel on a tie;
every call site (complete/cancel task, complete/cancel reminder, cancel
timer, plus the combined task/reminder lookup) asks the user which one
instead of guessing.

**Status:** Fixed. See `tests/test_chat_engine.py`'s
`test_mark_task_done_with_tied_match_asks_instead_of_picking_first` and
`test_cancel_task_with_tied_match_asks_instead_of_picking_first`.

---

## Finding 5 — "Tomorrow at X" landed on the wrong day

**File:** `app/ai/intent.py`, `_parse_absolute_time()`

**Issue:** The date was rolled forward once whenever the clock time had
already passed today, and separately rolled forward again whenever the
text contained "tomorrow" — so "tomorrow at 5pm" typed after 5pm today
landed two days out instead of one.

**Fix:** The target *date* is resolved first (today, or the next
calendar day if "tomorrow" is explicit), then the clock time is applied
to it; the "already passed" rollover only happens when no explicit date
word was given.

**Status:** Fixed. See `tests/test_intent.py`'s
`test_reminder_tomorrow_at_time_already_passed_today_lands_on_tomorrow`.

---

## Finding 6 — Stale calendar confirmation could survive unrelated turns

**File:** `app/ai/chat_engine.py`, `handle_message()`

**Issue:** A pending "add to calendar?" proposal used to be carried
forward through any message that wasn't a literal yes/no — including
small talk and clarification-shaped replies — so a much later, unrelated
bare "yes" could still confirm a proposal the user had long since moved
on from.

**Fix:** The proposal now expires the moment a non-yes/no message is
seen, in a single place right after the confirmation check, so every
downstream code path is correct by construction rather than needing to
remember to expire it individually (an earlier, narrower fix that only
expired it in some branches regressed for exactly this reason — see
Finding 7).

**Status:** Fixed. See `tests/test_chat_engine.py`'s
`test_small_talk_reply_expires_pending_action` and
`test_semantic_clarify_reply_expires_pending_action`.

---

## Finding 7 — Private event title logged in plaintext

**File:** `app/ai/chat_engine.py`

**Issue:** Several `logger.info(...)` calls interpolated the raw chat
message, the full tool-args dict, or the full `pending_action` dict
(`%r`) directly into the log file — all of which can contain a private
reminder/task/appointment title. `app/core/logger.py`'s own stated policy
is that user content shouldn't be logged.

**Fix:** Every log call in the chat/intent path now logs only the intent
name, tool name, or entity *kind* — never the message text, tool args, or
full entity dict.

**Status:** Fixed. See `tests/test_chat_engine.py`'s
`test_expiring_pending_action_never_logs_the_private_title`.

---

## Finding 8 — `data/` directory not permission-hardened like `config/`

**File:** `app/core/config.py`, `Settings.ensure_directories()`

**Issue:** `config/` (Google OAuth secrets) was restricted to the current
user (see Finding 2), but `data/` — which holds `mochi.db` and
`data/logs/mochi.log`, both containing personal task/reminder/appointment
content — was not given the same treatment.

**Fix:** Added a reusable `harden_directory()` helper, now applied to
both `data_dir` and `config_dir` (and the log directory specifically, in
`app/core/logger.py`).

**Status:** Fixed. See `tests/test_config.py`.

---

## Finding 9 — "did" treated as a bare synonym for "done"

**File:** `app/ai/db_glossary.py`, `STATUS_SYNONYMS`

**Issue:** A plain grammatical "did" (as in "what did I have for tasks?")
was mapped straight to the "done" status bucket, so an ordinary open-task
question was misread as a finished-tasks query.

**Fix:** Removed the bare `"did"` entry; the more specific phrase-level
entries ("have i done", "have i completed", ...) still work correctly.

**Status:** Fixed. See `tests/test_db_glossary.py`'s
`test_match_status_does_not_treat_bare_did_as_done`.

---

## Finding 10 — Glossary matching used substring containment, not word boundaries

**File:** `app/ai/db_glossary.py`, `match_entity()` / `match_status()`

**Issue:** Both functions checked `key in lowered_text` — plain substring
containment — so a short glossary entry could false-match inside an
unrelated word entirely: `"ping"` (→ reminders) inside `"shopping"`,
`"left"` (→ active) inside `"leftover"`, `"all"` (→ all-statuses) inside
`"call"`.

**Fix:** Both functions now match against precompiled `\b`-word-boundary
regexes instead of plain substring checks.

**Status:** Fixed. See `tests/test_db_glossary.py`'s
`test_match_entity_does_not_false_match_substrings` and
`test_match_status_does_not_false_match_substrings`.

---

## Finding 11 — No conversational memory: "it"/"that" couldn't be resolved

**File:** `app/ai/chat_engine.py` (new: `app/ai/conversation_state.py`)

**Issue:** Referring back to something just created or discussed ("add
task buy milk" → "actually delete it") only worked when there was
exactly one open item in the entire app — with two or more open
tasks/reminders/timers, Mochi always asked "which one?", even
immediately after creating the specific thing being referred to.

**Fix:** New `app/ai/conversation_state.py` module — a small, plain dict
threaded between `handle_message()` calls (same ownership convention as
`pending_action`) remembering the single most recent entity
created/resolved/listed. Complete/cancel/check-on handlers resolve bare
pronoun ("it"/"that") and ordinal ("the second one") references against
it *after* the normal fuzzy title search finds nothing, and only ever act
when the referenced entity is still real; a stale reference fails closed
and asks, exactly as if this feature didn't exist. Also added a new
"reschedule by reference" capability ("make it 8" / "change it to 9am").
See `PROJECT_ARCHITECTURE.md` section 5c for the full design.

**Status:** Fixed. See `tests/test_conversation_state.py` and
`tests/test_conversation_state_integration.py`.

