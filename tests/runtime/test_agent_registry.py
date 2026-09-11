"""Tests for miqi.runtime.agent_registry."""

import pytest

from miqi.runtime.agent_registry import AgentMetadata, AgentRegistry


def test_agent_metadata_creation():
    meta = AgentMetadata(
        name="test-agent",
        display_name="Test Agent",
        description="A test agent",
        system_prompt="You are a test agent.",
        available_tools=["read_file", "exec"],
    )
    assert meta.name == "test-agent"
    assert meta.is_builtin is True
    assert meta.max_iterations == 40


def test_registry_register_and_resolve():
    registry = AgentRegistry()
    meta = registry.resolve("main")
    assert meta.name == "main"
    assert meta.display_name == "MiQroForge"


def test_discover_plugin_agents_parses_frontmatter(tmp_path):
    """Regression: discover_plugin_agents used `re.DOTALL` while only
    `_agent_re` was imported — calling it would raise NameError. After
    the fix it must parse real agents/*.md files without raising.
    """
    # Build a fake plugin with an agents/ directory
    plugin_dir = tmp_path / "demo-plugin"
    agents_dir = plugin_dir / "agents"
    agents_dir.mkdir(parents=True)

    (agents_dir / "test-agent.md").write_text(
        "---\n"
        "name: test-agent\n"
        "description: A discoverable agent.\n"
        "model: sonnet\n"
        "maxTurns: 12\n"
        "color: cyan\n"
        "---\n\n"
        "# /test-agent\n\nYou are a test agent.\n",
        encoding="utf-8",
    )

    registry = AgentRegistry()
    discovered = registry.discover_plugin_agents(
        plugin_dir, plugin_name="demo-plugin"
    )

    assert len(discovered) == 1
    agent = discovered[0]
    assert agent.name == "kwp-demo-plugin-test-agent"
    assert agent.model_override == "sonnet"
    assert agent.max_iterations == 12
    assert "test agent" in agent.system_prompt.lower()


def test_discover_plugin_agents_skips_no_frontmatter(tmp_path):
    plugin_dir = tmp_path / "demo-plugin"
    agents_dir = plugin_dir / "agents"
    agents_dir.mkdir(parents=True)

    (agents_dir / "plain.md").write_text("# plain markdown, no frontmatter\n", encoding="utf-8")

    registry = AgentRegistry()
    assert registry.discover_plugin_agents(plugin_dir, plugin_name="demo") == []


def test_discover_plugin_agents_missing_dir(tmp_path):
    plugin_dir = tmp_path / "demo-plugin"
    plugin_dir.mkdir()
    registry = AgentRegistry()
    # No agents/ directory should be a no-op, not an error.
    assert registry.discover_plugin_agents(plugin_dir, plugin_name="demo") == []


def test_registry_has_builtins():
    registry = AgentRegistry()
    agents = registry.list_agents()
    names = {a.name for a in agents}
    assert "main" in names
    assert "code-agent" in names
    assert "doc-agent" in names
    assert "research-agent" in names


def test_main_agent_prompt_guides_skill_discovery():
    """The main agent's system prompt must instruct the model to check the
    Local Skills list and load matching SKILL.md before denying a capability
    (#613 follow-up)."""
    registry = AgentRegistry()
    main_agent = registry.resolve("main")
    assert "Local Skills" in main_agent.system_prompt
    assert "skill_manage" in main_agent.system_prompt
    assert "Never claim a skill does not exist" in main_agent.system_prompt


def test_main_agent_prompt_guides_structured_comparison_output():
    """#878: the main agent must be instructed to emit ```compare JSON for
    multi-scheme parameter comparisons, so the desktop renders a comparison table."""
    registry = AgentRegistry()
    main_agent = registry.resolve("main")
    assert "Structured Comparison Output" in main_agent.system_prompt
    assert "```compare" in main_agent.system_prompt
    assert '"schemes"' in main_agent.system_prompt


def test_registry_register_duplicate_raises():
    registry = AgentRegistry()
    meta = AgentMetadata(
        name="main",
        display_name="Duplicate",
        description="...",
        system_prompt="...",
        available_tools=[],
    )
    with pytest.raises(ValueError, match="already registered"):
        registry.register(meta)


def test_registry_resolve_unknown_raises():
    registry = AgentRegistry()
    with pytest.raises(KeyError, match="Unknown agent type"):
        registry.resolve("nonexistent")


def test_main_agent_has_office_tools():
    registry = AgentRegistry()
    main = registry.resolve("main")
    assert "docx_read" in main.available_tools
    assert "pptx_write" in main.available_tools


def test_code_agent_has_no_office_tools():
    registry = AgentRegistry()
    code = registry.resolve("code-agent")
    assert "docx_write" not in code.available_tools


def test_code_agent_has_fewer_iterations():
    registry = AgentRegistry()
    code = registry.resolve("code-agent")
    assert code.max_iterations == 25


def test_doc_agent_has_fewer_iterations():
    registry = AgentRegistry()
    doc = registry.resolve("doc-agent")
    assert doc.max_iterations == 20


def test_research_agent_has_fewer_iterations():
    registry = AgentRegistry()
    research = registry.resolve("research-agent")
    assert research.max_iterations == 15


def test_all_agents_have_system_prompts():
    registry = AgentRegistry()
    for agent in registry.list_agents():
        assert len(agent.system_prompt) > 50
        assert len(agent.available_tools) > 0


def test_custom_agent_registration():
    registry = AgentRegistry()
    custom = AgentMetadata(
        name="custom-agent",
        display_name="Custom",
        description="A custom agent",
        system_prompt="You are custom.",
        available_tools=["read_file"],
        max_iterations=10,
        model_override="gpt-4",
        is_builtin=False,
    )
    registry.register(custom)
    resolved = registry.resolve("custom-agent")
    assert resolved.model_override == "gpt-4"
    assert resolved.is_builtin is False
