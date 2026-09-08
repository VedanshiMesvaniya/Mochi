"""
Web Knowledge & Context Engine (V1.1, opt-in).

Off by default (settings.web_knowledge_enabled / MOCHI_WEB_KNOWLEDGE_ENABLED).
Everything in this package degrades to "no fresh context" rather than an
error on any failure, and nothing here is ever called synchronously inside
a chat reply - only cheap local SQLite reads happen at chat time (see
context_engine.get_web_context), matching the same design constraint
app/humor/trend_fetcher.py already follows.

Pipeline (see docs/ROADMAP_V1_1_WEB_KNOWLEDGE.md for the full spec):

    source_manager -> fetcher -> parser -> dedup -> classifier
        -> knowledge_store (persist)
        -> freshness (score at query time)
        -> context_engine (compact evidence package for the LLM)

scheduler.run_ingestion_cycle() ties the pipeline stages together and is
the only entry point meant to be wired into a periodic/background job or
manual "refresh" action (see app/character/pet.py's _RefreshTrendsWorker).
"""
