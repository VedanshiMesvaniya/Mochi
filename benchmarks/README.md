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
a real, running Ollama instance serving each model in turn, entirely
locally (see "Local models: Ollama only" below - no cloud fallback is
in scope). **This project's current sandboxed development environment
cannot do that**, checked one step at a time, not assumed:

- The Ollama *binary* itself is actually reachable, through a path
  already allowed for other reasons (`github.com`'s release-download
  redirect lands on `release-assets.githubusercontent.com`, already
  permitted). Downloaded and ran it for real to confirm this - the
  server starts and correctly detects CPU-only inference.
- But `ollama pull <model>` against that real, running server fails:
  ```
  Error: pull model manifest: 403: Host not in allowlist:
  registry.ollama.ai. Add this host to your network egress settings
  to allow access.
  ```
  `registry.ollama.ai` is what every model pull actually needs, and
  it's not reachable here - confirmed by trying it for real, not
  assumed from the earlier `ollama.com` block alone.
- No GPU, and only 3.9 GB total RAM (3.5 GB available, no swap) - even
  with weights available, the 8B candidate likely wouldn't fit here at
  all.

This is an infrastructure limitation of *this development
environment*, not a property of Mochi's code or of the benchmark
design - the harness is written so the real comparison is a single
command away the moment it's run somewhere Ollama access exists.

## Local models: Ollama only

This project is local-first by design (see `MOCHI_VERSIONED_ROADMAP.md`
section 4: "Never require a cloud API"). Every model this benchmark
suite targets is pulled through Ollama's own registry
(`registry.ollama.ai`) and run entirely on-device - no Hugging Face
account, no API key, no cloud inference endpoint, ever. If a model
isn't available as a plain `ollama pull <tag>`, it's not in scope for
Mochi.

## Recommended local models (all via `ollama pull`)

| Tag | Size | Params | Role |
|---|---|---|---|
| `qwen2.5:1.5b` | ~1 GB | 1.5B | Current Mochi default - the always-on baseline every comparison runs against |
| `qwen3:4b` | 2.5 GB | 4B | Phase 4 candidate - supports a `think` on/off toggle (spec section 18's reasoning-budget idea, natively) |
| `qwen3:8b` | 5.2 GB | 8B | Phase 4 candidate - higher-quality reasoning tier, same `think` toggle |
| `phi4-mini` | 2.5 GB | 3.8B | Phase 4 candidate - Microsoft's small reasoning model, alternative lineage to Qwen |

Pull whichever you want to test:

```bash
ollama pull qwen2.5:1.5b
ollama pull qwen3:4b
ollama pull qwen3:8b
ollama pull phi4-mini

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
