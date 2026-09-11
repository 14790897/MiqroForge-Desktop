"""Phase 3: lessons inject enabled by default with confidence threshold = 3."""
from miqi.agent.memory.store import MemoryStore
from miqi.config.schema import AgentSelfImprovementConfig


def test_default_inject_enabled():
    cfg = AgentSelfImprovementConfig()
    assert cfg.lessons_legacy_inject_enabled is True


def test_default_min_confidence_is_3():
    cfg = AgentSelfImprovementConfig()
    assert cfg.min_lesson_confidence == 3


def test_low_confidence_lesson_not_injected(tmp_path):
    """A confidence=1 lesson must not appear in memory context (below threshold)."""
    store = MemoryStore(
        workspace=tmp_path,
        lessons_legacy_inject_enabled=True,
        min_lesson_confidence=3,
    )
    store._lesson_store.learn(
        trigger="shell:error",
        bad_action="rm -rf /",
        better_action="use trash",
        scope="global",
        session_key="s1",
    )
    # learn() sets confidence = max(min_lesson_confidence, delta) = 3.
    # Manually lower it to 1 so it falls below the threshold.
    store._lesson_store._lessons[0]["confidence"] = 1
    store._lesson_store.flush()
    ctx = store.get_memory_context("s1", "run some commands")
    assert "use trash" not in ctx


def test_high_confidence_lesson_injected(tmp_path):
    """A confidence=5 lesson must appear in memory context."""
    store = MemoryStore(
        workspace=tmp_path,
        lessons_legacy_inject_enabled=True,
        min_lesson_confidence=3,
    )
    store._lesson_store.learn(
        trigger="shell:error",
        bad_action="rm -rf /",
        better_action="use trash instead",
        scope="global",
        session_key="s1",
    )
    # Manually bump confidence to 5, above threshold
    store._lesson_store._lessons[0]["confidence"] = 5
    store._lesson_store.flush()
    ctx = store.get_memory_context("s1", "run some commands")
    assert "use trash instead" in ctx
