# Mochi benchmark suite (Cognitive Upgrade phase 4)

Spec: `docs/ROADMAP_COGNITIVE_UPGRADE.md` sections 19 ("Model Strategy")
and 53 ("Model evaluation"). See that file's "Phase 4" section for the
honest, up-to-date status - this README covers only how to use what's
here.

## What this is, in one paragraph

`dataset.py` is a small, permanent, versioned set of test cases across
the categories spec section 53 asks for (intent accuracy, tool
correctness, hallucination resistance, ambiguity handling, date/time
reasoning, reference resolution and corrections, calendar safety,
failure handling, conversation). `harness.py` runs them against real
Mochi code and produces a report shaped around spec section 55's metric
groups (correctness, safety/reliability, performance).

## What it can measure right now, with no setup

Every case except the `conversation` ones exercises Mochi's
**deterministic router** (`app/ai/intent.py`, `app/ai/chat_engine.py`)
directly - no model involved, by design (spec section 60: "can a normal
function do it? -> tool, never a model guess"). These results are real
today, and they're the same regardless of which LLM Mochi is configured
to use, since the deterministic router never asks the model anything.

Run it:

```bash
python -m benchmarks.harness
```

A committed baseline from this project's own development environment
is at `benchmarks/results/baseline_deterministic_2026-09-14.json` -
100% on every category that could run (16/16; the one `conversation`
case is skipped, see below), captured against git commit `894901b`.
Re-running should reproduce it exactly - every case uses a fresh,
isolated temp database, and none of it depends on wall-clock time in a
way that would change the pass/fail outcome.

## What it can't measure here, and why - read this before trusting a
## report that mentions Qwen3-4B, Qwen3-8B, or Phi-4-mini

The actual point of spec section 19 is comparing those three models
plus the current one (`qwen2.5:1.5b`) against each other - conversation
quality, tool selection under ambiguity, latency, RAM, VRAM. That needs
a real, running Ollama instance serving each model in turn. **This
project's current sandboxed development environment cannot do that**:

- No route to `ollama.com` or `huggingface.co` to download model
  weights. Verified directly, not assumed:
  ```
  $ curl -sS -D - -o /dev/null https://ollama.com
  HTTP/2 403
  x-deny-reason: host_not_allowed
  ```
  (same result for `huggingface.co`)
- No Ollama binary installed, no Ollama server reachable at
  `localhost:11434` either.
- No GPU.

This is an infrastructure limitation of *this development
environment*, not a property of Mochi's code or of the benchmark
design - the harness is written so the real comparison is a single
command away the moment it's run somewhere Ollama access exists.

## Running the real comparison, once Ollama is available

```bash
# Current model (already Mochi's default)
ollama pull qwen2.5:1.5b

# The three phase-4 candidates (official Ollama library tags,
# confirmed current as of this writing)
ollama pull qwen3:4b       # 2.5 GB - closest available tag to the
                            # roadmap's named "Qwen3-4B-Thinking-2507";
                            # the base qwen3:4b supports a `think`
                            # parameter, so pass think=True for the
                            # closest match to that variant. For the
                            # exact 2507 checkpoint instead of the
                            # base model, pull a GGUF build of it from
                            # Hugging Face directly, e.g.:
                            # ollama run hf.co/unsloth/Qwen3-4B-Thinking-2507-GGUF
ollama pull qwen3:8b       # 5.2 GB
ollama pull phi4-mini      # 2.5 GB, 3.8B params

ollama serve   # if not already running

python -m benchmarks.harness --model qwen3:4b
python -m benchmarks.harness --model qwen3:8b
python -m benchmarks.harness --model phi4-mini
python -m benchmarks.harness --model qwen2.5:1.5b   # current model, for a same-conditions baseline
```

Each run writes a timestamped JSON report to `benchmarks/results/`.
`--model` only affects the `needs_llm=True` cases (currently just
`conversation`) - the deterministic categories run and score identically
regardless, which is expected and correct, not a bug: they're testing
Mochi's own router, not the model.

If `--model` is given but Ollama isn't reachable at `--host` (default
`http://localhost:11434`), the report records that plainly in each
skipped case's `skipped_reason` rather than silently looking identical
to a report that never tried.

## Extending the dataset

Add a `Case` to `dataset.py`. Every case must be verified against real
Mochi behavior before being written down - run it by hand first (see
any existing case's category for the pattern), don't guess at what the
expected response text will be. If a case needs a tool to fail on
purpose (testing failure handling), add a named patch factory to
`harness.py`'s `_PATCH_FACTORIES` rather than importing `unittest.mock`
into `dataset.py` - keeps the dataset file a plain data description.

## Tests

`tests/test_benchmark_harness.py` tests the harness's own scoring and
aggregation logic in isolation (a case that raises is caught as a
failure, not a crash; per-category accuracy aggregates correctly;
`needs_llm` cases are skipped and recorded, not silently dropped). It
does not re-test Mochi's behavior - that's what running the dataset
itself does, and why it's checked into git as `baseline_deterministic_*`
rather than being pytest-only: it's meant to be run standalone too.
