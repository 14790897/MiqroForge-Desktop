"""Tests for KWP converter script."""

from __future__ import annotations

# Import converter functions
import importlib.util as _util
import re
from pathlib import Path

_converter = _util.spec_from_file_location(
    "convert_kwp", str(Path(__file__).parent.parent.parent / "scripts" / "convert_kwp.py")
)
_conv = _util.module_from_spec(_converter)
_converter.loader.exec_module(_conv)

convert_frontmatter = _conv.convert_frontmatter
convert_body = _conv.convert_body
parse_kwp_frontmatter = _conv.parse_kwp_frontmatter
resolve_placeholder = _conv.resolve_placeholder
_resolve_body_placeholders = _conv._resolve_body_placeholders
PLUGIN_EMOJI = _conv.PLUGIN_EMOJI
_PLACEHOLDER_MAP = _conv._PLACEHOLDER_MAP


class TestFrontmatterConversion:
    def test_basic_conversion(self):
        kwp_meta = {"name": "account-research", "description": "Research a company"}
        result = convert_frontmatter(kwp_meta, "sales", "account-research")

        assert "name: kwp-sales-account-research" in result
        assert "description: Research a company" in result
        assert "metadata:" in result
        assert '"miqi"' in result
        assert '"emoji"' in result
        assert '"source": "knowledge-work-plugins"' in result

    def test_emoji_assignment(self):
        kwp_meta = {"name": "test", "description": "test"}
        result = convert_frontmatter(kwp_meta, "sales", "test")
        assert PLUGIN_EMOJI["sales"] in result

        result_no_emoji = convert_frontmatter(kwp_meta, "unknown-plugin", "test")
        assert '"emoji"' not in result_no_emoji

    def test_category_assignment(self):
        kwp_meta = {"name": "test", "description": "test"}
        # finance → operations category
        result = convert_frontmatter(kwp_meta, "finance", "test")
        assert '"category": "operations"' in result

    def test_argument_hint_not_in_output(self):
        kwp_meta = {
            "name": "review-contract",
            "description": "Review a contract",
            "argument-hint": "<contract file>",
        }
        result = convert_frontmatter(kwp_meta, "legal", "review-contract")
        assert "argument-hint" not in result


class TestFrontmatterParsing:
    def test_parse_simple(self):
        content = "---\nname: test\n---\n\n# Body"
        meta, body = parse_kwp_frontmatter(content)
        assert meta == {"name": "test"}
        assert body.strip() == "# Body"

    def test_parse_with_description(self):
        content = (
            "---\n"
            "name: call-prep\n"
            'description: "Prep for a sales call"\n'
            "---\n"
            "\n"
            "# Call Prep\n"
            "\n"
            "Instructions..."
        )
        meta, body = parse_kwp_frontmatter(content)
        assert meta["name"] == "call-prep"
        assert meta["description"] == "Prep for a sales call"
        assert "# Call Prep" in body

    def test_no_frontmatter(self):
        content = "# Just a heading\n\nNo frontmatter"
        meta, body = parse_kwp_frontmatter(content)
        assert meta == {}
        assert body == content


class TestBodyConversion:
    def test_placeholder_replacement(self):
        body = "Use ~~CRM for lookup and ~~email for outreach."
        result = convert_body(body)
        assert "~~CRM" not in result
        assert "web_search" in result
        assert "~~email" not in result
        assert result.count("No CRM connected") > 0 or result.count("web_search") > 0

    def test_connectors_table_simplified(self):
        body = (
            "## Connectors (Optional)\n"
            "\n"
            "Connect your tools:\n"
            "| Connector | What It Adds |\n"
            "|-----------|--------------|\n"
            "| **CRM** | Contact data |\n"
            "| **Email** | Messages |\n"
            "\n"
            "More text."
        )
        result = convert_body(body)
        assert "## MiQroForge Tools (Standalone Mode)" in result
        assert "- **CRM**" in result or "**CRM**" in result
        assert "- **Email**" in result or "**Email**" in result

    def test_slash_command_heading_converted(self):
        body = "# /ticket-triage\n\nTriage instructions."
        result = convert_body(body)
        assert "/ticket-triage" not in result
        assert "# ticket-triage" in result

    def test_miqi_footer_added(self):
        body = "# Test Skill\n\nDo something."
        result = convert_body(body)
        assert "## Using This Skill with MiQroForge" in result
        assert "MiQroForge includes built-in tools" in result


class TestPlaceholderResolution:
    def test_resolve_known_placeholder(self):
        result = resolve_placeholder(re.match(r"~~CRM", "~~CRM"))
        assert result != "~~CRM"
        assert "CRM" in result

    def test_placeholder_map_coverage(self):
        """Verify all common placeholders are covered."""
        expected = [
            "~~CRM",
            "~~email",
            "~~chat",
            "~~calendar",
            "~~knowledge base",
            "~~project tracker",
            "~~data warehouse",
            "~~enrichment",
            "~~ERP",
            "~~HRIS",
            "~~ATS",
            "~~support platform",
            "~~document store",
            "~~CLM",
            "~~esignature",
            "~~social",
            "~~design tool",
            "~~BI tool",
        ]
        for token in expected:
            assert token in _PLACEHOLDER_MAP, f"Missing: {token}"


class TestOutputFormat:
    """Verify the MiQroForge skill format is compatible with SkillsLoader."""

    def test_frontmatter_parsable_by_skillsloader(self):
        """SkillsLoader uses simple key: value line parsing."""
        fm = (
            "---\n"
            'name: kwp-sales-test\n'
            'description: Test skill description\n'
            'metadata: {"miqi": {"requires": {}, "emoji": "📈", "source": "knowledge-work-plugins"}}\n'
            "---"
        )
        # Simulate SkillsLoader parsing
        metadata = {}
        for line in fm.split("\n"):
            if line == "---" or not line.strip():
                continue
            if ":" in line:
                key, value = line.split(":", 1)
                metadata[key.strip()] = value.strip().strip("'\"")
        assert metadata.get("name") == "kwp-sales-test"
        assert "knowledge-work-plugins" in metadata.get("metadata", "")

    def test_full_roundtrip(self):
        """Convert a realistic KWP skill and verify output is valid."""
        raw = (
            "---\n"
            "name: account-research\n"
            "description: Research a company with web search and CRM.\n"
            "---\n"
            "\n"
            "# Account Research\n"
            "\n"
            "## How It Works\n"
            "\n"
            "Use ~~CRM for contacts and ~~enrichment for details.\n"
            "\n"
            "## Connectors (Optional)\n"
            "\n"
            "| Connector | What It Adds |\n"
            "|-----------|--------------|\n"
            "| **CRM** | Contact data |\n"
        )
        meta, body = parse_kwp_frontmatter(raw)
        fm = convert_frontmatter(meta, "sales", "account-research")
        new_body = convert_body(body)
        output = f"{fm}\n\n{new_body}"

        # Verify structure
        assert "---" in output[:10]
        assert "kwp-sales-account-research" in output
        # Placeholders resolved
        assert "~~CRM" not in output
        assert "~~enrichment" not in output
        # Connectors replaced
        assert "MiQroForge Tools (Standalone Mode)" in output
