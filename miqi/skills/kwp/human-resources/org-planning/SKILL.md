---
name: kwp-human-resources-org-planning
description: Headcount planning, org design, and team structure optimization. Trigger with "org planning", "headcount plan", "team structure", "reorg", "who should we hire next", or when the user is thinking about team size, reporting structure, or organizational design.
metadata: {"miqi": {"requires": {}, "emoji": "👥", "category": "operations", "source": "knowledge-work-plugins"}}
---

# Org Planning

Help plan organizational structure, headcount, and team design.

## Planning Dimensions

- **Headcount**: How many people do we need, in what roles, by when?
- **Structure**: Reporting lines, span of control, team boundaries
- **Sequencing**: Which hires are most critical? What's the right order?
- **Budget**: Headcount cost modeling and trade-offs

## Healthy Org Benchmarks

| Metric | Healthy Range | Warning Sign |
|--------|---------------|--------------|
| Span of control | 5-8 direct reports | < 3 or > 12 |
| Management layers | 4-6 for 500 people | Too many = slow decisions |
| IC-to-manager ratio | 6:1 to 10:1 | < 4:1 = top-heavy |
| Team size | 5-9 people | < 4 = lonely, > 12 = hard to manage |

## Output

Produce org charts (text-based), headcount plans with cost modeling, and sequenced hiring roadmaps. Flag structural issues like single points of failure or excessive management overhead.

---

## Using This Skill with MiQroForge

MiQroForge includes built-in tools that cover most standalone needs: `web_search`, `web_fetch`, `read_file`, `write_file`, `edit_file`, `create_docx`, `create_pptx`, `create_xlsx`, `create_pdf`, `exec`.

To add MCP connectors for supercharged mode, configure MCP servers in MiQroForge's MCP settings page or add them via `config.json`.
