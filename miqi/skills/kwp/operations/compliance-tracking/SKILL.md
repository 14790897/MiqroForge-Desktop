---
name: kwp-operations-compliance-tracking
description: Track compliance requirements and audit readiness. Trigger with "compliance", "audit prep", "SOC 2", "ISO 27001", "GDPR", "regulatory requirement", or when the user needs help tracking, preparing for, or documenting compliance activities.
metadata: {"miqi": {"requires": {}, "emoji": "⚙️", "category": "operations", "source": "knowledge-work-plugins"}}
---

# Compliance Tracking

Help track compliance requirements, prepare for audits, and maintain regulatory readiness.

## Common Frameworks

| Framework | Focus | Key Requirements |
|-----------|-------|-----------------|
| SOC 2 | Service organizations | Security, availability, processing integrity, confidentiality, privacy |
| ISO 27001 | Information security | Risk assessment, security controls, continuous improvement |
| GDPR | Data privacy (EU) | Consent, data rights, breach notification, DPO |
| HIPAA | Healthcare data (US) | PHI protection, access controls, audit trails |
| PCI DSS | Payment card data | Encryption, access control, vulnerability management |

## Compliance Tracking Components

### Control Inventory
- Map controls to framework requirements
- Document control owners and evidence
- Track control effectiveness

### Audit Calendar
- Upcoming audit dates and deadlines
- Evidence collection timelines
- Remediation deadlines

### Evidence Management
- What evidence is needed for each control
- Where evidence is stored
- When evidence was last collected

### Gap Analysis
- Requirements vs. current state
- Prioritized remediation plan
- Timeline to compliance

## Output

Produce compliance status dashboards, gap analyses, audit prep checklists, and evidence collection plans.

---

## Using This Skill with MiQroForge

MiQroForge includes built-in tools that cover most standalone needs: `web_search`, `web_fetch`, `read_file`, `write_file`, `edit_file`, `create_docx`, `create_pptx`, `create_xlsx`, `create_pdf`, `exec`.

To add MCP connectors for supercharged mode, configure MCP servers in MiQroForge's MCP settings page or add them via `config.json`.
