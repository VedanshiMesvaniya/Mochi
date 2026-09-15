"""
Runner for benchmarks/dataset.py (Cognitive Upgrade spec section 19
"Model Strategy" / phase 4).

What this can and can't measure, and why - read this before trusting
any report it produces:

REAL, measured right now, no model needed: everything in dataset.py
except the `needs_llm=True` cases exercises Mochi's DETERMINISTIC
router (app/ai/intent.py, app/ai/chat_engine.py) directly - the exact
same code path regardless of which LLM is configured, since spec
section 60's own rule ("can a normal function do it? -> tool, never a
model guess") means none of intent detection, tool selection,
hallucination resistance, ambiguity handling, date/time parsing,
corrections, or the calendar confirmation gate ever asks the model
anything. So these results are genuinely model-INDEPENDENT baselines,
not a stand-in for the model comparison the spec actually wants.

NOT measurable here: the actual point of spec section 19 - comparing
Qwen3-4B, Qwen3-8B, Phi-4-mini, and the current model (qwen2.5:1.5b)
against each other on open-ended conversation quality, and each one's
real latency/RAM/VRAM. That needs an actual running Ollama instance
serving each model, entirely locally - this project is local-first by
design (MOCHI_VERSIONED_ROADMAP.md section 4: "Never require a cloud
API"), so Ollama's own registry is the only model source in scope at
all. This project's current sandboxed development environment cannot
reach it: `ollama pull` needs `registry.ollama.ai`, which returns
`403: Host not in allowlist` here - checked directly by actually
running a real Ollama server (the binary itself is reachable via a
different, already-allowed path, `github.com`'s release-asset
redirect, and confirmed working) and attempting a real pull, not
assumed. There is also no GPU, and only 3.9 GB total RAM - even with
network access, an 8B model likely wouldn't fit here at all. This
harness is written to make the real comparison a single command away
the moment it's run somewhere Ollama access exists - see `--model` and
`--host` below and benchmarks/README.md for exact `ollama pull`
commands - rather than something that needs to be built from scratch
later.

Usage:
    python -m benchmarks.harness                      # deterministic cases only
    python -m benchmarks.harness --model qwen3:4b      # also attempt needs_llm cases
    python -m benchmarks.harness --model qwen3:4b --host http://192.168.1.5:11434
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import platform
import subprocess
import sys
import tempfile
import time
import tracemalloc
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Optional
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from benchmarks.dataset import ALL_CATEGORIES, CASES, Case  # noqa: E402


@dataclass
class CaseResult:
    id: str
    category: str
    description: str
    passed: Optional[bool]  # None when skipped (needs_llm and no model reachable)
    skipped_reason: Optional[str]
    elapsed_seconds: float
    error: Optional[str]


# Named failure-injection setups a Case can reference by string (kept
# here rather than importing unittest.mock into dataset.py, so the
# dataset file itself stays a plain, dependency-light data description).
def _fail_if_calendar_write_called():
    def _raise(*_a, **_kw):
        raise AssertionError("calendar write called before confirmation")

    return patch("app.ai.chat_engine.calendar_tools.create_event", _raise)


def _calendar_write_raises():
    from app.core.exceptions import MochiError

    def _raise(*_a, **_kw):
        raise MochiError("simulated failure for benchmark")

    return patch("app.ai.chat_engine.calendar_tools.create_event", _raise)


_PATCH_FACTORIES = {
    "fail_if_calendar_write_called": _fail_if_calendar_write_called,
    "calendar_write_raises": _calendar_write_raises,
}


@contextmanager
def _isolated_db():
    """Fresh, isolated SQLite DB for one case - same mechanism as
    tests/conftest.py's temp_db fixture, reimplemented here rather than
    imported so this harness can run standalone, outside pytest."""
    from app.core.config import settings

    original = settings.data_dir
    with tempfile.TemporaryDirectory() as tmp:
        settings.data_dir = pathlib.Path(tmp)
        try:
            yield
        finally:
            settings.data_dir = original


def run_case(case: Case) -> CaseResult:
    from app.ai import chat_engine

    if case.needs_llm:
        return CaseResult(
            id=case.id, category=case.category, description=case.description,
            passed=None, skipped_reason="needs a live model (see harness.py docstring)",
            elapsed_seconds=0.0, error=None,
        )

    patch_ctx = _PATCH_FACTORIES[case.patches]() if case.patches else None
    start = time.perf_counter()
    try:
        with _isolated_db():
            if patch_ctx is not None:
                patch_ctx.start()
            try:
                reactions = []
                pending_action = None
                conversation_state = None
                for message in (*case.setup_messages, case.message):
                    reaction = chat_engine.handle_message(
                        message, pending_action=pending_action,
                        conversation_state=conversation_state,
                    )
                    reactions.append(reaction)
                    if case.carry_state:
                        pending_action = reaction.pending_action
                        conversation_state = reaction.conversation_state
                passed = bool(case.check(reactions))
                error = None
            finally:
                if patch_ctx is not None:
                    patch_ctx.stop()
    except Exception as exc:  # noqa: BLE001 - a case blowing up is a FAIL, not a crash
        passed = False
        error = f"{type(exc).__name__}: {exc}"
    elapsed = time.perf_counter() - start
    return CaseResult(
        id=case.id, category=case.category, description=case.description,
        passed=passed, skipped_reason=None, elapsed_seconds=elapsed, error=error,
    )


def _check_ollama_reachable(host: str) -> bool:
    try:
        import urllib.request

        with urllib.request.urlopen(f"{host}/api/tags", timeout=2):
            return True
    except Exception:
        return False


def run_all(model: Optional[str] = None, host: str = "http://localhost:11434") -> dict:
    """Runs every case, returns a report shaped around spec section 55's
    metric groups (correctness, safety/reliability, performance)."""
    tracemalloc.start()
    wall_start = time.perf_counter()

    results = [run_case(case) for case in CASES]

    ollama_reachable = bool(model) and _check_ollama_reachable(host)
    if model and not ollama_reachable:
        # Recorded, not silently dropped - a report that ran with
        # --model but couldn't reach Ollama should say so loudly, not
        # look identical to a report that never tried.
        for r in results:
            if r.skipped_reason is not None:
                r.skipped_reason = f"needs a live model - {host} was not reachable"

    wall_elapsed = time.perf_counter() - wall_start
    _current_bytes, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    by_category = {}
    for cat in ALL_CATEGORIES:
        cat_results = [r for r in results if r.category == cat]
        run_results = [r for r in cat_results if r.passed is not None]
        by_category[cat] = {
            "total": len(cat_results),
            "run": len(run_results),
            "passed": sum(1 for r in run_results if r.passed),
            "skipped": len(cat_results) - len(run_results),
            "accuracy": (
                sum(1 for r in run_results if r.passed) / len(run_results)
                if run_results else None
            ),
        }

    run_results = [r for r in results if r.passed is not None]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "model_under_test": model or "current (deterministic router only, no model needed)",
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "correctness": {
            "cases_run": len(run_results),
            "cases_skipped": len(results) - len(run_results),
            "overall_accuracy": (
                sum(1 for r in run_results if r.passed) / len(run_results)
                if run_results else None
            ),
            "by_category": by_category,
        },
        "safety_reliability": {
            "hallucination_category_accuracy": by_category[
                "hallucination_resistance"
            ]["accuracy"],
            "ambiguity_category_accuracy": by_category["ambiguity_handling"]["accuracy"],
            "failure_handling_category_accuracy": by_category["failure_handling"]["accuracy"],
            "calendar_safety_category_accuracy": by_category["calendar_safety"]["accuracy"],
        },
        "performance": {
            "total_wall_seconds": wall_elapsed,
            "mean_case_seconds": (
                sum(r.elapsed_seconds for r in run_results) / len(run_results)
                if run_results else None
            ),
            "peak_traced_memory_bytes": peak_bytes,
            "note": (
                "This is Mochi's own process memory/latency for the "
                "deterministic router, not a per-model comparison - see "
                "harness.py's docstring."
            ),
        },
        "cases": [asdict(r) for r in results],
    }
    return report


def _git_commit() -> Optional[str]:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=pathlib.Path(__file__).resolve().parent.parent,
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model", default=None,
        help="Ollama model tag to also attempt needs_llm cases against (e.g. qwen3:4b). "
             "Omit to run the deterministic cases only.",
    )
    parser.add_argument("--host", default="http://localhost:11434")
    parser.add_argument("--output", default=None, help="Path to write the JSON report to.")
    args = parser.parse_args()

    report = run_all(model=args.model, host=args.host)

    output_path = args.output or (
        pathlib.Path(__file__).resolve().parent
        / "results"
        / f"report_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    )
    output_path = pathlib.Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2))

    correctness = report["correctness"]
    print(f"Ran {correctness['cases_run']} cases, skipped {correctness['cases_skipped']}.")
    if correctness["overall_accuracy"] is not None:
        print(f"Overall accuracy: {correctness['overall_accuracy']:.0%}")
    for cat, stats in correctness["by_category"].items():
        if stats["run"] == 0:
            print(f"  {cat}: skipped ({stats['skipped']} needs a live model)")
        else:
            print(f"  {cat}: {stats['passed']}/{stats['run']}")
    print(f"Report written to {output_path}")
    return 0 if correctness["overall_accuracy"] in (1.0, None) else 1


if __name__ == "__main__":
    sys.exit(main())
