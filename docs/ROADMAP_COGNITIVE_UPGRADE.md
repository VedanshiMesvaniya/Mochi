# Mochi Cognitive Intelligence Upgrade

Status: Phase 1 done; Phase 2 done (all four memory layers, working
memory as a container module, procedural-memory learning from tool
failures, and an opt-in LLM-based extraction path are all implemented -
see "Implementation status" below; memory consolidation stays folded
into fact_extraction.py/remember_fact rather than a separate pipeline
stage, a deliberate scoping choice explained below).

Phase 3 - as the spec's own section 28 defines it (confidence system,
clarification policy, reference resolution, correction handling,
reasoning budget) - is DONE; see "Implementation status" below for
each of the five pieces and where it lives. Note: an earlier version of
this status line lumped "model benchmarking" and "mood/initiative"
into "the rest of Phase 3" - that was imprecise. Per the spec's own
section 28, those are Phase 4 and Phase 5 respectively, not Phase 3.
Correcting that here rather than leaving it wrong.

Phase 4 (model benchmark) is PARTIALLY done: a permanent, versioned
benchmark dataset and harness now exist (`benchmarks/`), with a real,
reproducible baseline checked in - but the actual cross-model
comparison the spec asks for still cannot be run in this development
environment (confirmed no route to Ollama/Hugging Face, no GPU) - see
"Implementation status" for the full picture.

Phase 5 (mood, expression state, initiative, proactive reminders,
presence behavior) is partially done: mood/expression state was
already substantially satisfied by pre-existing architecture (the LLM
suggests an emotion, the app owns the final state - see below) and is
now documented as such; an initiative-scoring mechanism is implemented;
proactive reminders already existed before this spec (reminder-due
notifications); a NEW proactive channel (e.g. "an important calendar
event is approaching") does not exist and is left as an open,
deliberately-not-guessed-at product decision - see below.

Phase 6 (voice) is not started.
Project: Mochi
Purpose: Upgrade Mochi from a simple LLM chatbot into a reliable local
desktop companion with persistent context, memory, and reasoning.

---

## Implementation status (read this first)

This file is the original Cognitive Upgrade design spec, kept in full
below so future work can be checked against it. The current codebase
implements a first, narrow slice of Phase 1 - see `PROJECT_ARCHITECTURE.md`
section 5j for the actual code pointers and data flow.

**Implemented (Phase 1, partial):**

- **Active-goal single-slot clarification** (spec sections 3-4, 16-17's
  exact "5" example) - `app/ai/goal_state.py`, wired into
  `app/ai/chat_engine.py`/`app/ai/intent.py`/`app/ui/chat_window.py`.
  Covers every "*_needs_time"/"*_needs_duration" intent that exists
  today (`calendar_create_needs_time`, `create_reminder_needs_time`,
  `reschedule_reference_needs_time`, `create_timer_needs_duration`) - a
  bare follow-up reply like "5" now completes the request it answers,
  instead of being treated as an unrelated new message.
- **Tool verification for calendar writes** (spec section 12) -
  `app/tools/calendar_tools.py`'s `create_event`/`update_event`/
  `delete_event` now re-check the result via the new
  `app/calendar/google_calendar.get_event()` before reporting success,
  rather than equating "the API call didn't raise" with "the change
  took effect" (spec section 27, rule 10).
- Reference resolution ("it"/"that"/"the second one") and corrections to
  an existing reschedule already existed before this spec
  (`app/ai/conversation_state.py`, `PROJECT_ARCHITECTURE.md` section 5c)
  and satisfy spec sections 16-17 for those cases; this upgrade extended
  the same *goal* (not just entity-reference) memory to the "5" case
  above.
- Structured decision output (spec section 6/20's "internal decision ->
  response layer" split) already existed in the form of `DetectedIntent`
  (deterministic fields) feeding a separate response-text step - not
  newly built for this spec, just already-matching prior architecture.
- Tool schema validation (spec section 10) already existed via each
  tool module's `TOOL_SCHEMAS` + `ToolValidationError` - not newly built.
- **Semantic memory** (spec section 7, one of Phase 2's four memory
  layers) - `app/memory/semantic_memory.py` (storage: `remember_fact`/
  `list_facts`/`forget_fact`/`find_relevant`/`find_matching`, SQLite
  `user_facts` table) and `app/ai/fact_extraction.py` (deterministic,
  non-LLM pattern matching for passive extraction from ordinary chat -
  "I live in Austin", "I might switch to Linux", etc). Wired into
  `app/ai/chat_engine.py`: explicit "remember that .../what do you know
  about me/forget ..." commands, a passive best-effort extraction side
  effect on every message, and relevant stored facts fed into the LLM's
  context for open-ended chat (mirrors how `web_context` already works).
  Contradiction handling (spec section 9) works exactly like the spec's
  own example: "I use Windows" then "I switched to Linux" supersedes the
  old fact rather than storing both, via a `status`/`superseded_by`
  chain - simpler than the spec's suggested `valid_from`/`valid_until`
  temporal-window metadata, which isn't implemented (a straight
  supersede chain answers "what's true now" and "what did I used to
  think" equally well for this scope, without needing range queries).
  Gated by `settings.memory_enabled`
  (`MOCHI_MEMORY_ENABLED`) - a flag that existed since V1 but was dead
  config (nothing read it) until this.
- **Episodic memory** (spec section 7, the second of Phase 2's four
  memory layers) - `app/memory/episodic_memory.py` (storage:
  `record_event`/`recent_events`/`important_events`, SQLite
  `episodic_events` table, same `memory_enabled` gate as semantic
  memory). Deliberately scoped to real actions Mochi itself took
  (created a reminder, added a calendar event, ...) rather than a
  free-text summary of what the conversation was about - see that
  module's docstring for why the spec's own richer example (a
  conversational summary mentioning an OAuth problem) isn't attempted.
  `record_event` is called as a side effect right after a
  reminder/task/timer/calendar-event create or a calendar-event cancel
  succeeds (chat_engine.py), and is deliberately best-effort/never-raise
  (unlike semantic memory's `remember_fact`), since every call site sits
  alongside an action that has already succeeded. A new "what have you
  done for me" chat command answers from this real log rather than
  letting the LLM guess (spec: "ask database, not LLM").
- **Procedural memory** (spec sections 7/24, the third of Phase 2's four
  memory layers, "Learning From Failure") - `app/memory/procedural_memory.py`
  (storage: `learn_rule`/`relevant_rules`/`has_recurring_issue`, SQLite
  `procedural_rules` table, same `memory_enabled` gate). Wired into the
  five write-confirmation branches of `app/ai/chat_engine._resolve_pending_action`
  (calendar create/delete, Google Tasks create/complete/delete) via a
  small, fixed lookup (`_FAILURE_LESSONS`) from KNOWN, generalizable
  failure types (a Google Calendar/Tasks connection or sign-in problem)
  to a lesson worth remembering - reproducing spec section 24's own
  worked example exactly ("Google Calendar sign-in can expire or be
  revoked - check connection status before assuming a write will go
  through"). Deliberately narrow: a one-off input problem (a bad title,
  a malformed date) never becomes a "rule", since it wouldn't generalize
  to anything - only connection/auth-shaped failures do. A rule is
  deduplicated by `(rule, scope)` rather than piling up a new row per
  occurrence - repeat failures just bump a `trigger_count`. Once a scope
  has failed the same way more than once, the next failure's error
  message includes a one-line "this has come up before" hint using the
  rule's own wording (`_failure_reaction`), rather than reporting each
  occurrence as a first-time surprise.
- **Working memory as one dedicated module** (spec section 7) -
  `app/ai/working_memory.py`'s `WorkingMemory` container bundles
  `pending_action`, the reference-resolution state
  (`app/ai/conversation_state.py`), and the active-goal state
  (`app/ai/goal_state.py`, Phase 1) into the single named concept the
  spec describes - matching the spec's own framing of working memory as
  a BUNDLE of several pieces, not one algorithm to reimplement. The
  underlying reference-resolution and goal-completion logic
  deliberately still lives in its original, already-tested modules
  rather than being rewritten wholesale into one file - see that
  module's docstring for why a pure rename/merge wasn't worth the risk
  to already-working code. `app/ui/chat_window.py` uses it to build the
  next `handle_message()` call's keyword arguments and to unpack a
  `ChatReaction` in one step (`WorkingMemory.from_reaction(...)
  .as_kwargs()`), while keeping its own three `_pending_action`/
  `_conversation_state`/`_active_goal` attributes as the source of truth
  (existing tests already assert against those names directly).
- **LLM-based fact-candidate extraction** (spec section 5's fuller
  vision beyond deterministic patterns) - opt-in via
  `settings.llm_fact_extraction_enabled` (`MOCHI_LLM_FACT_EXTRACTION_ENABLED`,
  off by default, and requires `memory_enabled=True` as well - see that
  setting's own docstring in `app/core/config.py`). Adds **no extra
  model call**: `app/ai/llm.ask`'s new `request_fact_extraction` param
  rides on the SAME already-happening call for an "unknown"-intent chat
  reply, asking the model to optionally add a `"notable_fact"` field to
  its existing JSON response. `app/ai/chat_engine.py` only ever sets
  that param when the deterministic extractor
  (`app/ai/fact_extraction.py`) found NOTHING for the same message, so
  the two paths never both try to save the same thing. A model-supplied
  fact is stored with `subject=None` (its own note - a model's guess
  should never silently supersede an existing, more reliably-sourced
  fact) and a fixed, lower confidence (`source="inferred_llm"`) than the
  deterministic paths, since a model's judgment call is inherently less
  predictable than a fixed pattern match.

## Phase 3 - COMPLETE

Per spec section 28's own definition, Phase 3 is: confidence system,
clarification policy, reference resolution, correction handling,
reasoning budget. All five:

- **Confidence-aware clarification wording** (spec section 13's own
  worked example: "Schedule Devika tomorrow evening" -> "what time
  tomorrow evening?", never inventing a specific time) -
  `app/ai/intent.py`'s `_describe_missing_time()`. When a
  "*_needs_time" question is about to be asked (calendar create,
  reminder create - both the keyword and semantic-classification paths
  - and reschedule-reference), the message is checked for a
  time-of-day word ("morning"/"afternoon"/"evening"/"tonight"/"night"/
  "noon"/"midnight") plus an optional "today"/"tomorrow"/weekday name.
  If one is found, the clarifying question echoes it back ("what time
  tomorrow evening?") instead of the generic "but when?".
- **Weekday-name date resolution** (spec section 15's "do not rely on
  the LLM to calculate calendar dates" - a general date-parsing gap
  this phase surfaced, not a scoping choice). `_resolve_explicit_date()`
  in `app/ai/intent.py` now resolves "Thursday"/"next Thursday" to the
  next actual occurrence of that weekday (deterministic day-of-week
  arithmetic, same "pin the date, never re-roll it" pattern already
  used for "tomorrow"), for calendar creation, reminders, and
  reschedule-reference alike. Before this fix there was no weekday
  parsing AT ALL - only "today"/"tomorrow" - so a phrase like "schedule
  a meeting Thursday at 5pm" would have silently landed on the wrong
  day. This was a prerequisite for the next item, not just a nice-to-
  have.
- **Reference resolution** (section 16) - already existed before this
  spec (`app/ai/conversation_state.py`, built for an earlier security
  review; see section 5c in `PROJECT_ARCHITECTURE.md`), and already
  satisfies the spec's own example: "make it 7" / "reschedule it" /
  "cancel that" resolve deterministically against the single most
  recent thing created, resolved, or listed - never guessed at by the
  model.
- **Correction handling** (section 17) - the mechanism already existed
  before this spec too: `app/ai/intent.py`'s `RESCHEDULE_TRIGGER`
  ("make it"/"change it to"/"move it"/"set it"/"reschedule it") already
  modifies the same active/most-recent item rather than creating a
  second one, satisfying the spec's actual point ("a correction should
  update existing state, not spawn an unrelated new task"). The spec's
  own literal transcript - a bare "Actually Thursday" with no verb at
  all - is now also recognized: `BARE_CORRECTION_TRIGGER` matches a
  leading "actually"/"no, actually"/"wait, actually" (only at the very
  start of a message, so "I actually love this" is untouched) followed
  by an explicit, unambiguous date/time signal (a weekday name,
  "tomorrow", "at <hour>", or "in <N> minutes" - never a loose bare
  number, which is only safe as a fallback when a verb phrase like
  "make it" is already the strong signal). A date-only correction with
  no time at all ("Actually Thursday") now keeps the referenced item's
  existing time-of-day instead of having nothing to combine with -
  `app/ai/chat_engine.py`'s `_reschedule_reference_reaction()` resolves
  the entity first specifically to make that possible. See
  `PROJECT_ARCHITECTURE.md` section 5l for the full writeup and
  `tests/test_intent.py`/`tests/test_conversation_state_integration.py`
  for coverage (mid-sentence "actually" and a bare "actually" with no
  date/time signal are both confirmed to fall through untouched, not
  misfire).

  One remaining, narrower scope note: this only covers reminders and
  tasks, which is everything `reschedule_reference` has ever supported
  - it does not yet extend to calendar events. Digging into this
  surfaced that calendar events aren't tracked as a reschedulable
  entity in `app/ai/conversation_state.py` AT ALL (only "reminder" and
  "task" are recognized `entity_type`s), and `_reschedule_reference_reaction()`
  has no Google Calendar update path even if they were. So the spec's
  own worked example as a literal end-to-end scenario - "Schedule
  Devika tomorrow at 5" (a calendar event) then "Actually Thursday" -
  still would not work today, even though the identical correction on
  a reminder or task does. Making it work needs Google Calendar's
  update-event API wired in, plus `remember_entity("calendar_event",
  ...)` after a successful creation - a real, separate, and
  calendar-API-touching piece of work, not a phrasing-recognition
  problem like the rest of this item was.

  **Update: this is now closed too.** `google_calendar.update_event()`
  (`events().patch()`, Google's own documented approach for a partial
  change - confirmed by checking Google's Calendar API docs directly
  rather than assuming) already existed at the tools layer, complete
  with the `confirmed=True` gate and post-write verification - the
  chat-layer wiring was the actual missing piece, not the calendar API
  integration itself. `_resolve_pending_action()`'s calendar-create
  branch now calls `convo.remember_entity("calendar_event", event_id,
  title)` on success, the same pattern already used for reminders/
  tasks/timers. `_reschedule_reference_reaction()` branches on
  `entity_type == "calendar_event"` into a new
  `_reschedule_calendar_event()`, which fetches the event's real
  current start/end from Google Calendar (never assumed), preserves its
  original DURATION on any correction (so "make it 5pm" on a 30-minute
  meeting doesn't silently become an hour), preserves its time-of-day
  on a date-only correction exactly like the reminder/task path, and -
  unlike reminders/tasks, which write immediately - proposes rather
  than writes, going through the exact same yes/no confirmation gate as
  a fresh calendar create/delete (a reschedule is just as consequential
  as either). An all-day event or one that's since been deleted both
  decline gracefully rather than guessing.

  One real bug surfaced and fixed while wiring this: a generic
  post-processing step in `handle_message()` unconditionally reset
  `pending_action` to `None` after every `_ACTION_HANDLERS` call, on
  the historically-true assumption that none of them ever needed to set
  one (they all write immediately). That's no longer true now that one
  of them proposes instead - fixed to trust the handler's own return
  value, the same way `conversation_state` already worked in the same
  spot. Every other handler in that group already returns
  `pending_action=None` on its own (the dataclass default, never
  touched), so this only changes behavior for the one case that
  actually needed it.

  See `tests/test_chat_engine.py` for the calendar-reschedule coverage
  (propose-then-confirm, confirmed write includes the preserved
  duration, date-only correction keeps the existing time, a deleted or
  all-day event declines gracefully, no recent event asks instead of
  guessing).
- **General confidence system** (section 13's HIGH/MEDIUM/LOW rule,
  named and reusable rather than inline) - `app/ai/confidence.py`.
  Before this, the exact same act/ask/ignore rule already existed, but
  only as two bare threshold constants
  (`app/ai/semantic_intent.CONFIDENCE_LOW`/`CONFIDENCE_ACT`) compared
  by hand in `app/ai/chat_engine.py`'s semantic-intent handling. This
  module pulls the thresholds and the resulting band into one small,
  independently-tested piece: a `Confidence` enum
  (`LOW`/`MEDIUM`/`HIGH`) and a `band(score)` classifier, using the
  exact same two numbers as before (still re-exported as
  `semantic_intent.CONFIDENCE_LOW`/`CONFIDENCE_ACT` for backward
  compatibility - `semantic_intent.py` now imports them from
  `confidence.py` rather than defining them itself, so there is one
  source of truth). `chat_engine.py`'s act/ask/ignore branch now reads
  `confidence.band(guess.confidence)` and switches on
  `Confidence.HIGH`/`MEDIUM`/`LOW` by name instead of raw `>=`
  comparisons - same behavior, now an explicit named rule any future
  decision point with a numeric confidence score can reuse. Today's one
  real caller is still `app/ai/semantic_intent.py`'s model-produced
  score; semantic memory's fact-confidence numbers are deliberately
  NOT run through this (see that module's docstring and sections 8-9
  above - a stored fact isn't an action to gate, it already has its own
  hedged-wording/ranking treatment).
- **Clarification policy** (section 14) - "use sensible defaults, ask
  only when necessary". Event duration (one hour), timezone (system
  local), and default-calendar selection already use sensible defaults
  without asking (`app/calendar/google_calendar.py`), so there was no
  further gap here beyond the confidence-aware wording above.
- **Reasoning budget** (section 18, "Do not spend expensive reasoning
  on simple messages") - `app/ai/reasoning_budget.py`. Mochi already
  gets most of the spec's LEVEL 0-4 idea for free from the intent
  router itself (a deterministic keyword match never touches a model;
  a keyword miss costs one small, bounded semantic-classification
  call; only a genuine miss on both reaches open-ended generation) - see
  that module's docstring for the full breakdown. The one gap: every
  message reaching the open-ended chat fallback got the same
  context-gathering work (`get_web_context()`, the semantic-memory
  facts lookup) even for a bare "lol"/"thanks"/"ok" that plainly has
  nothing for either to find. `is_trivial_chat()` checks a message
  against a fixed, deliberately small list of acknowledgments/reactions
  (never a length-only heuristic, so a short-but-substantive message
  like "i'm sad" is never mistaken for trivial); when it matches,
  `app/ai/chat_engine.py`'s unknown-intent branch passes `None` for
  both `web_context` and `user_facts` instead of calling
  `get_web_context()`/`_relevant_user_facts_context()`, and skips
  requesting LLM-based fact extraction for that message too (nothing to
  extract from a bare "thanks"). Deliberately narrow: not the spec's
  fuller vision of a model-tier router switching between a smaller/
  larger reasoning model or a model's own thinking-mode toggle - not
  applicable to Mochi's current single fixed model (qwen2.5:1.5b via
  Ollama), which has neither, and there's no second model installed to
  route into yet (`MOCHI_VERSIONED_ROADMAP.md` section 19).

That closes every Phase 3 item from the spec's own section 28 list.
The bare-"actually" phrasing gap flagged in an earlier version of this
doc is now fixed (see the "Correction handling" bullet above); the
model-tier-routing scope boundary on reasoning budget remains
intentional, not a gap that was missed. The one remaining item, noted
above under "Correction handling", is scoped narrower than a caveat -
reschedule corrections don't yet reach calendar events, a distinct,
calendar-API-touching piece of work.

## Phase 4 - PARTIALLY DONE (the actual cross-model comparison remains infrastructure-blocked, not a scoping choice)

Spec section 19: benchmark Qwen3-4B, Qwen3-8B, Phi-4-mini, and the
current model against each other on Mochi-specific tests (context
continuity, calendar/reminder understanding, ambiguity, corrections,
tool selection, latency, RAM, VRAM).

**What's now built: `benchmarks/`** - a permanent, versioned benchmark
dataset and runner (spec section 53's own request: "Create a permanent
Mochi benchmark dataset"), covering intent accuracy, tool correctness,
hallucination resistance, ambiguity handling, date/time reasoning,
reference resolution and corrections, calendar safety, and failure
handling - every category applicable to what Mochi has actually built
so far ("task prioritization" and "habit reasoning" aren't implemented
at all yet, so no cases exist for them - nothing to benchmark, not an
oversight). Every case was verified against real Mochi behavior before
being written down, not guessed at. `python -m benchmarks.harness` runs
the whole thing standalone (no pytest needed) and writes a JSON report
shaped around spec section 55's metric groups. A real baseline is
checked in at `benchmarks/results/baseline_deterministic_2026-09-14.json`:
16/16 (100%) on every category that could run against git commit
`894901b`. See `benchmarks/README.md` for the full picture and
`tests/test_benchmark_harness.py` for coverage of the harness's own
scoring logic.

**Why this baseline is real but isn't the comparison the spec actually
wants:** every case except the placeholder `conversation` one exercises
Mochi's DETERMINISTIC router (`app/ai/intent.py`/`app/ai/chat_engine.py`)
directly, never a model call - by design (spec section 60's own rule).
So this is a genuine, reproducible, model-independent baseline of
Mochi's own correctness, not a stand-in for comparing Qwen3-4B,
Qwen3-8B, Phi-4-mini, and the current model against each other.

**What's still not done, and confirmed - not assumed - to be
infrastructure-blocked in this development environment:** running the
actual comparison needs a live Ollama instance serving each candidate
model. Checked directly before writing this, not from memory:

```
$ curl -sS -D - -o /dev/null https://ollama.com
HTTP/2 403
x-deny-reason: host_not_allowed
```

(same result for `huggingface.co`; no Ollama binary is installed, no
Ollama server is reachable at `localhost:11434`, and there's no GPU
either). Current, confirmed official Ollama tags for the three
candidates are `qwen3:4b` (2.5 GB - the closest available tag to the
roadmap's specifically-named "Qwen3-4B-Thinking-2507"; the base
`qwen3:4b` supports a `think` parameter, or the exact 2507 checkpoint
can be pulled as a GGUF straight from Hugging Face - see
`benchmarks/README.md` for the exact command), `qwen3:8b` (5.2 GB), and
`phi4-mini` (2.5 GB, 3.8B params) - confirmed via web search rather
than assumed from training data, since exact current tags/sizes are
exactly the kind of thing that goes stale. `benchmarks/harness.py`'s
`--model`/`--host` flags make running the real comparison a single
command away the moment this runs somewhere Ollama access exists -
`benchmarks/README.md` has the exact commands. This remains a
genuinely open item, blocked on hardware/network access this
environment doesn't have, not on more engineering effort.

## Phase 5 - PARTIALLY DONE

Spec section 28: mood, expression state, initiative, proactive
reminders, presence behavior.

- **Mood and expression** (section 22: "the LLM may suggest emotional
  context, but the application owns the final expression state") - was
  already substantially satisfied by pre-existing architecture, just
  never previously connected back to this spec section in the docs.
  `app/ai/llm.py`'s replies already include an `emotion` field the
  model suggests; `app/ai/chat_engine.py`'s `_emotion_and_animation()`
  validates it (falling back to `Emotion.NEUTRAL` on anything not a
  real `Emotion` value) and maps it through `EMOTION_PROFILE` to decide
  the actual `CharacterState` - the model suggests, the app decides,
  exactly as the spec asks. Separately, deterministic application
  events already drive expression directly throughout the codebase
  without going through the model at all (a due reminder ->
  `CharacterState.ALERT`, an ignored reminder -> `ANGRY`, a "*_needs_time"
  clarifying question -> `CONFUSED` - see `app/reminders/notifications.py`
  and the many `DetectedIntent(..., animation=...)` sites in
  `app/ai/intent.py`), matching the spec's own success/failure/waiting
  examples. No new code needed here - this entry exists so the
  connection to this spec section is documented rather than looking
  unaddressed.
- **Initiative scoring** (section 23) - `app/ai/initiative.py`, new
  this phase. Implements the spec's own formula: an `InitiativeSignal`
  (importance/timeliness/user_benefit, each 0.0-1.0, plus
  recent-interruption count and a focus-mode flag) and a
  `should_initiate()` decision built on `score()`, discounted by
  interruption fatigue and a hard focus-mode penalty, defaulting to
  staying quiet on anything borderline. Deliberately NOT wired to any
  actual proactive channel yet - see the next two bullets for why, and
  that module's own docstring for the full reasoning.
- **Proactive reminders** (existing behavior, unaffected) -
  `app/reminders/notifications.py`'s `ReminderNotifier` already does
  this and predates this spec entirely: a due reminder always
  perks Mochi up, plays a sound, shows a speech bubble, and raises a
  desktop notification. It was deliberately left untouched rather than
  gated behind the new initiative score above - a reminder is something
  the user explicitly asked for at creation time, so silently
  suppressing it for "notification fatigue" would break a reliability
  promise (spec section 27), not improve one.
- **A new proactive channel (e.g. "an important calendar event is
  approaching")** - does not exist in Mochi at all, and was
  deliberately NOT built this session. Nothing currently polls the
  calendar for what's coming up. Building this well means answering
  product questions this spec doesn't answer on its own - how many
  minutes of lead time counts as "approaching", what counts as
  "important" enough to interrupt for, whether/how it respects focus
  mode - and guessing at those silently while "closing out a phase"
  felt like the wrong tradeoff versus building a real feature blind.
  `app/ai/initiative.py` is the scoring piece such a feature would call
  once those questions have real answers. This is a genuinely open
  item, not a technical blocker like Phase 4 - just one that needs a
  product conversation first.
- **Presence behavior** - not addressed; no further gap identified
  beyond what's covered above.

## Other known open items (outside phases 3-5, kept from before this session)

- **Multi-slot goals** (spec section 4's fuller multi-slot example).
  `goal_state.py` is deliberately scoped to exactly one missing slot per
  goal. A goal needing two or more clarifying questions in a row isn't
  implemented - no current intent actually needs it, and building the
  machinery without a real caller would be speculative and untested.
  The natural extension point if/when a multi-slot flow is added is
  `known_slots`/`awaiting` becoming a list rather than a single string.
- **Memory consolidation as its own distinct pipeline stage, and
  temporal-window contradiction metadata** (spec sections 8, 9).
  Semantic memory's contradiction handling uses a straight
  `status`/`superseded_by` chain rather than the spec's suggested
  `valid_from`/`valid_until` range metadata - sufficient for "what's
  true now" and "what did I used to think", without needing range
  queries. Consolidation (candidate extraction -> importance filter ->
  duplicate/contradiction detection -> storage) stays collapsed into
  `app/ai/fact_extraction.py` + `semantic_memory.remember_fact`'s
  supersede-by-subject logic rather than being pulled out into a
  separate multi-stage process - sufficient for both the deterministic
  and LLM-based extraction paths so far, deferred until a concrete need
  for a separate stage actually shows up.
- **Voice interface** (Phase 6) - out of scope for this spec entirely;
  tracked separately in `docs/ROADMAP.md`'s V1.2.
- **Fine-tuning** (section 25) - explicitly, deliberately not started,
  per the spec's own "No Immediate Fine-Tuning" section.

---

# Mochi Cognitive Intelligence Upgrade Specification

## Objective

Upgrade Mochi from a simple LLM chatbot into a reliable local desktop companion that can maintain context, understand incomplete and follow-up messages, remember useful information, reason about tasks, use tools safely, verify actions, and behave consistently as a persistent companion.

The goal is NOT to simply increase the model size.

The goal is to build a cognitive architecture around the language model so that a small local model can operate more intelligently and reliably.

---

# 1. Core Principle

The LLM is Mochi's reasoning engine, not Mochi itself.

Mochi consists of:

* language model
* conversation state
* task state
* goal state
* working memory
* episodic memory
* semantic/user memory
* procedural memory
* memory retrieval
* memory consolidation
* tool registry
* tool execution
* tool validation
* tool verification
* environment state
* confidence handling
* personality
* mood/expression state
* evaluation system

Do not place all intelligence inside the system prompt.

---

# 2. Cognitive Loop

Every meaningful interaction should conceptually follow:

USER INPUT
-> understand
-> build context
-> retrieve relevant memory
-> identify current goal
-> determine whether this is conversation, action, clarification, update, correction, or multiple simultaneously
-> estimate confidence
-> decide whether to answer, ask, or act
-> execute tools when required
-> verify tool results
-> update active state
-> update memory candidates
-> generate natural Mochi response
-> update expression/mood

Do not treat every user message as an independent request.

---

# 3. Active Conversation State

Maintain persistent state for the current interaction.

Example:

{
"active_goal": "create_calendar_event",
"status": "awaiting_information",
"slots": {
"title": "Meeting with Devika",
"person": "Devika",
"date": "tomorrow",
"time": null,
"duration": null
},
"missing_information": [
"time"
],
"last_message": "Schedule something with Devika tomorrow",
"confidence": 0.95
}

When the user says:

"5"

interpret it against the active state.

Do not process it as an isolated message.

---

# 4. Goal Stack

Represent unfinished user goals explicitly.

Example:

{
"goal": "create_calendar_event",
"subgoals": [
"resolve_date",
"resolve_time",
"confirm",
"create_event",
"verify_event"
],
"status": "awaiting_confirmation"
}

If the user supplies new information, update the existing goal rather than creating a new unrelated intent.

---

# 5. Interaction Types

Do not use a rigid single-label intent classifier as the primary cognitive mechanism.

Support overlapping interaction types:

* conversation
* information_request
* task_request
* task_update
* task_completion
* correction
* clarification
* preference
* memory_candidate
* emotional_expression
* follow_up
* reference_resolution

Multiple types may apply simultaneously.

Example:

"I finally finished the API."

May be:

* conversation
* project_update
* positive_emotion
* memory_candidate

Do not automatically create a task.

---

# 6. Context Builder

Before asking the model to make a decision, construct a compact context containing only relevant information.

Possible components:

* recent conversation
* active goal
* unresolved task
* relevant user facts
* relevant episodic memories
* relevant procedural rules
* recent tool results
* current date/time
* environment state
* current Mochi state
* applicable policies

Do not send the entire database to the model.

Do not retrieve memory merely because it is semantically similar.

Rank memory by:

* semantic relevance
* task relevance
* recency
* importance
* entity match
* confidence

---

# 7. Memory Architecture

Implement four memory layers.

## Working Memory

Short-lived context.

Contains:

* current conversation
* active goal
* unresolved questions
* recent tool results
* current state

Lifetime:
minutes to hours.

## Episodic Memory

Records useful experiences.

Example:

"User worked on Google Calendar integration and encountered OAuth problems."

Store:

* timestamp
* event
* context
* importance
* entities

## Semantic Memory

Stable user facts and preferences.

Example:

"User prefers concise reminders."

Store:

* fact
* confidence
* source
* created_at
* updated_at
* last_confirmed

## Procedural Memory

Behavioral rules and lessons.

Example:

"Calendar creation must be verified before Mochi claims success."

Store:

* rule
* scope
* confidence
* source
* last_updated

---

# 8. Memory Consolidation

Do not permanently save every conversation.

Pipeline:

conversation
-> memory candidate extraction
-> importance filter
-> confidence estimation
-> duplicate detection
-> contradiction detection
-> consolidation
-> memory storage

Do not save uncertain statements as facts.

Example:

"I might switch to Linux."

Store as:

"User is considering switching to Linux."

Do NOT store:

"User uses Linux."

---

# 9. Memory Contradiction Handling

Memory records must support temporal validity.

Recommended metadata:

* created_at
* updated_at
* valid_from
* valid_until
* confidence
* source

If:

"User uses Windows."

is followed later by:

"I switched to Linux."

the newer information should supersede the older current-state belief.

Do not return contradictory memories without resolution.

---

# 10. Tool Architecture

Tools must be explicitly registered.

Every tool should contain:

* name
* description
* input schema
* output schema
* permission level
* confirmation policy
* timeout
* retry policy
* verification method

Example:

calendar.create_event

Inputs:

* title
* start
* end
* timezone
* calendar_id

Output:

* event_id
* status
* actual_start
* actual_end

---

# 11. Tool Execution Pipeline

Never blindly execute raw model output.

Use:

LLM decision
-> schema validation
-> policy validation
-> confirmation check
-> tool execution
-> result validation
-> verification
-> state update
-> response

If the tool fails, Mochi must not claim success.

---

# 12. Tool Verification

For important side effects:

create event
create reminder
delete event
modify event

the system should verify the resulting state when practical.

Example:

calendar.create_event()
-> returns event_id
-> calendar.get_event(event_id)
-> confirm existence
-> only then tell the user it succeeded.

Never equate "tool call was generated" with "action succeeded."

---

# 13. Confidence System

Use confidence-aware behavior.

HIGH confidence:
-> act or answer.

MEDIUM confidence:
-> infer reasonable defaults and confirm if the action is consequential.

LOW confidence:
-> ask a clarification question.

Example:

"Schedule Devika tomorrow evening."

Known:

person = Devika
date = tomorrow
time = evening

Time is insufficiently precise.

Ask:

"What time tomorrow evening?"

Do not invent a specific time.

---

# 14. Clarification Policy

Mochi should minimize unnecessary questions.

Use sensible defaults for:

* event duration
* timezone
* default calendar
* reminder behavior
* title generation

Ask only when missing information materially affects the result.

Do not turn simple interactions into forms.

Bad:

"What should the title be?"
"Which calendar?"
"What timezone?"
"How long?"
"Should I notify you?"

Better:

Infer reasonable defaults and ask only when necessary.

---

# 15. Temporal Reasoning

Use deterministic application code for dates and times.

The LLM should interpret:

"tomorrow"
"next Thursday"
"this evening"
"in 20 minutes"

Application code should convert them into exact timestamps.

Do not rely on the LLM to calculate calendar dates.

---

# 16. Reference Resolution

Mochi must resolve:

* it
* that
* this
* same thing
* the meeting
* her
* tomorrow
* the one we discussed

using:

* recent conversation
* active task
* entities
* memory

Example:

User:
"Schedule Devika tomorrow."

Mochi:
"What time?"

User:
"5."

Interpret "5" as the time for the active Devika meeting.

---

# 17. Corrections

User corrections should modify active state.

Example:

User:
"Schedule Devika tomorrow at 5."

User:
"Actually Thursday."

Mochi should interpret this as a correction to the existing calendar task.

Do not create a second independent task.

---

# 18. Reasoning Budget

Use different reasoning levels.

LEVEL 0:
simple conversation.

LEVEL 1:
simple tool calls.

LEVEL 2:
contextual follow-ups.

LEVEL 3:
multi-step planning.

LEVEL 4:
complex reasoning/research.

Do not spend expensive reasoning on simple messages.

If using a model supporting hybrid thinking, use non-thinking mode for ordinary conversation and thinking mode selectively for difficult tasks.

---

# 19. Model Strategy

Benchmark at least:

* Qwen3-4B
* Qwen3-8B
* Phi-4-mini
* current Mochi model

Do not select based only on generic benchmark scores.

Evaluate on Mochi-specific tests:

* context continuity
* calendar understanding
* reminder understanding
* ambiguity
* corrections
* reference resolution
* memory recall
* tool selection
* tool arguments
* failure handling
* natural conversation
* latency
* RAM
* VRAM

Start with Qwen3-4B as the low-resource candidate and Qwen3-8B as the higher-quality candidate.

Do not automatically replace the current model until architecture-level improvements have been tested.

---

# 20. Response Generation

Separate reasoning from final personality expression.

Internal decision:

{
"action": "calendar.create_event",
"status": "success",
"event": {
"title": "Meeting with Devika",
"time": "17:00"
}
}

Response layer converts this into natural Mochi language.

Do not make the model simultaneously solve the task and perform elaborate personality writing.

---

# 21. Personality

Mochi should be:

* cute
* warm
* concise
* naturally curious
* slightly playful
* emotionally responsive
* not childish in every sentence
* not excessively enthusiastic
* not robotic
* not constantly mentioning that it is an AI

Personality must never override correctness.

Never fabricate emotions, actions, memories, or tool results for the sake of personality.

---

# 22. Mood and Expression

Expression state should primarily be controlled by application events.

Examples:

success -> happy
tool failure -> confused
waiting for clarification -> thinking
important notification -> alert
user praise -> happy/proud
long idle -> idle
wake -> wake expression

The LLM may suggest emotional context, but the application owns the final expression state.

---

# 23. Initiative

Mochi may proactively communicate only when an initiative policy permits it.

Examples:

* important calendar event approaching
* reminder due
* user-requested monitoring condition satisfied

Do not randomly interrupt the user.

Use an initiative score based on:

importance
+
timeliness
+
user benefit
------------

## recent interruption

## focus mode

notification fatigue

---

# 24. Learning From Failure

Store useful lessons from failures as procedural memory.

Example:

Calendar authentication failed.

Do not learn:

"Calendar is broken."

Learn:

"Calendar authentication expired; future calendar operations should check authentication state."

Use lessons to improve future decisions without immediately fine-tuning the model.

---

# 25. No Immediate Fine-Tuning

Do not fine-tune until:

1. cognitive architecture is implemented
2. failure cases are collected
3. failure causes are classified
4. benchmark exists
5. sufficient real interaction data exists

Many apparent model problems are actually context, state, memory, or tool-integration problems.

---

# 26. Evaluation Framework

Create automated tests for:

## Context

* follow-up messages
* incomplete messages
* pronouns
* corrections

## Calendar

* create
* update
* delete
* conflict handling
* confirmation

## Reminders

* relative time
* exact time
* vague time
* modification

## Memory

* save
* retrieve
* ignore
* update
* contradiction

## Tool Reliability

* malformed arguments
* failed tool
* timeout
* permission failure
* successful verification

## Conversation

* casual chat
* emotion
* topic changes
* interruptions

## Companion Behavior

* initiative
* personality consistency
* expression consistency

---

# 27. Reliability Rules

Mochi must follow these rules:

1. Never claim an action succeeded without evidence.
2. Never invent calendar/reminder state.
3. Never treat a follow-up message as unrelated without checking active context.
4. Never save uncertain information as certain memory.
5. Never expose private chain-of-thought.
6. Never ask unnecessary clarification questions.
7. Never perform consequential actions without required confirmation.
8. Never allow personality to override correctness.
9. Never rely on the LLM for deterministic date/time calculations.
10. Never assume a tool call equals successful execution.

---

# 28. Implementation Priority

Implement in this order:

PHASE 1

* active conversation state
* task state
* goal stack
* context builder
* structured decision output
* tool validation
* tool verification

PHASE 2

* working memory
* semantic memory
* episodic memory
* procedural memory
* retrieval
* consolidation
* contradiction handling

PHASE 3

* confidence system
* clarification policy
* reference resolution
* correction handling
* reasoning budget

PHASE 4

* model benchmark
* Qwen3-4B
* Qwen3-8B
* Phi-4-mini

PHASE 5

* mood
* expression state
* initiative
* proactive reminders
* presence behavior

PHASE 6

* voice interface

---

# 29. Definition of Success

Mochi should be considered intelligent when it can reliably demonstrate:

1. "5" correctly continues an unfinished calendar conversation.
2. "Actually Thursday" modifies the existing task.
3. "Do that tomorrow" resolves the referenced task.
4. It asks only necessary questions.
5. It remembers stable user preferences.
6. It updates outdated memories.
7. It does not hallucinate completed actions.
8. It verifies tool results.
9. It distinguishes casual conversation from actionable requests.
10. It knows when additional reasoning is worthwhile.
11. It behaves consistently across sessions.
12. It can proactively help without becoming annoying.
13. Its personality remains natural while its decisions remain reliable.

The objective is not to make Mochi look like a large language model.

The objective is to make Mochi behave like a persistent, context-aware, reliable desktop companion.
