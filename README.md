# MiQroForge Desktop

<p align="center">
  <em>🐈‍⬛🪶 A lightweight, extensible personal AI agent framework with a modern desktop interface</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11%20%7C%203.12-blue" alt="Python 3.11 | 3.12" />
  <img src="https://img.shields.io/badge/node.js-20+-green" alt="Node.js 20+" />
  <img src="https://img.shields.io/badge/status-alpha-orange" alt="Development Status: Alpha" />
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License" /></a>
</p>

---

## Overview

MiQroForge Desktop is an Electron-based desktop application that provides a modern graphical interface for the MiQroForge AI agent. It combines powerful AI agent capabilities with an intuitive user interface, supporting chat interaction, memory management, task scheduling, and more.
![interface](interface.png)

### Core Positioning

- 🎯 **Personal AI Agent** — not just a chatbot: persistent memory, learned skills, file operations, and scheduled tasks
- 🔧 **Highly Extensible** — MCP protocol for external tools, custom skills, and pluggable LLM providers
- 🖥️ **Native Desktop Experience** — Electron with system-level integration (WSL2 sandbox, filesystem operations)
- 🔒 **Local-First** — all data stored locally; non-destructive file editing with versioned snapshots
- 📋 **Typed Application Protocol** — typed AppServer with JSON Schema catalog, method stability tracking, and handler-boundary validation

### Tech Stack

| Layer | Technology | Version |
|-------|-----------|---------|
| Desktop Framework | Electron | 35.2 |
| Frontend UI | React + TypeScript | 19.1 / 5.8 |
| CSS | Tailwind CSS 4 | 4.x |
| Component Library | Radix UI + Lucide Icons | — |
| Python Runtime | Python (asyncio) | 3.11+ |
| Data Validation | Pydantic v2 | 2.12+ |
| CLI Framework | Typer | 0.20+ |
| Build (Desktop) | electron-vite + electron-builder | 3.1 / 26.0 |
| Build (Python) | PyInstaller + Hatchling | 6.20+ |

---

## Key Features

| Feature | Description |
|---|---|
| **Smart Chat** | Natural language conversation with streaming responses and tool-call progress |
| **Multi-Provider** | OpenAI, Anthropic, Gemini, OpenRouter, DeepSeek, and more — with provider resilience |
| **Typed Protocol** | Typed AppServer with method specs, JSON Schema catalog, and handler-boundary validation |
| **Memory System** | Long-term memory snapshots, self-improvement lessons, and cross-session recall |
| **Task Scheduler** | Cron-based scheduled tasks with timezone support |
| **Skill System** | Create, upload, and manage agent skills; SkillHub registry integration |
| **Plugin Ecology** | MCP servers, plugins, and marketplace with deterministic catalog |
| **Sandbox Execution** | bwrap-based sandbox with LANDLOCK filesystem rules, streaming I/O, and process lifecycle |
| **File Management** | Workspace FS with watch, fuzzy search, snapshot/versioning, and non-destructive editing |
| **Replay & Debug** | Deterministic replay of turns, timeline, and messages for inspection |
| **Session Management** | Browse, search, archive, import/export conversation history |
| **Desktop App** | 15+ feature pages with real-time streaming, typewriter animation, and context menus |

---

## Quick Start

### Prerequisites

- **Python 3.11+** — to run the MiQroForge backend
- **Node.js 20+** — to run Electron frontend
- **uv** — Python package manager (recommended)

> **Windows users** can install `uv` and Node.js with winget (included in Windows 10 1709+ / Windows 11; if missing, install "App Installer" from the Microsoft Store first):
>
> ```bash
> # Install uv
> winget install --id astral-sh.uv -e
>
> # Install nvm-windows, then Node.js 22
> winget install --id CoreyButler.NVMforWindows -e
> nvm install 22
> ```
>
> After installing nvm-windows, open a new terminal before running `nvm`.

### Installation

```bash
# 1. Clone the repository
git clone http://git.miqroera.com/intership/miqi-desktop.git
cd miqi-desktop

# 2. Install Python dependencies
uv sync

# 3. Install frontend dependencies
cd apps/desktop
npm install
```

### Development Mode

```bash
# Start Electron dev server with hot-reload
cd apps/desktop
npm run dev
```

### Production Build

**One-step build** (recommended):

```bash
cd apps/desktop
npm run build:all    # Python backend → Frontend compile → Electron package
```

**Step-by-step build**:

```bash
cd apps/desktop

# 1. Build Python backend (generates dist/miqi-bridge.exe)
npm run build:bridge

# 2. Compile frontend
npm run build

# 3. Package as desktop application
npx electron-builder --win --publish never
```

The packaged `miqi-bridge.exe` is a self-contained binary (PyInstaller onefile) that includes Python and all dependencies — no system Python installation required on the target machine. It also supports a `--check` flag for environment validation:

```bash
miqi-bridge.exe --check
# Output: {"ok": true, "python_version": "3.12.10", "issues": []}
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    MiQroForge Desktop App                   │
├─────────────────────────────────────────────────────────────┤
│  Electron Frontend                                          │
│  ├── React 19 + TypeScript                                 │
│  ├── Tailwind CSS 4 + shadcn/ui                            │
│  └── 15+ Feature Pages (Chat, Agents, Skills, MCPs, ...)   │
├─────────────────────────────────────────────────────────────┤
│  Bridge (IPC Communication)                                 │
│  ├── stdin/stdout JSON-line protocol                        │
│  ├── State synchronization + Log forwarding                 │
│  └── BridgeRuntimeLoop (persistent asyncio event loop)      │
├─────────────────────────────────────────────────────────────┤
│  AppServer (Typed Protocol Layer)                            │
│  ├── ProtocolRegistry (typed method specs)                  │
│  ├── Typed Envelopes (Pydantic v2)                          │
│  ├── JSON Schema Draft 2020-12 Catalog                      │
│  └── Handler Typed Validation                               │
├─────────────────────────────────────────────────────────────┤
│  MiQroForge Runtime Engine                                  │
│  ├── RuntimeSession / TaskRunner / TurnRunner               │
│  ├── HistoryRuntime + LedgerRuntime (SQLite persistence)    │
│  ├── ContextRuntime (compaction, token budgeting)           │
│  ├── ThreadRuntime (fork, rollback, import/export)          │
│  └── ReplayRuntime (deterministic replay inspection)        │
├─────────────────────────────────────────────────────────────┤
│  Execution & Sandbox                                        │
│  ├── ToolOrchestrator (approval → sandbox → execute)       │
│  ├── PermissionEngine + ApprovalPolicy + HookRuntime        │
│  ├── bwrap Sandbox (LANDLOCK, streaming, cancellation)      │
│  └── Workbench Process Runtime (command/exec, process/*)    │
├─────────────────────────────────────────────────────────────┤
│  Tools & Integrations                                       │
│  ├── Built-in Tools (filesystem, shell, web, papers, ...)   │
│  ├── MCP Client (external tool servers)                     │
│  ├── Plugin Manager + Skill Loader                          │
│  └── Office Document Tools (docx, pptx, xlsx)               │
└─────────────────────────────────────────────────────────────┘
```

1. Launch the application
2. Go through the setup wizard:
   - **Environment Check** — validates Python and dependencies (bundled exe auto-detects; dev mode checks system Python)
   - **WSL2 Setup** — (Windows only) auto-detects and installs WSL2 for sandbox support
   - **LLM Provider** — configure API keys and default model
3. Start chatting with the AI agent
### Protocol Method Families

| Family | Scope | Methods |
|--------|-------|---------|
| `turn/*` | Turn | start, interrupt, steer |
| `thread/*` | Thread | list, get, rollback, fork, delete, compact/start, inject_items |
| `fs/*` | Filesystem | readFile, writeFile, createDirectory, getMetadata, readDirectory, remove, copy, watch, unwatch |
| `fuzzyFileSearch/*` | Filesystem | sessionStart, sessionUpdate, sessionStop |
| `command/exec` | Process | exec, exec/write, exec/resize, exec/terminate |
| `process/*` | Process | spawn, writeStdin, resizePty, kill, list, get, snapshot |
| `replay.*` | Debug | turns, timeline, messages |
| `config/*` | Session | get, batchWrite |
| `model/*` | Session | list, get |
| `feature/*` | Session | list, set |
| `permission/*` | Session | listProfiles, getProfile |
| `plugin/*` | Session | list, install, uninstall, enable, disable, configure |
| `skills/*` | Session | list, get, create, upload, delete, setExtraRoots |
| `mcp/*` | Session | listServers, getServer, status |
| `agent/*` | Session | list, get, spawn, kill |
| `protocol/*` | Connection | catalog, method_names, schema |

---

## Configuration

The application configuration file is located at `~/.miqi/config.json`:

```json
{
  "providers": {
    "openai": { "apiKey": "sk-..." },
    "anthropic": { "apiKey": "sk-ant-..." }
  },
  "agents": {
    "defaults": {
      "model": "claude-sonnet-4-6",
      "temperature": 0.1,
      "maxToolIterations": 100
    }
  },
  "tools": {
    "restrictToWorkspace": true
  }
}
```

### Environment Variables

| Variable | Description |
|---|---|
| `MIQI_PYTHON_PATH` | Custom Python interpreter path |
| `MIQI_AGENTS__DEFAULTS__MODEL` | Override default model |

---

## Development Guide

### Project Structure

```
miqi-desktop/
├── miqi/                         # Python backend
│   ├── runtime/                  # Runtime engine (AppServer, Session, Turn, Thread, Replay, Agent, MCP, ...)
│   ├── agent/                    # Agent logic, tools, memory, trace, context compression, smart routing
│   ├── bridge/                   # Electron bridge service (IPC protocol)
│   ├── bus/                      # Internal message bus (async in/out queues)
│   ├── execution/                # Tool orchestrator, permissions, approval, hooks, sandbox policy
│   ├── providers/                # LLM provider implementations + resilience
│   ├── protocol/                 # Typed commands, events, permissions (runtime-frontend protocol)
│   ├── channels/                 # Chat channel adapters (Feishu, Slack, Discord, Telegram, ...)
│   ├── sandbox/                  # bwrap sandbox manager
│   ├── skills/                   # Built-in skills (cron, paper-research, feishu-report, ...)
│   ├── session/                  # Session management (Manager, SQLite store)
│   ├── config/                   # Configuration loader and schema
│   ├── cli/                      # CLI commands (agent, gateway, trace, config)
│   ├── cron/                     # Cron scheduler service
│   ├── context/                  # Context fragments, thread store
│   ├── heartbeat/                # Heartbeat service
│   ├── plan/                     # Plan tracker and tool
│   ├── documents/                # Office document tools (docx, pptx, xlsx)
│   ├── observability/            # OpenTelemetry integration
│   ├── server/                   # Server assets and configuration
│   ├── templates/                # Templates
│   ├── tui/                      # Terminal UI (Textual-based)
│   └── utils/                    # Utility functions
├── apps/
│   └── desktop/                  # Electron frontend
│       ├── src/main/             # Main process (BridgeManager, IPC handlers)
│       ├── src/renderer/         # Renderer (React pages and components)
│       └── src/preload/          # Preload scripts (contextBridge API)
├── tests/                        # Test suite (~150+ test files)
│   ├── runtime/                  # Runtime unit and integration tests (~70+ files)
│   ├── bridge/                   # Bridge protocol and audit tests (~20+ files)
│   ├── execution/                # Sandbox, permissions, orchestration tests
│   ├── providers/                # LLM provider tests
│   ├── protocol/                 # Protocol commands/events/permissions tests
│   └── agent/tools/              # Tool-level tests
├── docs/                         # Documentation (MkDocs)
├── plan/                         # Implementation plans (not in VCS deliverables)
└── scripts/                      # Build and utility scripts
```

### Code Standards

- **Python**: Ruff for linting (line-length 100)
- **TypeScript**: ESLint for linting
- **Commit Messages**: Conventional Commits format

### Testing

```bash
# Python backend tests (~1800+ tests)
uv run pytest

# Skip sandbox/subprocess tests for quick feedback
uv run pytest -m "not sandbox and not subprocess"

# Frontend tests
cd apps/desktop
npm run test
```

#### E2E Tests

```bash
# Electron E2E (full desktop app + bridge + LLM)
cd apps/desktop
npm run build && npx playwright test --config=playwright.config.ts --project=electron
```

| Platform | E2E Coverage | Notes |
|---|---|---|
| **Linux** (Ubuntu CI) | Full suite ✓ | bwrap sandbox + all specs |
| **Windows** (WSL CI) | Full suite ✓ | WSL bwrap sandbox + all specs (needs `MIQI_RUN_SANDBOX_E2E=1`) |
| **macOS** (CI) | Non-sandbox only | bwrap not available; sandbox specs excluded via `--grep-invert` |

> **macOS known limitation**: The "restart recall" E2E test (`session-context-recall.spec.ts`) is skipped only when `process.env.CI && process.platform === 'darwin'` (local macOS runs still exercise the test). After a full app restart on macOS ARM64 CI runners, session history (chat messages) fails to render in `<main>` even though the sidebar session title loads correctly and the bridge reports `running / initialized`. This is likely a bridge IPC timing issue or APFS/SQLite WAL checkpoint race on cold start — needs native debugging. The non-restart session-switch recall test still validates #490 behavior on macOS.

---

## Documentation

- [Getting Started](docs/getting-started.md)
- [Architecture](docs/architecture.md)
- [Configuration](docs/configuration.md)
- [MCP Integration](docs/mcp-integration.md)
- [Developer Guide](docs/developer-guide.md)
- [Internal Alpha Smoke](docs/internal-alpha-smoke.md)
![alt text](interface.png)
---

## License

[MIT License](LICENSE)

---

## Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for details.
test
