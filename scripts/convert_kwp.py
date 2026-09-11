#!/usr/bin/env python3
"""Convert anthropics/knowledge-work-plugins skills to MiQroForge format.

Usage:
  python scripts/convert_kwp.py [--repo PATH] [--output PATH] [--filter PLUGIN]

Options:
  --repo PATH    Path to a local clone of anthropics/knowledge-work-plugins
  --output PATH  Output directory (default: miqi/skills/kwp)
  --filter NAME  Only convert a specific plugin (e.g., "sales")
  --dry-run      Preview changes without writing files
  --fetch-url    Fetch a single SKILL.md from GitHub raw URL (debug aid)

The script:
1. Reads each plugin's skills/<name>/SKILL.md
2. Converts frontmatter to MiQroForge format (adds metadata field)
3. Adapts `~~placeholder` references for MiQroForge's tool landscape
4. Generates a collection manifest (plugin.json)
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

# ── Emoji assignments per plugin domain ─────────────────────────────────
PLUGIN_EMOJI = {
    "sales": "📈",
    "finance": "💰",
    "legal": "⚖️",
    "marketing": "📣",
    "customer-support": "🎧",
    "product-management": "📋",
    "data": "📊",
    "operations": "⚙️",
    "human-resources": "👥",
    "engineering": "🔧",
    "design": "🎨",
    "enterprise-search": "🔍",
    "bio-research": "🧬",
    "productivity": "✅",
    "small-business": "🏪",
}

PLUGIN_CATEGORIES = {
    "sales": "go-to-market",
    "finance": "operations",
    "legal": "operations",
    "marketing": "go-to-market",
    "customer-support": "go-to-market",
    "product-management": "product",
    "data": "technical",
    "operations": "operations",
    "human-resources": "operations",
    "engineering": "technical",
    "design": "product",
    "enterprise-search": "technical",
    "bio-research": "technical",
    "productivity": "personal",
    "small-business": "go-to-market",
}

# ── Placeholder → MiQroForge tool reference mapping ────────────────────────
_PLACEHOLDER_MAP = {
    "~~CRM": (
        "No CRM connected — use `web_search` and `web_fetch` for company "
        "research, or manually enter contact/account details"
    ),
    "~~email": (
        "No email connected — compose emails in text format using "
        "`write_file` or respond directly in chat"
    ),
    "~~chat": (
        "No chat platform connected — use the current conversation or "
        "the `message` tool if configured"
    ),
    "~~calendar": (
        "No calendar connected — describe your schedule and meetings "
        "manually, or use `exec` to check local calendar files"
    ),
    "~~knowledge base": (
        "No knowledge base connected — use `web_search` and `read_file` "
        "to find documentation"
    ),
    "~~project tracker": (
        "No project tracker connected — use `write_file` and `edit_file` "
        "to maintain local project files, or `exec` to run CLI tools"
    ),
    "~~data warehouse": (
        "Data analysis via `exec` with Python/R, `create_xlsx` for "
        "spreadsheets, or `web_search` for benchmarks"
    ),
    "~~enrichment": (
        "Use `web_search` and `web_fetch` for company and contact research"
    ),
    "~~ERP": (
        "No ERP connected — use `exec` for local data processing, "
        "`create_xlsx` for financial reports"
    ),
    "~~HRIS": (
        "No HRIS connected — describe people data manually or use "
        "`exec`/`create_xlsx` for analysis"
    ),
    "~~ATS": (
        "No ATS connected — use `write_file` to track candidates and "
        "pipelines"
    ),
    "~~support platform": (
        "No support platform connected — use `write_file` and `edit_file` "
        "to track tickets locally"
    ),
    "~~document store": (
        "Use `read_file` and `list_dir` to access local documents, "
        "`web_search` for external references"
    ),
    "~~CLM": (
        "No CLM connected — paste contract text directly or use "
        "`read_file` to load from local files"
    ),
    "~~esignature": (
        "No e-signature connected — draft the document with "
        "`create_docx` or `create_pdf`, then send manually"
    ),
    "~~social": (
        "Use `web_search` and `web_fetch` to research social media "
        "presence"
    ),
    "~~design tool": (
        "Use `create_pptx` and `create_pdf` for design deliverables, "
        "or describe visual specifications in text"
    ),
    "~~BI tool": (
        "Use `exec` with Python/matplotlib for visualization, "
        "`create_xlsx` for data tables, `create_pdf` for reports"
    ),
    "${CLAUDE_PLUGIN_ROOT}": "the current workspace skills directory",
}

# Placeholder patterns to detect
# Pattern to detect `~~placeholder` tokens — matches any of the known
# placeholder patterns followed by a word boundary or punctuation.
# Multi-word placeholders like "~~support platform" and "~~knowledge base"
# are matched literally; single-word ones like "~~CRM" stop at non-word chars.
_PLACEHOLDER_RE = re.compile(
    r'~~[A-Za-z][A-Za-z0-9]*(?: [a-z][a-z0-9]*)?(?: [a-z][a-z0-9]*)?|'
    r'\$\{CLAUDE_PLUGIN_ROOT\}'
)

# Additional tokens found in KWP skills that aren't in the main PLACEHOLDER_MAP.
# These are resolved to MiQroForge-equivalent tool guidance.
_EXTRA_PLACEHOLDERS = {
    "~~CI/CD": "CI/CD pipeline — use `exec` to run deployment scripts or check build status",
    "~~SEO tools": "SEO analysis tools — use `web_search` and `web_fetch` for SEO research",
    "~~ITSM": "ITSM platform — use `write_file` and `edit_file` to track changes locally",
    "~~AI research platform": "AI research platform — use `web_search` for literature, `exec` for analysis scripts",
}

# Skills that heavily depend on MCP connectors and should be filtered
# because they make limited sense in standalone mode
_FILTER_SKILLS = {
    # enterprise-search is entirely connector-dependent
    "enterprise-search",
}

# Individual skills to exclude (mostly talk about MCP connectors
# without standalone value)
_SKIP_SKILLS: dict[str, set[str]] = {
    "productivity": {"update", "start"},  # heavily reference /slash commands
}


def _resolve_body_placeholders(text: str) -> str:
    """Replace known ``~~placeholder`` tokens in text with MiQroForge guidance.

    Uses longest-first replacement so multi-word tokens like
    ``~~data warehouse`` match before ``~~data``.
    """
    all_tokens = {**_PLACEHOLDER_MAP, **_EXTRA_PLACEHOLDERS}
    sorted_tokens = sorted(all_tokens, key=len, reverse=True)
    for token in sorted_tokens:
        text = text.replace(token, all_tokens[token])
    return text


def resolve_placeholder(match: re.Match) -> str:
    """Replace a ``~~placeholder`` token with MiQroForge-appropriate guidance.

    Falls back to returning the token as-is with a connector note.
    """
    token = match.group(0)
    if token in _PLACEHOLDER_MAP:
        return _PLACEHOLDER_MAP[token]
    return f"[Connector: {token} — not yet configured in MiQroForge]"


def convert_body(body: str) -> str:
    """Adapt KWP skill body for MiQroForge context."""
    lines = body.split("\n")
    result: list[str] = []
    in_connectors_table = False

    for line in lines:
        # Convert slash-command headings to plain headings
        if line.startswith("# /"):
            line = "# " + line[3:].strip()

        # Replace `~~placeholder` tokens using the new exact-match function
        line = _resolve_body_placeholders(line)

        # Trim connector table width hints (those are workspace-dependent)
        # Skip the "Connectors (Optional)" section header — replace with MiQroForge note
        if line.strip() == "## Connectors (Optional)":
            result.append("## MiQroForge Tools (Standalone Mode)")
            result.append("")
            result.append(
                "> 💡 This skill works **standalone** with MiQroForge's built-in tools. "
            )
            result.append(
                "> For the **supercharged** experience, connect MCP servers for "
            )
            result.append(
                "> external tools via MiQroForge's MCP configuration. "
            )
            result.append(
                "> See the plugin's `.mcp.json` and `CONNECTORS.md` for available connectors."
            )
            result.append("")
            in_connectors_table = True
            continue

        if in_connectors_table:
            if line.startswith("|") and "|" in line[1:]:
                # Simplify connector table rows for standalone context
                parts = [p.strip() for p in line.split("|") if p.strip()]
                if len(parts) >= 2 and parts[0] not in ("Connector", "---", ":-"):
                    connector = parts[0].strip("*_ ")
                    desc = parts[1] if len(parts) > 1 else ""
                    result.append(
                        f"- **{connector}**: {desc} "
                        f"(requires MCP setup — works standalone without it)"
                    )
                continue
            elif line.strip() == "":
                result.append("")
                continue
            else:
                in_connectors_table = False

        result.append(line)

    # Append MiQroForge tool guidance footer
    result.append("")
    result.append("---")
    result.append("")
    result.append("## Using This Skill with MiQroForge")
    result.append("")
    result.append(
        "MiQroForge includes built-in tools that cover most standalone needs: "
        "`web_search`, `web_fetch`, `read_file`, `write_file`, `edit_file`, "
        "`create_docx`, `create_pptx`, `create_xlsx`, `create_pdf`, `exec`."
    )
    result.append("")
    result.append(
        "To add MCP connectors for supercharged mode, configure MCP servers "
        "in MiQroForge's MCP settings page or add them via `config.json`."
    )

    return "\n".join(result)


def convert_frontmatter(kwp_meta: dict, plugin_name: str, skill_name: str) -> str:
    """Convert KWP frontmatter to MiQroForge format with metadata JSON."""
    name = kwp_meta.get("name", skill_name)
    description = kwp_meta.get("description", "")

    # Build MiQroForge metadata
    miqi_meta: dict = {
        "requires": {},
    }

    if plugin_name in PLUGIN_EMOJI:
        miqi_meta["emoji"] = PLUGIN_EMOJI[plugin_name]

    if plugin_name in PLUGIN_CATEGORIES:
        miqi_meta["category"] = PLUGIN_CATEGORIES[plugin_name]
    miqi_meta["source"] = "knowledge-work-plugins"

    # ── Build the new frontmatter ──
    lines = ["---"]
    lines.append(f"name: kwp-{plugin_name}-{name}")
    lines.append(f"description: {description}")
    lines.append(f'metadata: {json.dumps({"miqi": miqi_meta}, ensure_ascii=False)}')
    lines.append("---")

    return "\n".join(lines)


def parse_kwp_frontmatter(content: str) -> tuple[dict, str]:
    """Parse KWP SKILL.md frontmatter. Returns (metadata_dict, body_text)."""
    if not content.startswith("---"):
        return {}, content

    match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
    if not match:
        return {}, content

    fm_text = match.group(1)
    body = content[match.end():]

    meta: dict = {}
    for line in fm_text.split("\n"):
        if ":" in line:
            key, _, value = line.partition(":")
            raw = value.strip().strip("\"'")
            # Simple boolean coercion
            if raw.lower() in ("true", "yes"):
                raw_val: bool | str = True
            elif raw.lower() in ("false", "no"):
                raw_val = False
            else:
                raw_val = raw
            meta[key.strip()] = raw_val

    return meta, body


def convert_skill(
    plugin_name: str,
    skill_name: str,
    source_path: Path,
    output_dir: Path,
    dry_run: bool = False,
) -> bool:
    """Convert a single KWP skill to MiQroForge format. Returns True if written."""
    raw = source_path.read_text(encoding="utf-8")

    meta, body = parse_kwp_frontmatter(raw)
    new_fm = convert_frontmatter(meta, plugin_name, skill_name)
    new_body = convert_body(body.strip())

    output = f"{new_fm}\n\n{new_body}\n"

    skill_dir = output_dir / plugin_name / skill_name
    skill_file = skill_dir / "SKILL.md"

    if dry_run:
        print(f"  [dry-run] Would write: {skill_file}")
        return True

    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file.write_text(output, encoding="utf-8")
    return True


def get_skill_refs(plugin_name: str, skills_dir: Path) -> list[str]:
    """Find all SKILL.md files under a plugin's skills/ directory."""
    if not skills_dir.is_dir():
        return []
    refs = []
    for item in sorted(skills_dir.iterdir()):
        if item.is_dir() and (item / "SKILL.md").exists():
            refs.append(item.name)
    return refs


def _copy_commands_dir(
    src: Path, dst: Path, dry_run: bool
) -> int:
    """Copy <plugin>/commands/*.md verbatim.

    Slash command files use their own frontmatter (description,
    argument-hint) which PluginManager parses directly — we don't
    rewrite them the way we rewrite SKILL.md.
    """
    if not src.is_dir():
        return 0
    count = 0
    for cmd_file in sorted(src.glob("*.md")):
        if dry_run:
            print(f"    [dry-run] Would copy: {dst / cmd_file.name}")
        else:
            dst.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cmd_file, dst / cmd_file.name)
        count += 1
    return count


def _parse_command_metadata(cmd_file: Path) -> dict | None:
    """Return ``{name, description}`` from a KWP command file, or None."""
    import re as _re

    try:
        raw = cmd_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    desc = ""
    fm = _re.match(r"^---\n(.*?)\n---\n", raw, _re.DOTALL)
    if fm:
        for line in fm.group(1).split("\n"):
            if line.startswith("description:"):
                desc = line.split(":", 1)[1].strip().strip("\"'")
    return {"name": cmd_file.stem, "description": desc}


def generate_plugin_json(
    plugin_name: str,
    skills: list[str],
    output_dir: Path,
    dry_run: bool = False,
    commands_metadata: list[dict] | None = None,
) -> None:
    """Generate a MiQroForge-compatible plugin.json for one KWP plugin."""
    manifest = {
        "name": f"kwp-{plugin_name}",
        "version": "1.0.0",
        "description": (
            f"Knowledge Work Plugins — {plugin_name.replace('-', ' ').title()} "
            f"({len(skills)} skills). "
            f"Adapted from anthropics/knowledge-work-plugins for MiQroForge."
        ),
        "author": "Anthropic (adapted for MiQroForge)",
        "skills": skills,
        "mcp_servers": [],
        "slash_commands": commands_metadata or [],
        "hooks": [],
    }

    if dry_run:
        print(f"  [dry-run] Would write: {output_dir / plugin_name / 'plugin.json'}")
        return

    out = output_dir / plugin_name
    out.mkdir(parents=True, exist_ok=True)
    (out / "plugin.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def generate_collection_readme(output_dir: Path, all_plugins: list[str]) -> None:
    """Write a README explaining the KWP collection."""
    lines = [
        "# Knowledge Work Plugins for MiQroForge",
        "",
        "Skills adapted from [anthropics/knowledge-work-plugins]"
        "(https://github.com/anthropics/knowledge-work-plugins) "
        "for use with MiQroForge Desktop Agent.",
        "",
        "## How to use",
        "",
        "These skills appear automatically in MiQroForge when placed under "
        "`miqi/skills/kwp/`. The agent can read them via `read_file` "
        "when the skill description matches the current task.",
        "",
        "## Standalone vs Supercharged",
        "",
        "All skills work **standalone** with MiQroForge's built-in tools "
        "(`web_search`, `web_fetch`, `write_file`, `exec`, `create_docx`, "
        "`create_pptx`, `create_xlsx`, `create_pdf`).",
        "",
        "For the **supercharged** experience described in the original "
        "plugin docs, configure MCP servers via MiQroForge's MCP settings.",
        "",
        "## Included Plugins",
        "",
    ]

    for name in sorted(all_plugins):
        emoji = PLUGIN_EMOJI.get(name, "📦")
        category = PLUGIN_CATEGORIES.get(name, "general")
        lines.append(f"- {emoji} **{name}** ({category})")

    lines.extend([
        "",
        "## License",
        "",
        "Original content: Apache 2.0 — [anthropics/knowledge-work-plugins]"
        "(https://github.com/anthropics/knowledge-work-plugins)",
        "Adaptation: MIT — [MiQroForge Desktop](http://git.miqroera.com/intership/miqi-desktop.git)",
    ])

    (output_dir / "README.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Convert anthropics/knowledge-work-plugins to MiQroForge format"
    )
    parser.add_argument(
        "--repo",
        help="Path to local knowledge-work-plugins clone",
        default=None,
    )
    parser.add_argument(
        "--output",
        help="Output directory",
        default="miqi/skills/kwp",
    )
    parser.add_argument(
        "--filter",
        help="Only convert a specific plugin (e.g., sales)",
        default=None,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview without writing files",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available plugins and exit",
    )
    parser.add_argument(
        "--fetch-url",
        help="Fetch a specific SKILL.md from a GitHub raw URL (debug)",
        default=None,
    )
    args = parser.parse_args()

    # Debug mode: fetch one URL
    if args.fetch_url:
        import urllib.request
        with urllib.request.urlopen(args.fetch_url) as resp:
            content = resp.read().decode("utf-8")
        meta, body = parse_kwp_frontmatter(content)
        plugin = (args.filter or "debug")
        new_fm = convert_frontmatter(meta, plugin, "debug-skill")
        new_body = convert_body(body.strip())
        print(new_fm)
        print()
        print(new_body)
        return 0

    output_dir = Path(args.output).resolve()

    if args.repo:
        repo_path = Path(args.repo).resolve()
    else:
        # Auto-detect: look for a clone or try to use gh CLI
        candidates = [
            Path("~/knowledge-work-plugins").expanduser(),
            Path("/tmp/knowledge-work-plugins"),
        ]
        repo_path = None
        for c in candidates:
            if c.is_dir() and (c / ".claude-plugin").is_dir():
                repo_path = c
                break

        if repo_path is None:
            print(
                "No local clone found. Please provide --repo PATH to "
                "a clone of anthropics/knowledge-work-plugins.",
                file=sys.stderr,
            )
            print(
                "  git clone --depth=1 https://github.com/anthropics/knowledge-work-plugins.git /tmp/knowledge-work-plugins",
                file=sys.stderr,
            )
            return 1

    # ── Discover plugins ──
    plugins: dict[str, Path] = {}
    for item in sorted(repo_path.iterdir()):
        if not item.is_dir():
            continue
        cp_dir = item / ".claude-plugin"
        if not cp_dir.is_dir():
            continue
        manifest = cp_dir / "plugin.json"
        if not manifest.exists():
            continue
        if args.filter and item.name != args.filter:
            continue
        plugins[item.name] = item

    if args.list:
        print(f"Available plugins ({len(plugins)}):")
        for name in sorted(plugins):
            print(f"  {name}")
        return 0

    if not plugins:
        print("No plugins found.", file=sys.stderr)
        return 1

    print(f"Converting {len(plugins)} plugin(s) → {output_dir}")
    total_skills = 0
    converted_plugins: list[str] = []

    for plugin_name in sorted(plugins):
        plugin_path = plugins[plugin_name]

        # Apply filter
        if plugin_name in _FILTER_SKILLS:
            print(f"  ⏭  {plugin_name}: filtered (connector-dependent)")
            continue

        skills_dir = plugin_path / "skills"
        skill_refs = get_skill_refs(plugin_name, skills_dir)

        # Filter individual skills
        skip = _SKIP_SKILLS.get(plugin_name, set())
        skill_refs = [s for s in skill_refs if s not in skip]

        if not skill_refs:
            print(f"  ⏭  {plugin_name}: no skills found")
            continue

        print(f"  📦 {plugin_name} ({len(skill_refs)} skills)")

        converted = 0
        for skill_name in skill_refs:
            source = skills_dir / skill_name / "SKILL.md"
            if not source.exists():
                print(f"    ⚠  {skill_name}: SKILL.md not found")
                continue
            try:
                convert_skill(plugin_name, skill_name, source, output_dir, args.dry_run)
                converted += 1
            except Exception as e:
                print(f"    ✗  {skill_name}: {e}")

        # Also copy slash commands from <plugin>/commands/*.md verbatim
        # (KWP command files have a different, lighter frontmatter that we
        # want to preserve intact so PluginManager can read description +
        # body directly).
        commands_metadata: list[dict] = []
        commands_dir = plugin_path / "commands"
        if commands_dir.is_dir():
            cmd_count = _copy_commands_dir(
                commands_dir, output_dir / plugin_name / "commands",
                args.dry_run,
            )
            for cmd_file in sorted(commands_dir.glob("*.md")):
                md = _parse_command_metadata(cmd_file)
                if md:
                    commands_metadata.append(md)
            if cmd_count and not args.dry_run:
                print(f"    + {cmd_count} slash command(s)")

        if converted > 0:
            total_skills += converted
            converted_plugins.append(plugin_name)
            generate_plugin_json(
                plugin_name,
                skill_refs,
                output_dir,
                args.dry_run,
                commands_metadata=commands_metadata,
            )

    # Generate collection-level files
    if converted_plugins:
        generate_collection_readme(output_dir, converted_plugins)

    print(f"\nDone. Converted {total_skills} skills across {len(converted_plugins)} plugins.")

    if not args.dry_run:
        # Verify
        kwp_skills = sum(
            1 for _ in output_dir.rglob("SKILL.md")
        )
        print(f"  SKILL.md files created: {kwp_skills}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
