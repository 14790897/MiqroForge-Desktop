---
name: kwp-operations-change-request
description: Create a change management request with impact analysis and rollback plan. Use when proposing a system or process change that needs approval, preparing a change record for CAB review, documenting risk and rollback steps before a deployment, or planning stakeholder communications for a rollout.
metadata: {"miqi": {"requires": {}, "emoji": "⚙️", "category": "operations", "source": "knowledge-work-plugins"}}
---

# change-request

> If you see unfamiliar placeholders or need to check which tools are connected, see [CONNECTORS.md](../../CONNECTORS.md).

Create a structured change request with impact analysis, risk assessment, and rollback plan.

## Usage

```
/change-request $ARGUMENTS
```

## Change Management Framework

Apply the assess-plan-execute-sustain framework when building the request:

### 1. Assess
- What is changing?
- Who is affected?
- How significant is the change? (Low / Medium / High)
- What resistance should we expect?

### 2. Plan
- Communication plan (who, what, when, how)
- Training plan (what skills are needed, how to deliver)
- Support plan (help desk, champions, FAQs)
- Timeline with milestones

### 3. Execute
- Announce and explain the "why"
- Train and support
- Monitor adoption
- Address resistance

### 4. Sustain
- Measure adoption and effectiveness
- Reinforce new behaviors
- Address lingering issues
- Document lessons learned

## Communication Principles

- Explain the **why** before the **what**
- Communicate early and often
- Use multiple channels
- Acknowledge what's being lost, not just what's being gained
- Provide a clear path for questions and concerns

## Output

```markdown
## Change Request: [Title]
**Requester:** [Name] | **Date:** [Date] | **Priority:** [Critical/High/Medium/Low]
**Status:** Draft | Pending Approval | Approved | In Progress | Complete

### Description
[What is changing and why]

### Business Justification
[Why this change is needed — cost savings, compliance, efficiency, risk reduction]

### Impact Analysis
| Area | Impact | Details |
|------|--------|---------|
| Users | [High/Med/Low/None] | [Who is affected and how] |
| Systems | [High/Med/Low/None] | [What systems are affected] |
| Processes | [High/Med/Low/None] | [What workflows change] |
| Cost | [High/Med/Low/None] | [Budget impact] |

### Risk Assessment
| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| [Risk] | [H/M/L] | [H/M/L] | [How to mitigate] |

### Implementation Plan
| Step | Owner | Timeline | Dependencies |
|------|-------|----------|--------------|
| [Step] | [Person] | [Date] | [What it depends on] |

### Communication Plan
| Audience | Message | Channel | Timing |
|----------|---------|---------|--------|
| [Who] | [What to tell them] | [How] | [When] |

### Rollback Plan
[Step-by-step plan to reverse the change if needed]
- Trigger: [When to roll back]
- Steps: [How to roll back]
- Verification: [How to confirm rollback worked]

### Approvals Required
| Approver | Role | Status |
|----------|------|--------|
| [Name] | [Role] | Pending |
```

## If Connectors Available

If **ITSM platform — use `write_file` and `edit_file` to track changes locally** is connected:
- Create the change request ticket automatically
- Pull change advisory board schedule and approval workflows

If **No project tracker connected — use `write_file` and `edit_file` to maintain local project files, or `exec` to run CLI tools** is connected:
- Link to related implementation tasks and dependencies
- Track change progress against milestones

If **No chat platform connected — use the current conversation or the `message` tool if configured** is connected:
- Draft stakeholder notifications for the communication plan
- Post change updates to the relevant team channels

## Tips

1. **Be specific about impact** — "Everyone" is not an impact assessment. "200 users in the billing team" is.
2. **Always have a rollback plan** — Even if you're confident, plan for failure.
3. **Communicate early** — Surprises create resistance. Previews create buy-in.

---

## Using This Skill with MiQroForge

MiQroForge includes built-in tools that cover most standalone needs: `web_search`, `web_fetch`, `read_file`, `write_file`, `edit_file`, `create_docx`, `create_pptx`, `create_xlsx`, `create_pdf`, `exec`.

To add MCP connectors for supercharged mode, configure MCP servers in MiQroForge's MCP settings page or add them via `config.json`.
