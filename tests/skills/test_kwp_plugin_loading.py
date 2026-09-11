"""Tests for KWP plugin loading and context injection."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from miqi.agent.skills import SkillsLoader


class TestNestedSkillDiscovery:
    """Verify SkillsLoader discovers KWP-style nested skills (kwp/<plugin>/<skill>/SKILL.md)."""

    def setup_method(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.workspace = self.tmp / "workspace"
        self.workspace.mkdir()
        self.builtin = self.tmp / "builtin"
        self.builtin.mkdir()

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_flat_skill_discovery(self):
        """Classic skill: builtin/<name>/SKILL.md"""
        (self.builtin / "cron").mkdir(parents=True)
        (self.builtin / "cron" / "SKILL.md").write_text(
            "---\nname: cron\ndescription: Schedule tasks\n---\n\n# Cron\n\nUse cron...",
            encoding="utf-8",
        )
        loader = SkillsLoader(self.workspace, builtin_skills_dir=self.builtin)
        names = {s["name"] for s in loader.list_skills(filter_unavailable=False)}
        assert "cron" in names

    def test_nested_kwp_skill_discovery(self):
        """KWP skill: builtin/kwp/sales/account-research/SKILL.md"""
        (self.builtin / "kwp" / "sales" / "account-research").mkdir(parents=True)
        (self.builtin / "kwp" / "sales" / "account-research" / "SKILL.md").write_text(
            "---\n"
            "name: kwp-sales-account-research\n"
            "description: Research a company\n"
            'metadata: {"miqi": {"requires": {}, "emoji": "📈", "source": "knowledge-work-plugins"}}\n'
            "---\n\n"
            "# Account Research\n\nWorkflow steps...",
            encoding="utf-8",
        )
        loader = SkillsLoader(self.workspace, builtin_skills_dir=self.builtin)
        names = {s["name"] for s in loader.list_skills(filter_unavailable=False)}
        assert "account-research" in names

    def test_nested_skill_content_loading(self):
        """Verify load_skill works for nested skills."""
        (self.builtin / "kwp" / "sales" / "test-skill").mkdir(parents=True)
        (self.builtin / "kwp" / "sales" / "test-skill" / "SKILL.md").write_text(
            "---\n"
            "name: kwp-sales-test-skill\n"
            "description: Test\n"
            'metadata: {"miqi": {"requires": {}}}\n'
            "---\n\n"
            "# Test\n\nWorkflow.",
            encoding="utf-8",
        )
        loader = SkillsLoader(self.workspace, builtin_skills_dir=self.builtin)
        content = loader.load_skill("test-skill")
        assert content is not None
        assert "kwp-sales-test-skill" in content
        assert "Workflow" in content

    def test_nested_skill_metadata_parsing(self):
        """Verify get_skill_metadata works for nested skills."""
        (self.builtin / "kwp" / "finance" / "variance-analysis").mkdir(parents=True)
        (self.builtin / "kwp" / "finance" / "variance-analysis" / "SKILL.md").write_text(
            "---\n"
            "name: kwp-finance-variance-analysis\n"
            "description: Analyze financial variances\n"
            'metadata: {"miqi": {"requires": {}, "emoji": "💰", "source": "knowledge-work-plugins"}}\n'
            "---\n\n"
            "# Variance Analysis\n\nTechniques...",
            encoding="utf-8",
        )
        loader = SkillsLoader(self.workspace, builtin_skills_dir=self.builtin)
        meta = loader.get_skill_metadata("variance-analysis")
        assert meta is not None
        assert meta["name"] == "kwp-finance-variance-analysis"

    def test_no_duplicate_with_flat_and_nested(self):
        """A flat skill should win over a same-named nested skill."""
        (self.builtin / "conflict").mkdir(parents=True)
        (self.builtin / "conflict" / "SKILL.md").write_text(
            "---\nname: conflict\ndescription: Flat skill\n---\n\n# Flat",
            encoding="utf-8",
        )
        (self.builtin / "kwp" / "whatever" / "conflict").mkdir(parents=True)
        (self.builtin / "kwp" / "whatever" / "conflict" / "SKILL.md").write_text(
            "---\nname: conflict\ndescription: Nested skill\n---\n\n# Nested",
            encoding="utf-8",
        )
        loader = SkillsLoader(self.workspace, builtin_skills_dir=self.builtin)
        paths_with_conflict = [
            s for s in loader.list_skills(filter_unavailable=False)
            if s["name"] == "conflict"
        ]
        assert len(paths_with_conflict) == 1

    def test_skill_filtered_by_requirements(self):
        """Skills without requirements in metadata should still be available."""
        (self.builtin / "kwp" / "legal" / "review-contract").mkdir(parents=True)
        (self.builtin / "kwp" / "legal" / "review-contract" / "SKILL.md").write_text(
            "---\n"
            "name: kwp-legal-review-contract\n"
            "description: Review contracts\n"
            'metadata: {"miqi": {"requires": {}, "emoji": "⚖️"}}\n'
            "---\n\n"
            "# Review Contract\n\nWorkflow...",
            encoding="utf-8",
        )
        loader = SkillsLoader(self.workspace, builtin_skills_dir=self.builtin)
        all_skills = loader.list_skills(filter_unavailable=False)
        available = loader.list_skills(filter_unavailable=True)
        assert any(s["name"] == "review-contract" for s in all_skills)
        # Should be available (no bins/env requirements)
        assert any(s["name"] == "review-contract" for s in available)


class TestKWPPlaceholderResolution:
    """Verify placeholder resolution in context builder."""

    def test_placeholder_in_skill_body(self):
        from miqi.agent.context import _resolve_placeholders

        body = "Use ~~CRM and ~~email for outreach."
        resolved = _resolve_placeholders(body)
        assert "~~CRM" not in resolved
        assert "~~email" not in resolved
        # Both placeholders should be replaced with guidance text
        assert resolved != body

    def test_unknown_placeholder_preserved(self):
        from miqi.agent.context import _resolve_placeholders

        body = "Use ~~unknown_tool for magic."
        resolved = _resolve_placeholders(body)
        # Unknown placeholders kept as-is (they're documentation)
        assert "~~unknown_tool" in resolved

    def test_skills_summary_gets_resolved(self):
        """Integration: verify build_skills_summary resolves placeholders."""
        import shutil
        import tempfile
        from pathlib import Path

        tmp = Path(tempfile.mkdtemp())
        try:
            workspace = tmp / "workspace"
            workspace.mkdir()
            builtin = tmp / "builtin"
            builtin.mkdir()

            (builtin / "kwp" / "test" / "sample-skill").mkdir(parents=True)
            (builtin / "kwp" / "test" / "sample-skill" / "SKILL.md").write_text(
                "---\n"
                "name: sample-skill\n"
                "description: A skill that references ~~CRM and ~~calendar for planning.\n"
                'metadata: {"miqi": {"requires": {}}}\n'
                "---\n\n"
                "# Sample\n\nBody...",
                encoding="utf-8",
            )

            loader = SkillsLoader(workspace, builtin_skills_dir=builtin)
            summary = loader.build_skills_summary()

            assert "<skills>" in summary
            assert "sample-skill" in summary
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
