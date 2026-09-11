---
name: kwp-operations-risk-assessment
description: Identify, assess, and mitigate operational risks. Trigger with "what are the risks", "risk assessment", "risk register", "what could go wrong", or when the user is evaluating risks associated with a project, vendor, process, or decision.
metadata: {"miqi": {"requires": {}, "emoji": "⚙️", "category": "operations", "source": "knowledge-work-plugins"}}
---

# Risk Assessment

Systematically identify, assess, and plan mitigations for operational risks.

## Risk Assessment Matrix

| | Low Impact | Medium Impact | High Impact |
|---|-----------|---------------|-------------|
| **High Likelihood** | Medium | High | Critical |
| **Medium Likelihood** | Low | Medium | High |
| **Low Likelihood** | Low | Low | Medium |

## Risk Categories

- **Operational**: Process failures, staffing gaps, system outages
- **Financial**: Budget overruns, vendor cost increases, revenue impact
- **Compliance**: Regulatory violations, audit findings, policy breaches
- **Strategic**: Market changes, competitive threats, technology shifts
- **Reputational**: Customer impact, public perception, partner relationships
- **Security**: Data breaches, access control failures, third-party vulnerabilities

## Risk Register Format

For each risk, document:
- **Description**: What could happen
- **Likelihood**: High / Medium / Low
- **Impact**: High / Medium / Low
- **Risk Level**: Critical / High / Medium / Low
- **Mitigation**: What we're doing to reduce likelihood or impact
- **Owner**: Who is responsible for managing this risk
- **Status**: Open / Mitigated / Accepted / Closed

## Output

Produce a prioritized risk register with specific, actionable mitigations. Focus on risks that are controllable and material.

---

## Using This Skill with MiQroForge

MiQroForge includes built-in tools that cover most standalone needs: `web_search`, `web_fetch`, `read_file`, `write_file`, `edit_file`, `create_docx`, `create_pptx`, `create_xlsx`, `create_pdf`, `exec`.

To add MCP connectors for supercharged mode, configure MCP servers in MiQroForge's MCP settings page or add them via `config.json`.
