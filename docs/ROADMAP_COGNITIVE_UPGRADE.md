# Mochi Cognitive Intelligence Upgrade

Status: Phase 1 done; Phase 2 partially implemented (semantic memory and
episodic memory - see "Implementation status" below). Working memory's
role is covered by existing modules rather than a dedicated one (see
below); procedural memory and consolidation-as-a-distinct-pipeline are
not started. Confidence system, reasoning budget, model benchmarking,
mood/initiative, and voice (phases 3-6) are also not started.
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

**Deferred** (not yet built - noted here so it isn't rediscovered as a
gap by accident; roughly spec sections 5-9, 13-15, 18-19, 21-29's
remaining scope):

- **Multi-slot goals.** `goal_state.py` is deliberately scoped to
  exactly one missing slot per goal. A goal needing two or more
  clarifying questions in a row (spec section 4's fuller multi-slot
  example) isn't implemented - no current intent actually needs it, and
  building the machinery without a real caller would be speculative and
  untested. The natural extension point if/when a multi-slot flow is
  added is `known_slots`/`awaiting` becoming a list rather than a single
  string.
- **Working memory as a single dedicated module, procedural memory, and
  memory consolidation as a distinct pipeline** (sections 6, 7, 8) -
  semantic and episodic memory (two of the four layers) are implemented,
  see above. Working memory's role is currently split across
  `app/ai/conversation_state.py` (short-lived entity/reference memory)
  and `app/ai/goal_state.py` (Phase 1's active-goal state) rather than a
  single unified module - functionally similar to the spec's
  description, just not built as one dedicated piece. Procedural memory
  (behavioral rules learned from failures, spec section 24 - e.g.
  "calendar authentication expired; check auth state before trying
  again") doesn't exist yet, even though the tool-verification failures
  from Phase 1 (`ToolValidationError`) would be a natural source for it.
  Consolidation as its own pipeline stage (candidate extraction ->
  importance filter -> duplicate/contradiction detection -> storage) is
  collapsed into `app/ai/fact_extraction.py` + `semantic_memory
  .remember_fact`'s supersede-by-subject logic rather than being a
  separate multi-stage process - sufficient for the deterministic-
  pattern extraction actually implemented, but would need real design
  work if/when LLM-based candidate extraction (see the next bullet) is
  ever added.
- **LLM-based (rather than pattern-based) fact/memory candidate
  extraction.** `app/ai/fact_extraction.py` is deliberately regex-only -
  see that module's docstring for why (no synchronous model call per
  message, no background-job infrastructure yet either). This means
  Mochi will never infer a fact from indirect phrasing the way a human
  (or an LLM) would - "I finally finished the API and my back hurts"
  produces nothing, on purpose (a missed inference is safe; a wrong one
  stored as a confident fact is not). Revisiting this is real future
  work, not an oversight.
- **Confidence system and clarification policy as a general mechanism**
  (sections 13-14) - today's clarification is per-intent and rule-based
  (ask when a slot is missing), not a scored HIGH/MEDIUM/LOW confidence
  gate applied uniformly across every decision.
- **Reasoning budget / hybrid thinking mode selection** (section 18) -
  Mochi's local LLM is invoked the same way regardless of message
  complexity; there's no LEVEL 0-4 routing.
- **Model benchmark suite and Qwen3-4B/8B/Phi-4-mini comparison**
  (section 19) - no formal Mochi-specific benchmark dataset exists yet.
- **Mood/expression driven by cognitive state, initiative/proactive
  communication, learning-from-failure as stored procedural memory**
  (sections 22-24) - expression state is driven by existing
  success/failure/waiting events (see `PROJECT_ARCHITECTURE.md`'s
  expression system docs), not by a dedicated cognitive-loop layer; there
  is no initiative-scoring mechanism.
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
