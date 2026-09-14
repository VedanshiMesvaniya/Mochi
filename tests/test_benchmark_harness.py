"""
Tests for benchmarks/harness.py's own scoring and aggregation logic -
NOT a re-test of Mochi's behavior (that's the main suite's job; see
benchmarks/dataset.py's cases for the actual behavioral assertions).
These make sure the harness itself correctly runs a case, skips a
needs_llm case, catches a case that raises instead of crashing the
whole run, and aggregates per-category accuracy correctly.
"""

from benchmarks.dataset import ALL_CATEGORIES, Case
from benchmarks.harness import run_all, run_case


def test_run_case_passes_when_check_returns_true(temp_db):
    case = Case(
        id="t1", category="intent_accuracy", description="d",
        message="hi mochi", check=lambda reactions: True,
    )
    result = run_case(case)
    assert result.passed is True
    assert result.error is None
    assert result.elapsed_seconds >= 0


def test_run_case_fails_when_check_returns_false(temp_db):
    case = Case(
        id="t2", category="intent_accuracy", description="d",
        message="hi mochi", check=lambda reactions: False,
    )
    result = run_case(case)
    assert result.passed is False


def test_run_case_fails_gracefully_when_check_raises(temp_db):
    """A broken case must be recorded as a failure with an error
    message, never crash the whole benchmark run."""

    def _boom(_reactions):
        raise ValueError("simulated bug in a case's check function")

    case = Case(
        id="t3", category="intent_accuracy", description="d",
        message="hi mochi", check=_boom,
    )
    result = run_case(case)
    assert result.passed is False
    assert "ValueError" in result.error


def test_run_case_skips_needs_llm_cases(temp_db):
    case = Case(
        id="t4", category="conversation", description="d",
        message="anything", check=None, needs_llm=True,
    )
    result = run_case(case)
    assert result.passed is None
    assert result.skipped_reason is not None


def test_run_case_threads_setup_messages_and_state(temp_db):
    """A reference-resolution style case (create, then correct) must
    see the setup's effect in the final reaction."""
    case = Case(
        id="t5", category="reference_resolution_and_corrections", description="d",
        setup_messages=("remind me to call mom at 7pm",),
        message="make it 8pm",
        check=lambda reactions: "8:00 pm" in reactions[-1].text.lower(),
    )
    result = run_case(case)
    assert result.passed is True


def test_run_case_can_opt_out_of_carrying_state(temp_db):
    """carry_state=False must mean each message starts fresh - no
    pending_action/conversation_state from the previous message."""
    case = Case(
        id="t6", category="ambiguity_handling", description="d",
        setup_messages=("remind me to call mom at 7pm", "remind me to call dad at 8pm"),
        message="cancel my reminder",
        check=lambda reactions: "which one" in reactions[-1].text.lower(),
        carry_state=False,
    )
    result = run_case(case)
    assert result.passed is True


def test_run_all_aggregates_by_category(temp_db, monkeypatch):
    fake_cases = [
        Case(id="a1", category="intent_accuracy", description="d", message="hi mochi",
             check=lambda r: True),
        Case(id="a2", category="intent_accuracy", description="d", message="hi mochi",
             check=lambda r: False),
        Case(id="a3", category="hallucination_resistance", description="d",
             message="do i have any reminders", check=lambda r: True),
        Case(id="a4", category="conversation", description="d", message="x",
             check=None, needs_llm=True),
    ]
    monkeypatch.setattr("benchmarks.harness.CASES", fake_cases)

    report = run_all()

    assert report["correctness"]["cases_run"] == 3
    assert report["correctness"]["cases_skipped"] == 1
    assert report["correctness"]["overall_accuracy"] == 2 / 3
    by_cat = report["correctness"]["by_category"]
    assert by_cat["intent_accuracy"]["passed"] == 1
    assert by_cat["intent_accuracy"]["run"] == 2
    assert by_cat["hallucination_resistance"]["accuracy"] == 1.0
    assert by_cat["conversation"]["run"] == 0
    assert by_cat["conversation"]["skipped"] == 1
    # Every category from the dataset's own list appears, even ones with
    # zero cases in this fake set - a report should never silently omit
    # a whole category just because nothing happened to exercise it.
    assert set(by_cat.keys()) == set(ALL_CATEGORIES)


def test_run_all_with_no_model_reports_current_router(temp_db, monkeypatch):
    monkeypatch.setattr("benchmarks.harness.CASES", [
        Case(id="b1", category="intent_accuracy", description="d", message="hi mochi",
             check=lambda r: True),
    ])
    report = run_all(model=None)
    assert report["model_under_test"] == "current (deterministic router only, no model needed)"


def test_run_all_records_when_model_requested_but_unreachable(temp_db, monkeypatch):
    monkeypatch.setattr("benchmarks.harness.CASES", [
        Case(id="c1", category="conversation", description="d", message="x",
             check=None, needs_llm=True),
    ])
    monkeypatch.setattr("benchmarks.harness._check_ollama_reachable", lambda host: False)

    report = run_all(model="qwen3:4b", host="http://localhost:11434")

    skipped_reason = report["cases"][0]["skipped_reason"]
    assert "not reachable" in skipped_reason
