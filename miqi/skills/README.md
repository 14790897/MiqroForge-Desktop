# Assistant Skills

This directory contains built-in skills that extend MiQroForge capabilities.

## Skill Format

Each skill is a directory containing a `SKILL.md` file with:
- YAML frontmatter (name, description, metadata)
- Markdown instructions for the agent

## Attribution

These skills are adapted from [OpenClaw](https://github.com/openclaw/openclaw)'s skill system.
The skill format and metadata structure follow OpenClaw's conventions to maintain compatibility.

## Available Skills

| Skill | Description |
|-------|-------------|
| `cron` | Schedule reminders and recurring tasks |
| `github` | Interact with GitHub using the `gh` CLI |
| `memory` | RAM-first memory system with short-term and long-term recall |
| `paper-research` | Search, download, translate, and summarize academic papers |
| `feishu-report` | Deliver content to Feishu in the right format (text/card/doc/calendar/task) |
| `summarize` | Summarize URLs, files, and YouTube videos |
| `tmux` | Remote-control tmux sessions |
| `weather` | Get weather info using wttr.in and Open-Meteo |
| `workspace-cleanup` | Organize the miqi workspace directory |
| `qraft-workflowspec-export` | Export session outputs to a schema-validated WorkflowRun JSON, validate (schema + semantic A/B), confirm the plan with the user, and upload to the MiQroForge platform (dataUpload) |
| `skill-creator` | Create new skills |
| `pptx-generator` | Generate, edit, and read PowerPoint presentations |
