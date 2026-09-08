"""
CLI: manually run one ingestion cycle of the Web Knowledge Engine
(V1.1, see app/knowledge/scheduler.py) - fetches every due, enabled
registered source (app/knowledge/source_manager.py), stores new evidence,
and purges anything expired.

Usage:
    python scripts/run_knowledge_ingestion.py

Requires MOCHI_WEB_KNOWLEDGE_ENABLED=true (see .env.example) - the engine
is off by default, so running this with the feature disabled is a no-op
by design, same as the app's own background job would be.

Safe to run repeatedly - each source respects its own frequency_hours and
incremental-fetch state (app/knowledge/knowledge_store.get_fetch_state),
so re-running immediately after a successful run does nothing new.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402
from app.knowledge.scheduler import run_ingestion_cycle  # noqa: E402


def main() -> int:
    if not settings.web_knowledge_enabled:
        print(
            "MOCHI_WEB_KNOWLEDGE_ENABLED is false - nothing to do. "
            "Set it to true in your .env to enable the Web Knowledge Engine."
        )
        return 0
    stored = run_ingestion_cycle()
    print(f"Done: {stored} new document(s) stored.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
