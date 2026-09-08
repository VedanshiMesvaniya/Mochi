from app.knowledge import source_manager
from app.knowledge.models import Source


def test_get_enabled_sources_returns_only_enabled():
    enabled = Source(
        key="a", kind="rss", target="https://example.com", category_hint="knowledge",
        authority="high", frequency_hours=1, enabled=True,
    )
    disabled = Source(
        key="b", kind="rss", target="https://example.com", category_hint="knowledge",
        authority="high", frequency_hours=1, enabled=False,
    )
    original = source_manager.DEFAULT_SOURCES
    try:
        source_manager.DEFAULT_SOURCES = (enabled, disabled)
        result = source_manager.get_enabled_sources()
        assert result == [enabled]
    finally:
        source_manager.DEFAULT_SOURCES = original


def test_default_sources_are_all_enabled_out_of_the_box():
    assert all(source.enabled for source in source_manager.DEFAULT_SOURCES)
    keys = [source.key for source in source_manager.DEFAULT_SOURCES]
    assert len(keys) == len(set(keys)), "source keys must be unique"
