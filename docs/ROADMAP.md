# Mochi — Versioned Product & Engineering Roadmap

> **Where the codebase is right now: V1.0 — Correct Assistant** (see
> section 7 below for what that phase covers, and section 56 "Release
> gates" for what has to be true before moving to V1.2). This file is the
> full versioned plan — V1.0 → V1.2 → V2.0 → V2.1 → V3.0 → V3.1 — kept in
> the repo so architecture decisions can be checked against it. When this
> roadmap changes, update this file, then check whether
> `PROJECT_ARCHITECTURE.md` and `README.md` still match it.

> A privacy-first, local desktop companion that starts as a reliable assistant and progressively learns the user's context, routines, and preferences.
>
> **Core evolution:** Correctness → Voice & Always-On → Observation → ML → Personal Learning → Deep Learning

---

## 1. Product Vision

Mochi is not intended to be "one giant AI model running all the time."

The long-term architecture is a **local orchestration system**:

```text
User
  │
  ▼
Mochi Router
  │
  ├── Deterministic Tools
  │     ├── Reminder
  │     ├── Task
  │     ├── Timer
  │     ├── Calendar
  │     ├── Notes
  │     └── System
  │
  ├── Small Specialist Models
  │     ├── Intent / extraction
  │     ├── Simple conversation
  │     └── Voice-related tasks
  │
  ├── Context Engine
  │     ├── Activity
  │     ├── System state
  │     ├── Calendar
  │     └── User history
  │
  └── Reasoning Engine
        ├── Planning
        ├── Habit interpretation
        ├── Prediction
        └── Complex decisions
```

The most important design rule is:

> **The LLM should reason about actions; it should not be trusted to directly perform actions.**

Python/native tools validate and execute real-world changes.

---

# 2. Long-Term Development Philosophy

Mochi should become smarter without becoming unnecessarily heavier.

### Principle 1 — Deterministic where possible

If a normal function can answer something reliably, do not use an LLM.

Examples:

- Current time
- Reminder creation
- Timer
- Task completion
- Battery state
- Charging state
- Database lookup

### Principle 2 — Small model before large model

Use the smallest model capable of the task.

```text
Rules / Python
      ↓
Tiny model
      ↓
Small reasoning model
      ↓
Larger reasoning model
```

### Principle 3 — Load models on demand

Mochi should not keep every model in RAM.

```text
Mochi running
    ↓
Core + database + lightweight router
    ↓
Task requiring AI?
    ├── No → stay lightweight
    └── Yes → load required model
                    ↓
                 respond
                    ↓
             unload / keep warm briefly
```

### Principle 4 — Never train on raw secrets

Mochi should learn behavioral metadata, not passwords, keystrokes, private message contents, or sensitive credentials.

### Principle 5 — User feedback is evidence, not truth

A user's correction should improve future behavior, but it should be stored as feedback and evaluated rather than blindly treated as a perfect label.

### Principle 6 — Confidence controls autonomy

```text
0–50%       Observe only
50–75%      Soft suggestion / ask
75–90%      Strong suggestion
90%+        Automatic action only when safe
```

The exact thresholds must be calibrated using real validation data.

---

# 3. Shared Technology Stack

## Core

| Purpose | Primary | Alternative | Why |
|---|---|---|---|
| Language | Python 3.10+ | Rust/TypeScript for future performance-sensitive pieces | Existing project and strong ML ecosystem |
| Desktop UI | PySide6 / Qt | Tauri | Cross-platform and already used |
| Database | SQLite | DuckDB | SQLite is ideal for transactional local app state |
| Configuration | python-dotenv | TOML/configparser | Simple local configuration |
| Logging | Python logging | structlog | Standard library is sufficient |
| Tests | pytest | unittest | Existing Python ecosystem |
| Lint | Ruff | Flake8 + Black | Fast and simple |

PySide6 documentation: https://doc.qt.io/qtforpython/

SQLite: https://sqlite.org/

Python: https://www.python.org/

---

# 4. Local AI Stack

## Primary LLM runtime

### Ollama

Use Ollama initially because it is simple for development and local model management.

Source:
https://ollama.com/

Alternative:

### llama.cpp

Better when Mochi eventually needs tighter control over memory, quantization, GPU/CPU offload, and packaged distribution.

Source:
https://github.com/ggml-org/llama.cpp

---

# 5. Recommended Model Ladder

## Small / routing model

Candidate:

### Qwen3 small models

Use the smallest Qwen3 model that passes Mochi's intent/extraction tests.

Source:
https://github.com/QwenLM/Qwen3

Alternative:

### Phi small models

Source:
https://huggingface.co/microsoft

These should handle:

- Intent classification
- Entity extraction
- Simple conversational requests
- Structured JSON extraction

Many V1 commands should still use **no model at all**.

---

## Reasoning model

### Primary: Qwen3-4B-Thinking-2507

This is the first serious Mochi reasoning candidate.

Use for:

- Multi-step reasoning
- Task prioritization
- Calendar/task interpretation
- Habit analysis
- Complex answers
- Planning

Source:
https://huggingface.co/Qwen/Qwen3-4B-Thinking-2507

Alternative:

### Microsoft Phi reasoning models

Source:
https://huggingface.co/microsoft

A second candidate should be benchmarked rather than assumed to be better.

---

## Future larger reasoning model

### Qwen3 MoE / larger thinking models

Consider this only after V2/V3 proves that larger reasoning actually improves Mochi.

Source:
https://github.com/QwenLM/Qwen3

Alternative:

Other locally runnable MoE/reasoning models evaluated on Mochi's own benchmark suite.

**Do not make a 14B/30B model a hard requirement for Mochi.**

---

# 6. Quantization Strategy

For local desktop use:

```text
Q4_K_M
   ↓
Default efficiency target

Q5_K_M
   ↓
Quality upgrade if hardware allows

Q8
   ↓
Only when memory is abundant
```

Start with Q4-class quantization and measure actual quality.

Do not choose a quantization solely from benchmark numbers.

The real benchmark is:

> "Does Mochi understand and execute the user's requests correctly?"

---

# 7. V1.0 — Correct Assistant

## Goal

Make Mochi **reliable before making it intelligent**.

This is the current development phase.

### Features

- Chat
- Reminders
- Tasks
- Timers
- Local SQLite storage
- Calendar integration
- Character reactions
- Local LLM fallback
- Hallucination prevention
- Answer correction
- Ambiguous request handling
- Deterministic tool execution

---

## V1 architecture

```text
User
 │
 ▼
Chat
 │
 ▼
Intent Router
 │
 ├── deterministic intent
 │       ↓
 │    validated tool
 │       ↓
 │    SQLite / Calendar
 │
 └── unknown / conversational
         ↓
      local LLM
         ↓
      response only
```

The LLM must NOT directly write to:

- reminders
- tasks
- timers
- calendar

It can propose an action; the application validates it.

---

## V1 database

Keep the existing database and expand it carefully.

Recommended logical entities:

```text
settings
reminders
tasks
timers
calendar_events_cache
conversations
messages
interaction_events
intent_events
tool_calls
feedback
```

### `tool_calls`

Store:

```text
timestamp
tool_name
request_id
input
validation_result
execution_result
success
error
```

This becomes valuable for debugging and later ML.

### `feedback`

Store:

```text
timestamp
prediction_or_response_id
user_feedback
feedback_type
context
```

---

## V1 hallucination strategy

### Rule

If the answer depends on real application state:

```text
ASK DATABASE
```

not:

```text
ASK LLM
```

Example:

> "Did you set my reminder?"

Correct pipeline:

```text
User
 ↓
Intent
 ↓
Reminder lookup
 ↓
Real result
 ↓
Mochi response
```

Never:

```text
User
 ↓
LLM
 ↓
"I think I did!"
```

---

## V1 model

### Normal command handling

Prefer:

**No LLM**

### Simple language understanding

Candidate:

**Qwen3 small**

### Open-ended chat

Candidate:

**Qwen3 small instruct**

### Reasoning

Do not require a large reasoning model yet.

---

## V1 training

**No model training initially.**

Instead build a test dataset.

Example:

```json
{
  "input": "remind me to call mom at seven",
  "expected_intent": "create_reminder",
  "expected_entities": {
    "text": "call mom",
    "time": "19:00"
  }
}
```

Build hundreds of examples covering:

- Different wording
- Typos
- Missing words
- Ambiguous references
- Relative dates
- Negative commands
- Corrections
- Follow-up questions
- Hallucination traps

This dataset is more valuable than premature fine-tuning.

---

## V1 acceptance criteria

Mochi should:

1. Never claim a tool action happened when it did not.
2. Ask for clarification when multiple records match.
3. Answer database questions from the database.
4. Handle typos and natural phrasing.
5. Survive missing Ollama.
6. Never freeze the UI because of AI.
7. Never require a cloud API.
8. Preserve existing character behavior.

---

# 8. Base Hardware Requirements

## Absolute baseline for V1

```text
CPU: 2 cores / 4 threads
RAM: 4 GB
Storage: 1 GB free
GPU: Not required
Internet: Not required
```

This baseline is for the desktop application and deterministic features, not a heavy local reasoning model.

## Recommended V1

```text
CPU: 4+ cores
RAM: 8 GB
SSD: Recommended
GPU: Optional
```

## Recommended V1.2 with local AI + voice

```text
CPU: 4+ cores
RAM: 8 GB minimum
RAM: 16 GB recommended
GPU: Optional
Microphone: Required for voice input
Speaker/headphones: Required for voice output
SSD: Recommended
```

## Recommended V2+

```text
CPU: 6+ cores
RAM: 16 GB
SSD: Recommended
GPU: Optional
```

GPU acceleration is an optimization, not a product requirement.

---

# 9. V1.2 — Voice + Always-On Mochi

## Goal

Make Mochi feel like a desktop companion that is available from startup while remaining lightweight.

---

## Startup behavior

```text
PC starts
   ↓
Mochi core starts
   ↓
SQLite opens
   ↓
Schedulers start
   ↓
UI starts
   ↓
AI models remain unloaded
```

Only load models when required.

---

## Voice pipeline

```text
Microphone
    ↓
Wake/activation
    ↓
Speech-to-text
    ↓
Intent Router
    ↓
Tool OR LLM
    ↓
Text response
    ↓
Text-to-speech
```

---

# 10. Speech-to-text

## Primary candidate

### whisper.cpp

Local Whisper implementation optimized for efficient inference.

Source:
https://github.com/ggml-org/whisper.cpp

Alternative:

### faster-whisper

Python/CTranslate2 implementation.

Source:
https://github.com/SYSTRAN/faster-whisper

For Mochi, start with a small Whisper model and benchmark latency.

---

# 11. Text-to-speech

Possible local options:

### Piper

Source:
https://github.com/rhasspy/piper

Alternative:

### Kokoro

Source:
https://github.com/hexgrad/kokoro

Use a lightweight local voice first.

The objective is:

```text
low latency
small memory footprint
pleasant voice
offline operation
```

not maximum voice quality.

---

# 12. V1.2 Low-Compute Strategy

Never keep every model loaded.

### Always resident

```text
Mochi core
SQLite
event loop
UI
schedulers
lightweight router
```

### On demand

```text
STT
TTS
LLM
reasoning model
embedding model
```

Possible lifecycle:

```text
User speaks
 ↓
Load STT
 ↓
Transcribe
 ↓
Release / sleep STT
 ↓
Route
 ↓
If simple → tool
If complex → reasoning model
 ↓
Generate
 ↓
TTS
 ↓
Release / sleep TTS
```

---

# 13. V1.2 Permissions

## Windows

Potential permissions/capabilities:

- Microphone access
- Startup/background application permission
- Notifications
- Optional system integration

The application should request only what it needs.

## macOS

Potential permissions:

- Microphone
- Notifications
- Login item/background startup
- Accessibility permission only if a later feature genuinely needs to inspect/control other applications
- Screen Recording only if screen capture is ever implemented

**Do not request Accessibility or Screen Recording in V1.2 unless there is a concrete feature requiring it.**

---

# 14. V2.0 — Observation Engine

## Goal

Mochi becomes context-aware.

This version is about:

> **Observe → Normalize → Store**

Not prediction.

---

# 15. Activity collection

## Primary

### ActivityWatch

Source:
https://activitywatch.net/

Documentation:
https://docs.activitywatch.net/

Useful capabilities:

- Active application
- Window title
- AFK/idle
- Activity timeline
- Local storage
- Cross-platform operation

Alternative:

### Custom collectors

Windows:
- Win32 APIs
- Power/session notifications
- Native window APIs

macOS:
- NSWorkspace
- Accessibility APIs where required
- IOKit / native power information where appropriate

Use custom collectors only when ActivityWatch does not provide a needed signal.

---

# 16. V2 event model

Recommended normalized event:

```json
{
  "timestamp": "...",
  "event_type": "window_change",
  "application": "VS Code",
  "window_title": "main.py",
  "duration_seconds": 420,
  "idle_seconds": 0,
  "battery_percent": 67,
  "is_charging": false,
  "screen_state": "on",
  "session_state": "unlocked",
  "source": "activitywatch"
}
```

---

# 17. Human Activity State

Create a context layer:

```text
ACTIVE_WORK
ACTIVE_BROWSING
COMMUNICATION
MEETING
READING
MEDIA
AFK
LIKELY_AWAY
LOCKED
SLEEPING
UNKNOWN
```

Initially use rules.

Example:

```text
VS Code + input
      ↓
ACTIVE_WORK
```

Do not claim:

```text
VS Code + no input = unproductive
```

No-input could mean:

- reading
- watching
- thinking
- away
- waiting for a build
- attending a meeting

Context matters.

---

# 18. V2 privacy

Never collect:

- Passwords
- Keystroke contents
- Clipboard contents by default
- Private message contents
- Authentication tokens
- Screen recordings by default
- Screenshots by default

Track **metadata about activity**, not the user's actual private content.

Example:

```text
GOOD:
VS Code active for 42 minutes

BAD:
Every key typed into VS Code
```

---

# 19. V2 database

Add:

```text
activity_events
application_sessions
idle_sessions
system_events
power_events
screen_events
sleep_events
daily_summary
session_summary
application_summary
```

Keep raw events and derived summaries separate.

---

# 20. V2 data retention

Provide configuration:

```text
Raw events:
7 / 30 / 90 / 365 days / forever

Aggregated summaries:
Longer retention
```

Allow the user to delete all collected activity.

A local assistant should make deletion easy.

---

# 21. V2.1 — Machine Learning

## Goal

Move from:

> "Mochi observes."

to:

> "Mochi recognizes patterns and makes cautious predictions."

---

# 22. First ML models

Do not start with deep learning.

Use classical ML.

### Classification

Candidate:

**scikit-learn**

Source:
https://scikit-learn.org/

Alternatives:

**XGBoost**
https://xgboost.readthedocs.io/

**LightGBM**
https://lightgbm.readthedocs.io/

---

# 23. ML tasks

## A. Activity classification

Input:

```text
hour
day_of_week
application
window_category
session_duration
idle_duration
previous_application
recent_application_sequence
```

Target:

```text
activity_state
```

Start with:

- Random Forest
- Gradient Boosting
- Logistic Regression baseline

---

## B. Anomaly detection

Candidate:

### Isolation Forest

Detect unusual behavior compared with the user's own history.

Example:

```text
Normal:
weekday active 09:00–18:00

Observed:
active until 02:30
```

Output:

```text
unusual = true
```

Do not call this:

> "Burnout detected."

Call it:

> "Routine deviation."

---

# 24. C. Next-context prediction

Start with:

```text
Markov / transition model
```

Then compare with:

```text
Random Forest
Gradient Boosting
```

Later:

```text
sequence neural network
```

Do not start with a transformer.

---

# 25. V2.1 confidence system

Every prediction should produce:

```json
{
  "prediction": "likely_break",
  "confidence": 0.63,
  "evidence": [
    "current session 95 minutes",
    "normal break interval 62 minutes"
  ]
}
```

The confidence value must be calibrated against validation data.

Do not interpret:

```text
0.63
```

as automatically meaning:

> "63% probability the model is correct"

unless the model has been properly calibrated.

Use calibration techniques such as:

- Platt scaling
- Isotonic regression

when appropriate.

---

# 26. User feedback loop

Example:

```text
Mochi:
"You usually take a break around now. Want one?"

User:
"No"
```

Store:

```text
prediction
confidence
context
action
user_feedback
```

Later:

```text
prediction
      ↓
feedback
      ↓
training dataset
      ↓
model retraining
      ↓
validation
      ↓
new model
```

Never replace the active model without validation.

---

# 27. V2.1 Training pipeline

```text
Raw data
   ↓
Cleaning
   ↓
Feature extraction
   ↓
Train / validation / test split
   ↓
Baseline model
   ↓
Evaluate
   ↓
Calibration
   ↓
Save model
   ↓
Shadow testing
   ↓
Deploy
```

Use **time-based splits** for behavioral data.

Do not randomly mix future data into training data.

Example:

```text
January–April → training
May           → validation
June          → test
```

This better reflects real-world prediction.

---

# 28. Model versioning

Store:

```text
model_name
model_version
training_date
training_data_range
feature_schema_version
metrics
```

Example:

```text
habit_classifier_v0.3
trained: 2026-09-01
data: 2026-07-01 → 2026-08-31
accuracy: ...
f1: ...
```

Never silently overwrite a model.

---

# 29. V2.1 Reinforcement Learning Preparation

Do not implement full RL immediately.

First collect the required signals:

```text
state
action
response
reward proxy
```

Example:

```text
State:
2h focused work

Action:
suggest break

Response:
user accepts

Reward:
positive
```

Possible reward signals:

- Accepted suggestion
- Dismissed suggestion
- Repeated dismissal
- User explicitly says "not useful"
- User explicitly says "good suggestion"
- User changes behavior after suggestion

Only later should these become an RL policy.

---

# 30. V3.0 — Personal Learning System

## Goal

Mochi maintains a continuously evolving model of the user.

---

# 31. Weekly learning cycle

Run only when appropriate:

```text
Computer idle
OR
user explicitly allows learning
OR
plugged in
```

Pipeline:

```text
Weekly data
    ↓
Quality checks
    ↓
Aggregate
    ↓
Compare with historical baseline
    ↓
Pattern discovery
    ↓
Prediction evaluation
    ↓
Feedback processing
    ↓
Model retraining
    ↓
Validation
    ↓
Deploy if improved
```

---

# 32. Pattern discovery

Possible techniques:

### Clustering

scikit-learn:

- KMeans
- DBSCAN
- HDBSCAN alternative

HDBSCAN:
https://github.com/scikit-learn-contrib/hdbscan

Use to discover recurring behavior groups.

Example:

```text
Morning deep work
Afternoon meetings
Evening browsing
```

---

# 33. Personal routine model

Mochi can eventually learn:

```text
Typical wake time
Typical work start
Typical deep-work period
Typical break interval
Typical meeting period
Typical shutdown
Typical late-night activity
```

But these are **probabilistic patterns**, not hard-coded facts.

---

# 34. V3.0 memory architecture

Separate:

### Raw memory

What happened.

### Semantic memory

What Mochi has learned.

### Preference memory

What the user explicitly said they prefer.

### Behavioral memory

Patterns inferred from activity.

Example:

```text
RAW:
VS Code 09:00–10:00

DERIVED:
Focused work

PATTERN:
User frequently performs focused work around 09:00

CONFIDENCE:
0.84
```

This distinction prevents inferred behavior from being mistaken for explicit user facts.

---

# 35. V3.0 Personal Reasoning

The reasoning model receives summaries, not raw logs.

Bad:

```text
50,000 events → LLM
```

Good:

```json
{
  "period": "last_30_days",
  "coding_average": "2h32m/day",
  "browser_average": "1h03m/day",
  "normal_work_start": "09:21",
  "current_session": "2h11m",
  "historical_break_average": "61m",
  "current_break_gap": "103m"
}
```

Then the reasoning model can interpret it.

---

# 36. V3.1 — Deep Learning

## Goal

Use deep learning only when the amount and quality of personal data justify it.

Potential areas:

- Sequential behavior prediction
- Time-series forecasting
- Learned user representations
- Personalized recommendation
- Multi-event context understanding
- Long-horizon pattern detection

---

# 37. Deep learning stack

Primary:

### PyTorch

Source:
https://pytorch.org/

Alternative:

### TensorFlow

Source:
https://www.tensorflow.org/

Use PyTorch unless a specific deployment reason favors TensorFlow.

---

# 38. Deep learning progression

Do not jump directly to a large transformer.

Progress:

```text
Classical ML
   ↓
Feature-based neural network
   ↓
MLP
   ↓
GRU/LSTM for sequences
   ↓
Transformer/time-series model
```

Benchmark every stage against a simpler baseline.

If Random Forest performs equally well, keep Random Forest.

---

# 39. Deep learning training

Use rolling time windows.

Example:

```text
Train:
weeks 1–12

Validate:
week 13

Test:
week 14
```

Then periodically roll forward.

This avoids leaking future behavior into training.

---

# 40. Hardware for V3.1

Deep learning should preferably run:

```text
while idle
+
plugged in
```

GPU is strongly recommended for serious local training, but inference may still run on CPU depending on model size.

Potential hardware target:

```text
RAM: 16–32 GB
CPU: 6–12+ cores
GPU: optional for inference
GPU: recommended for training
SSD: strongly recommended
```

Mochi should still remain usable on weaker hardware by disabling heavy training.

---

# 41. Permission Model

Mochi should follow **least privilege**.

Never ask for every permission at installation.

Request permissions only when the user enables the feature.

---

# 42. V1 permissions

### Required

- Application execution
- Local file/database access inside Mochi's own data directory

### Optional

- Notifications

### Calendar

Only when the user enables Calendar.

---

# 43. Google Calendar permissions

Use OAuth.

Default:

```text
calendar.readonly
```

Only request write access when calendar writing is enabled.

The current repository already follows this principle, with read-only access by default and explicit confirmation for writes. fileciteturn4file2

Source:
https://developers.google.com/calendar/api

OAuth:
https://developers.google.com/identity/protocols/oauth2

Never store a Google password.

Store the OAuth token securely/local-only.

---

# 44. V1.2 microphone permissions

Request microphone permission only when voice is enabled.

Windows:
- Windows Privacy → Microphone access

macOS:
- System Settings → Privacy & Security → Microphone

Do not continuously record audio.

Recommended architecture:

```text
Microphone
 ↓
wake/activation
 ↓
short capture
 ↓
STT
 ↓
discard raw audio
```

Unless the user explicitly chooses otherwise.

---

# 45. V2 Windows permissions

For application/window observation, use the minimum OS capability needed.

Potential APIs:

- Win32 window APIs
- Session/power notifications
- Battery/power APIs

Source:
https://learn.microsoft.com/windows/win32/

For system sleep/power events, use Windows power/session APIs rather than polling aggressively.

---

# 46. V2 macOS permissions

Potential capabilities:

- Application/window observation
- Accessibility permission when required
- Power/session notifications

Apple documentation:
https://developer.apple.com/documentation/appkit/nsworkspace

If Accessibility permission is required:

```text
System Settings
 → Privacy & Security
 → Accessibility
 → enable Mochi
```

Only request this after explaining why.

---

# 47. Screen Recording permission

This should be **OFF by default and absent unless explicitly needed**.

If future Mochi functionality requires screenshots or screen understanding:

macOS:
```text
System Settings
 → Privacy & Security
 → Screen & System Audio Recording
```

Windows:
use the appropriate Windows capture APIs.

But do not add screen capture merely because it is technically possible.

---

# 48. Data permission philosophy

Mochi should clearly distinguish:

```text
Permission to observe metadata
```

from:

```text
Permission to access content
```

Examples:

### Low sensitivity

```text
VS Code active for 40 minutes
```

### High sensitivity

```text
Text typed into VS Code
```

Mochi should default to the first.

---

# 49. Local security

Use:

```text
SQLite
+
OS-protected storage for secrets
+
filesystem permissions
```

Never place:

- API secrets
- OAuth secrets
- passwords

inside the activity database.

---

# 50. Optional encryption

If activity data becomes highly detailed, consider:

### SQLCipher

Source:
https://www.zetetic.net/sqlcipher/

Alternative:

Encrypt selected sensitive records using a platform keychain/credential store.

Do not encrypt everything prematurely if it creates unnecessary complexity in V1.

---

# 51. Background compute policy

Mochi should have a resource manager:

```text
ResourcePolicy
```

Possible states:

```text
INTERACTIVE
IDLE
LOW_POWER
CHARGING
SLEEP
```

Rules:

```text
INTERACTIVE:
    minimal computation

LOW_POWER:
    no heavy models

CHARGING + IDLE:
    allow learning

SLEEP:
    stop active processing
```

This becomes extremely important in V2/V3.

---

# 52. Model loading policy

A future model manager:

```text
ModelManager
├── router model
├── reasoning model
├── STT model
├── TTS model
└── embedding model
```

Each model should have:

```text
load()
unload()
is_loaded()
memory_estimate
priority
```

Example:

```text
Qwen3 small:
priority = high
memory = low

Qwen3 4B Thinking:
priority = medium
memory = moderate

Deep reasoning model:
priority = low
memory = high
```

---

# 53. Model evaluation

Create a permanent Mochi benchmark dataset.

Categories:

```text
Intent
Tool correctness
Hallucination resistance
Ambiguity
Date/time reasoning
Task prioritization
Calendar reasoning
Habit reasoning
Conversation
Safety
Latency
Memory usage
```

Every new model must pass the same benchmark.

---

# 54. Example benchmark

Input:

> "Did you set my reminder to call Mom?"

Database:

```text
No matching reminder
```

Expected:

```text
No, I don't have that reminder saved.
```

Bad:

```text
Yep, I set it!
```

---

# 55. Model metrics

Track:

### Correctness

- Intent accuracy
- Tool execution accuracy
- Entity extraction accuracy

### Reasoning

- Task prioritization accuracy
- Calendar reasoning accuracy
- Habit interpretation accuracy

### Safety/reliability

- Hallucination rate
- False-action rate
- Clarification rate

### Performance

- Time to first token
- Total response time
- Peak RAM
- CPU utilization
- Model load time

---

# 56. Release gates

## V1.0

Do not move to V1.2 until:

```text
Tool correctness is high
Hallucination rate is acceptably low
Ambiguous requests are handled
UI remains responsive
```

## V1.2

Do not move to V2 until:

```text
Voice is reliable
Startup/shutdown is stable
Resource usage is acceptable
```

## V2

Do not activate ML until:

```text
Data collection is reliable
Privacy controls work
Data schema is stable
```

## V2.1

Do not automate predictions until:

```text
Offline evaluation is better than baseline
Confidence is calibrated
User feedback is stored
```

## V3

Do not introduce deep learning until:

```text
Enough longitudinal data exists
Classical ML has measurable limitations
Deep model beats baseline
```

---

# 57. What NOT to build early

Avoid these until they are justified:

- Cloud backend
- User accounts
- Remote database
- Mandatory internet
- Large always-loaded LLM
- Always-on microphone
- Keylogging
- Screenshot recording
- Full browser history collection
- Automatic calendar writes without confirmation
- Automatic model retraining after every event
- Large neural networks without enough data
- RL before collecting meaningful feedback/reward signals

---

# 58. Recommended project evolution

### V1.0

```text
PySide6
Python
SQLite
Deterministic tools
Small local LLM
Ollama
```

### V1.2

Add:

```text
whisper.cpp / faster-whisper
Piper / Kokoro
ModelManager
Startup/background service
ResourceManager
```

### V2.0

Add:

```text
ActivityWatch
OS event collectors
Normalized event schema
Activity database
Privacy controls
Timeline
```

### V2.1

Add:

```text
scikit-learn
Feature pipeline
Random Forest / Gradient Boosting
Isolation Forest
Confidence calibration
Feedback dataset
Model registry
```

### V3.0

Add:

```text
Pattern discovery
Personal behavior model
Weekly learning worker
Semantic memory
Preference memory
Behavioral memory
```

### V3.1

Add:

```text
PyTorch
Sequence models
Time-series models
Deep personalization
Optional GPU training
```

---

# 59. Final Architecture

```text
                              MOCHI
                                │
                    ┌───────────▼───────────┐
                    │       Mochi Core      │
                    │ UI / Events / Config  │
                    └───────────┬───────────┘
                                │
                 ┌──────────────┼──────────────┐
                 │              │              │
                 ▼              ▼              ▼
              TOOLS          CONTEXT          AI
                 │              │              │
        ┌────────┼──────┐       │       ┌──────┼────────┐
        │        │      │       │       │      │        │
    Reminder  Task  Calendar  Activity  Router  Reasoner
    Timer     Notes System    Power     Small   Qwen
        │        │      │       │       │       Thinking
        └────────┴──────┴───────┴───────┴────────┘
                                │
                                ▼
                         SQLite / Local Data
                                │
                     ┌──────────┴──────────┐
                     │                     │
                Raw Events             Summaries
                     │                     │
                     └──────────┬──────────┘
                                ▼
                           ML Pipeline
                                │
                         ┌──────┴──────┐
                         │             │
                     Classical ML   Deep ML
                         │             │
                         └──────┬──────┘
                                ▼
                         Personal Model
                                │
                                ▼
                         Mochi Decision
                                │
                                ▼
                      Expression / Voice /
                         Notification
```

---

# 60. The central Mochi rule

Everything should ultimately follow:

```text
                CAN A NORMAL FUNCTION DO IT?
                       │
                 ┌─────┴─────┐
                YES          NO
                 │            │
                 ▼            ▼
              TOOL       SMALL MODEL
                              │
                       Is reasoning needed?
                              │
                       ┌──────┴──────┐
                      NO             YES
                       │              │
                       ▼              ▼
                   Respond       REASONING MODEL
                                      │
                              Need historical data?
                                      │
                                ┌─────┴─────┐
                               NO          YES
                                │            │
                                ▼            ▼
                            Answer      Context/ML
                                             │
                                             ▼
                                         Answer
```

This prevents Mochi from becoming a resource-hungry "LLM wrapper."

---

# 61. Success definition

Mochi is successful when:

> **It is useful without being intrusive, intelligent without pretending certainty, local without sacrificing capability, and increasingly personalized without requiring the user to constantly configure it.**

The long-term goal is not:

> "Build a huge AI model."

It is:

> **Build a small local system that becomes increasingly good at understanding one person.**

---

## Primary sources

- Python — https://www.python.org/
- Qt for Python / PySide6 — https://doc.qt.io/qtforpython/
- SQLite — https://sqlite.org/
- Ollama — https://ollama.com/
- llama.cpp — https://github.com/ggml-org/llama.cpp
- Qwen3 — https://github.com/QwenLM/Qwen3
- Qwen3-4B-Thinking-2507 — https://huggingface.co/Qwen/Qwen3-4B-Thinking-2507
- ActivityWatch — https://activitywatch.net/
- ActivityWatch documentation — https://docs.activitywatch.net/
- scikit-learn — https://scikit-learn.org/
- XGBoost — https://xgboost.readthedocs.io/
- LightGBM — https://lightgbm.readthedocs.io/
- PyTorch — https://pytorch.org/
- TensorFlow — https://www.tensorflow.org/
- whisper.cpp — https://github.com/ggml-org/whisper.cpp
- faster-whisper — https://github.com/SYSTRAN/faster-whisper
- Piper — https://github.com/rhasspy/piper
- Kokoro — https://github.com/hexgrad/kokoro
- Google Calendar API — https://developers.google.com/calendar/api
- Google OAuth — https://developers.google.com/identity/protocols/oauth2
- Apple NSWorkspace — https://developer.apple.com/documentation/appkit/nsworkspace
- Windows Win32 documentation — https://learn.microsoft.com/windows/win32/
- SQLCipher — https://www.zetetic.net/sqlcipher/
