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
