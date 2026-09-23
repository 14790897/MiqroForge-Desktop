# KUN Runtime Migration — Design Document

> **Phase 0 deliverable.** Analysis, module mapping, risk assessment, and phased migration plan for replacing MiQi's native `AgentLoop` with a Python-based KUN runtime.

---

## 1. Executive Summary

MiQi's current `miqi.agent.loop.AgentLoop` follows a **message-bus model**:

```
InboundMessage → AgentLoop → OutboundMessage
```

KUN's `AgentLoop` follows a **desktop workbench model**:

```
Thread → Turn → TurnItem → RuntimeEvent → SSE
```

The goal is to build a Python KUN runtime (`miqi/kun_runtime/`) that drives the main execution path, while keeping MiQi's existing provider, tool, MCP, channel, and memory assets as adapter layers. The old `AgentLoop` is **not deleted** — it becomes a legacy fallback.

---

## 2. Architectural Comparison

### 2.1 State Model

| Dimension | KUN (TypeScript) | MiQi (Python) | Migration |
|---|---|---|---|
| **Top-level container** | `ThreadRecord` (id, title, workspace, mode, status, goal, todos, turns[]) | `Session` (key, messages[]) | New KUN `ThreadStore`; map `session_key → threadId` |
| **Execution unit** | `Turn` (id, status, prompt, model, items[], activeSkillIds, toolCatalogFingerprint) | N/A (messages are flat) | New `Turn` in `contracts.py` |
| **Content item** | `TurnItem` discriminated union (10 variants) | Raw `dict` messages (role + content) | New Pydantic `TurnItem` union in `contracts.py` |
| **Event** | `RuntimeEvent` discriminated union (25+ variants), per-thread seq | `InboundMessage`/`OutboundMessage` on `MessageBus` | New `RuntimeEvent` union + `EventBus` |
| **Transport** | SSE (`id: <seq>\nevent: <kind>\ndata: <json>`) | Channel push (`MessageBus.publish_outbound`) | New `sse.py` for desktop; channel adapter for legacy |

### 2.2 Execution Flow

| Stage | KUN | MiQi | Notes |
|---|---|---|---|
| 1. Input | `TurnService.startTurn()` → creates Turn + user item | `AgentLoop._process_message()` → builds messages dict | KUN is more structured |
| 2. Steering | `SteeringQueue.drain()` → injects steered text as user items | N/A (no mid-turn modification) | New capability for MiQi |
| 3. Context | `ImmutablePrefix` (system + few-shots, byte-stable for cache) | `ContextBuilder.build_system_prompt()` (dynamic, re-built each turn) | KUN uses immutable prefix for prompt-cache reuse |
| 4. History healing | `healLoadedHistoryItems()` → normalize, then `repairModelHistoryItems()` | None (messages loaded as-is) | New for MiQi |
| 5. Memory | `MemoryStore.retrieve()` → injects into context instructions | `MemoryStore.get_memory_context()` → injected into system prompt | Similar, different injection point |
| 6. Skills | `SkillRuntime.resolveTurn()` → active skill IDs + instructions | `SkillsLoader.get_always_skills()` → skill texts in system prompt | KUN skills are more structured |
| 7. Routing | `resolveAutoModelRoute()` → auto/fixed model selection | `SmartModelRouter.resolve()` → cheap/expensive model switch | Can wrap existing router |
| 8. Compaction | `ContextCompactor` (soft/hard/force thresholds, heuristic + LLM summary) | `ContextCompressor` (5-phase LLM algorithm) | Port KUN compactor; MiQi compressor as fallback |
| 9. Model call | `ModelClient.stream()` → async iterable of `ModelStreamChunk` | `LLMProvider.chat()` → single `LLMResponse` | Need streaming adapter |
| 10. Tool dispatch | `ToolHost.execute()` + `dispatchToolCalls()` (parallel batch, serial, storm guard) | `ToolRegistry.execute()` / `execute_concurrent()` | Wrap ToolRegistry in ToolHost |
| 11. Approval | `ApprovalGate.request()` → `pending → allowed/denied` | `command_approval.py` (CLI prompt in shell tool) | New generic gate |
| 12. User input | `UserInputGate` → `pending → submitted/cancelled` | N/A | New capability |
| 13. Finish | `TurnService.finishTurn()` → closes turn, records event | `_save_turn()` → appends to session JSONL | KUN has richer lifecycle |

---

## 3. KUN → MiQi Module Mapping

### 3.1 Core Contracts

| KUN Source | MiQi Target | Content |
|---|---|---|
| `contracts/items.ts` | `miqi/kun_runtime/contracts.py` | `TurnItem` union (10 variants via Pydantic discriminated union) |
| `contracts/events.ts` | `miqi/kun_runtime/contracts.py` | `RuntimeEvent` union (25+ variant events) |
| `contracts/threads.ts` | `miqi/kun_runtime/contracts.py` | `ThreadRecord`, `ThreadSummary`, `ThreadGoal`, `ThreadTodoList` |
| `contracts/turns.ts` | `miqi/kun_runtime/contracts.py` | `Turn`, `StartTurnRequest/Response`, `SteerTurnRequest`, `InterruptTurnRequest`, `CompactRequest/Response` |
| `contracts/policy.ts` | `miqi/kun_runtime/contracts.py` | `ApprovalPolicy`, `SandboxMode` |
| `contracts/usage.ts` | `miqi/kun_runtime/contracts.py` | `UsageSnapshot` |
| `contracts/capabilities.ts` | `miqi/kun_runtime/contracts.py` | `ModelCapabilityMetadata`, `ToolProviderKind` |

### 3.2 Services

| KUN Source | MiQi Target | Content |
|---|---|---|
| `services/turn-service.ts` | `miqi/kun_runtime/turn_service.py` | `startTurn`, `finishTurn`, `interruptTurn`, `steerTurn`, `applyItem`, `updateItem`, `compact`, `getAbortController` |
| `services/thread-service.ts` | `miqi/kun_runtime/thread_service.py` | `create`, `list`, `get`, `update`, `delete`, `fork` |
| `services/usage-service.ts` | `miqi/kun_runtime/usage.py` | `record`, `forThread`, `recordTokenEconomySavings`, `seedThread` |
| `services/runtime-event-recorder.ts` | `miqi/kun_runtime/event_recorder.py` | `record(event)` — writes event to bus + session store |

### 3.3 Loop Core

| KUN Source | MiQi Target | Content |
|---|---|---|
| `loop/agent-loop.ts` | `miqi/kun_runtime/loop.py` | Main `runTurn(threadId, turnId)` with full pipeline: drain steering → model_step → tool dispatch → loop control |
| `loop/context-compactor.ts` | `miqi/kun_runtime/compactor.py` | `planCompaction`, `compact`, `shouldCompact`, soft/hard/force thresholds |
| `loop/token-economy.ts` | `miqi/kun_runtime/token_economy.py` | `applyTokenEconomyToRequest`, `compactToolSpec`, `compactHistoryItem`, `compressProse` |
| `loop/tool-storm-breaker.ts` | `miqi/kun_runtime/tool_storm_breaker.py` | Turn-scoped repeat-loop guard, windowed identical-call detection |
| `loop/tool-call-repair.ts` | `miqi/kun_runtime/tool_call_repair.py` | `repairDispatchToolArguments`, wrapper flattening, JSON scavenging, oversized string truncation |
| `loop/history-healing.ts` | `miqi/kun_runtime/history_repair.py` | `healLoadedHistoryItems`, normalization, repair of orphan tool results |
| `loop/request-history-hygiene.ts` | `miqi/kun_runtime/history_hygiene.py` | `applyRequestHistoryHygiene`, line/byte/token budget trimming, signal-line preservation |
| `loop/auto-model-router.ts` | `miqi/kun_runtime/auto_model_router.py` | `resolveAutoModelRoute` with recent context, per-turn caching |
| `loop/model-context-profile.ts` | `miqi/kun_runtime/context_estimator.py` | `modelCapabilitiesForModel`, context thresholds, model profiles |
| `loop/model-request-estimator.ts` | `miqi/kun_runtime/context_estimator.py` | `estimateModelRequestInputTokens` |
| `loop/inflight-tracker.ts` | Inline in `cancellation.py` + `turn_service.py` | Track running model/tool operations |
| `loop/steering-queue.ts` | Inline in `turn_service.py` | Queue of user-steered text for mid-turn injection |

### 3.4 Ports / Adapters

| KUN Source | MiQi Target | Content |
|---|---|---|
| `ports/model-client.ts` | `miqi/kun_runtime/model_client.py` | `ModelClient` interface, `ModelStreamChunk`, `ModelRequest`, `ModelToolSpec` |
| `ports/tool-host.ts` | `miqi/kun_runtime/tool_host.py` | `ToolHost` interface wrapping MiQi `ToolRegistry` |
| `ports/thread-store.ts` | `miqi/kun_runtime/stores.py` | `FileThreadStore`, `FileSessionStore` |
| `ports/session-store.ts` | `miqi/kun_runtime/stores.py` | Append-only JSONL with `loadItems`, `appendItem`, `rewriteItems`, `updateItem`, `loadEventsSince` |
| `ports/approval-gate.ts` | `miqi/kun_runtime/approval_gate.py` | `ApprovalGate` interface |
| `ports/user-input-gate.ts` | `miqi/kun_runtime/user_input_gate.py` | `UserInputGate` interface |
| `ports/id-generator.ts` | Inline in `contracts.py` or simple util | UUID-based ID generation |
| `adapters/in-memory-event-bus.ts` | `miqi/kun_runtime/event_bus.py` | Per-thread seq, append, replay, subscribe, sinceSeq |
| `adapters/in-memory-approval-gate.ts` | `miqi/kun_runtime/approval_gate.py` | In-memory approval gate |
| `adapters/in-memory-user-input-gate.ts` | `miqi/kun_runtime/user_input_gate.py` | In-memory user input gate |
| `adapters/model/deepseek-compat-model-client.ts` | `miqi/kun_runtime/model_client.py` | Wrap `OpenAIProvider.chat()` with streaming |
| `adapters/tool/local-tool-host.ts` | `miqi/kun_runtime/tool_host.py` | Wrap `ToolRegistry` with KUN tool semantics |
| `server/runtime-factory.ts` | `miqi/kun_runtime/runtime.py` | Composition root (factory function) |
| `server/routes/index.ts` | `miqi/kun_runtime/router.py` | HTTP routes (Starlette) |
| `server/node-http-server.ts` | `miqi/kun_runtime/sse.py` + Starlette/Uvicorn | HTTP server + SSE transport |

### 3.5 Supporting Modules

| KUN Source | MiQi Target | Content |
|---|---|---|
| `cache/immutable-prefix.ts` | (defer — Phase 8+) | Byte-stable system prompt prefix for cache reuse |
| `cache/tool-catalog-fingerprint.ts` | `miqi/kun_runtime/loop.py` (inline) | Tool catalog hash for drift detection |
| `domain/item.ts` | `miqi/kun_runtime/contracts.py` (factory functions) | `makeUserItem`, `makeAssistantTextItem`, etc. |
| `domain/turn.ts` | `miqi/kun_runtime/contracts.py` (factory functions) | `createTurnRecord`, `appendTurnItem`, `finishTurn` |
| `domain/thread.ts` | `miqi/kun_runtime/contracts.py` (factory functions) | `touchThread` |
| `domain/model-history-repair.ts` | `miqi/kun_runtime/history_repair.py` | `repairModelHistoryItems` |
| `skills/skill-runtime.ts` | (defer — Phase 8+; reuse MiQi `SkillsLoader`) | Skill resolution and instruction injection |
| `memory/memory-store.ts` | (reuse MiQi `MemoryStore`) | Long-term memory retrieval |
| `attachments/attachment-store.ts` | (defer — Phase 8+) | File image/drag-drop attachments |

---

## 4. MiQi Existing Capability → KUN Adapter Mapping

| MiQi Capability | KUN Equivalent | Migration Approach |
|---|---|---|
| `LLMProvider.chat()` (base.py) | `ModelClient.stream()` | Wrap provider in `MiQiModelClient`; Phase 5: pseudo-streaming first, real streaming later |
| `ToolRegistry` (registry.py) | `ToolHost` | Wrap in `MiQiToolHost`; convert ToolRegistry definitions to `ModelToolSpec`, execute to `ToolHostResult` |
| `SessionManager` (manager.py) | `ThreadStore` + `SessionStore` | New stores; `migration_adapter.py` maps `session_key → threadId` |
| `MessageBus` (queue.py) | `EventBus` | New event bus; channel adapter consumes RuntimeEvents → OutboundMessage |
| `ContextBuilder` (context.py) | ImmutablePrefix + ContextInstructions | KUN system prompt via immutable prefix; MiQi bootstrap files can be prefix source |
| `MemoryStore` (store.py) | `MemoryStore` (kun_runtime) | Wrap MiQi MemoryStore; inject via KUN context instructions |
| `SkillsLoader` (skills.py) | `SkillRuntime` | Wrap MiQi SkillsLoader in initial phase; full SkillRuntime later |
| `ContextCompressor` (context_compressor.py) | `ContextCompactor` | Port KUN compactor; MiQi compressor as model-summary backend for compaction |
| `SmartModelRouter` (smart_routing.py) | `auto_model_router.py` | Wrap MiQi router; align with KUN auto-route semantics |
| `IterationBudget` (iteration_budget.py) | `ToolStormBreaker` + budget gate | MiQi budget provides max_iterations; KUN adds turn-scoped repeat guard |
| `command_approval.py` | `ApprovalGate` | Retain dangerous-command detection; replace CLI prompt with gate API |
| `SubagentManager` (subagent.py) | `DelegationRuntime` | Defer; wrap SubagentManager as delegation adapter |
| MCP integration (tools/mcp.py) | MCP tool providers | Keep MiQi MCP connection; adapt to KUN ToolHost provider structure |
| Channels (channels/*.py) | Channel adapters | InboundMessage → StartTurnRequest; RuntimeEvent → OutboundMessage |
| CLI (cli/agent_cmd.py) | `runtime.py` composition | Use KUNRuntimeRunner instead of AgentLoop; legacy fallback via config |

---

## 5. Data Model Mapping

### 5.1 KUN TurnItem → Pydantic

```python
# KUN TurnItem kinds → Python discriminated union fields
class UserMessageItem(BaseModel):
    kind: Literal["user_message"]
    id: str; turnId: str; threadId: str
    role: Literal["user"]; status: ItemStatus
    createdAt: str; finishedAt: Optional[str]
    text: str; displayText: Optional[str]
    attachmentIds: list[str] = []

class AssistantTextItem(BaseModel):
    kind: Literal["assistant_text"]
    # ... base fields
    text: str

class AssistantReasoningItem(BaseModel):
    kind: Literal["assistant_reasoning"]
    text: str

class ToolCallItem(BaseModel):
    kind: Literal["tool_call"]
    toolName: str; callId: str
    toolKind: Literal["tool_call", "command_execution", "file_change"]
    arguments: dict[str, Any]
    summary: Optional[str]

class ToolResultItem(BaseModel):
    kind: Literal["tool_result"]
    toolName: str; callId: str
    toolKind: Literal["tool_call", "command_execution", "file_change"]
    output: Any; isError: bool = False

class ApprovalItem(BaseModel):
    kind: Literal["approval"]
    approvalId: str; toolName: str; summary: str
    status: Literal["pending", "allowed", "denied", "expired"]

class UserInputItem(BaseModel):
    kind: Literal["user_input"]
    inputId: str; prompt: str; questions: list[UserInputQuestion]
    status: Literal["pending", "submitted", "cancelled"]

class CompactionItem(BaseModel):
    kind: Literal["compaction"]
    summary: str; replacedTokens: int
    pinnedConstraints: list[str]
    sourceDigest: Optional[str]
    digestMarker: Optional[str]
    sourceItemIds: Optional[list[str]]

class ErrorItem(BaseModel):
    kind: Literal["error"]
    message: str; code: Optional[str]

TurnItem = Annotated[
    Union[
        UserMessageItem, AssistantTextItem, AssistantReasoningItem,
        ToolCallItem, ToolResultItem, ApprovalItem,
        UserInputItem, CompactionItem, ErrorItem
    ],
    Field(discriminator="kind")
]
```

### 5.2 KUN RuntimeEvent → Pydantic

All events share: `seq: int`, `timestamp: str`, `threadId: str`, `turnId: Optional[str]`, `itemId: Optional[str]`. Discriminated union with 25+ kinds covering: thread lifecycle, turn lifecycle, item lifecycle, deltas (text/reasoning), tool events, approval events, user input events, compaction events, pipeline stage, usage, error, heartbeat.

### 5.3 MiQi Session → KUN Thread

```python
# Migration mapping
session_key = f"{channel}:{chat_id}"
thread_id = session_key_to_thread_id(session_key)  # or generate new

# MiQi Session.messages → KUN TurnItems
# user message → UserMessageItem
# assistant message → AssistantTextItem (or ReasoningItem if reasoning_content)
# tool_call in assistant → ToolCallItem
# tool result → ToolResultItem
```

---

## 6. Risk Analysis

| # | Risk | Probability | Impact | Mitigation |
|---|---|---|---|---|
| R1 | CLI/gateway breakage during migration | High | High | Config-based runtime switch (`kun` vs `legacy`); keep old loop |
| R2 | Provider doesn't support streaming | Medium | Medium | Pseudo-streaming in Phase 5; real streaming for OpenAI/DeepSeek in Phase 5b |
| R3 | KUN thread/item store conflicts with MiQi session store | Medium | Medium | Coexist initially; `session_key → threadId` mapping in `migration_adapter.py` |
| R4 | Approval semantics mismatch (CLI prompt → gate API) | Medium | Medium | Keep dangerous-command detection; replace blocking prompt with gate event |
| R5 | Tests touch real `~/.forge` | Low | High | ALL tests use `tmp_path` or explicit temp config directory |
| R6 | Two runtimes increase codebase complexity | Medium | Medium | Clear sunset timeline for legacy loop; feature flag gates new code |
| R7 | Context compaction behavior differs | Medium | Low | Port KUN compactor first; MiQi compressor as model-summary backend |
| R8 | MCP tool semantics differ (KUN providers vs MiQi direct) | Medium | Medium | Wrap MiQi MCP tools as KUN ToolProviders |
| R9 | Historical session data compatibility | Low | Low | Read-only legacy sessions; new data in KUN format; migration script later |

---

## 7. Phase-by-Phase Migration Plan

### Phase 0 — Analysis & Design (THIS PHASE)

**Output:** This document (`docs/kun-runtime-migration.md`).

**Artifacts:**
- Module mapping table (Section 3)
- Capability adapter mapping (Section 4)
- Data model mappings (Section 5)
- Risk register (Section 6)

**No code changes.**

---

### Phase 1 — Contracts & Event Model

**Goal:** Establish KUN protocol and data models in Python.

**Files to create:**
- `miqi/kun_runtime/__init__.py`
- `miqi/kun_runtime/contracts.py` — Pydantic v2 models for ThreadRecord, Turn, TurnItem union, RuntimeEvent union, Request/Response types

**Tests:** `tests/kun_runtime/test_contracts.py`

**Acceptance:** `uv run pytest tests/kun_runtime/test_contracts.py` — all contracts round-trip JSON.

---

### Phase 2 — EventBus, SSE, RuntimeEventRecorder

**Files to create:**
- `miqi/kun_runtime/event_bus.py` — per-thread seq, append, history replay, subscribe, sinceSeq
- `miqi/kun_runtime/event_recorder.py` — record(event) → event bus + session store
- `miqi/kun_runtime/sse.py` — SSE encoding: `id: <seq>\nevent: <kind>\ndata: <json>`

**Tests:** `tests/kun_runtime/test_event_bus.py`

---

### Phase 3 — ThreadStore, SessionItemStore, UsageStore

**Files to create:**
- `miqi/kun_runtime/stores.py` — FileThreadStore, FileSessionStore (append-only JSONL)
- `miqi/kun_runtime/usage.py` — UsageService (accumulate per-thread usage)

**Tests:** `tests/kun_runtime/test_stores.py`

---

### Phase 4 — TurnService & ThreadService

**Files to create:**
- `miqi/kun_runtime/thread_service.py` — create/list/get/update/delete/fork thread
- `miqi/kun_runtime/turn_service.py` — startTurn, finishTurn, interruptTurn, steerTurn, applyItem, updateItem, compact
- `miqi/kun_runtime/cancellation.py` — Python cancellation token
- `miqi/kun_runtime/migration_adapter.py` — session_key → threadId mapping

**Tests:** `tests/kun_runtime/test_turn_service.py`

---

### Phase 5 — ModelClient Adapter

**Files to create:**
- `miqi/kun_runtime/model_client.py` — MiQiModelClient wrapping LLMProvider.chat()
  - Phase 5a: pseudo-streaming (one chunk per response)
  - Phase 5b: real streaming for OpenAI/DeepSeek-compatible providers

**Tests:** `tests/kun_runtime/test_model_client.py`

---

### Phase 6 — ToolHost Adapter

**Files to create:**
- `miqi/kun_runtime/tool_host.py` — MiQiToolHost wrapping ToolRegistry
  - Schema conversion: ToolRegistry definitions → ModelToolSpec
  - Execution: KUN ToolCallLike → ToolRegistry.execute()
  - Concurrency: reuse ToolRegistry.should_parallelize()

**Tests:** `tests/kun_runtime/test_tool_host.py`

---

### Phase 7 — ApprovalGate & UserInputGate

**Files to create:**
- `miqi/kun_runtime/approval_gate.py` — request/resolve/timeout/cancel
- `miqi/kun_runtime/user_input_gate.py` — request/resolve/cancel

**Tests:** `tests/kun_runtime/test_agent_loop_gates.py`

---

### Phase 8 — AgentLoop Core

**Files to create:**
- `miqi/kun_runtime/loop.py` — Main KUN AgentLoop.runTurn()
- `miqi/kun_runtime/history_repair.py` — healLoadedHistoryItems + repairModelHistoryItems
- `miqi/kun_runtime/history_hygiene.py` — applyRequestHistoryHygiene
- `miqi/kun_runtime/token_economy.py` — applyTokenEconomyToRequest
- `miqi/kun_runtime/tool_call_repair.py` — repairDispatchToolArguments
- `miqi/kun_runtime/tool_storm_breaker.py` — ToolStormBreaker
- `miqi/kun_runtime/compactor.py` — ContextCompactor (port from KUN)
- `miqi/kun_runtime/context_estimator.py` — Token estimation, model profiles
- `miqi/kun_runtime/auto_model_router.py` — Auto model routing

**Tests:**
- `tests/kun_runtime/test_agent_loop_basic.py`
- `tests/kun_runtime/test_agent_loop_tools.py`
- `tests/kun_runtime/test_context_compaction.py`

---

### Phase 9 — HTTP + SSE Runtime API

**Files to create:**
- `miqi/kun_runtime/router.py` — Starlette routes
- `miqi/kun_runtime/runtime.py` — Composition root (factory)
- `miqi/kun_runtime/auth.py` — Bearer token auth

**New dependencies:** `starlette>=0.48.0`, `uvicorn>=0.35.0`

**Tests:** `tests/kun_runtime/test_http_runtime.py`

---

### Phase 10 — CLI/Gateway Integration

**Files to modify:**
- `miqi/cli/agent_cmd.py` — use KUNRuntimeRunner; config switch
- `miqi/cli/gateway_cmd.py` — channel messages → KUN turns
- `miqi/kun_runtime/migration_adapter.py` — InboundMessage → StartTurnRequest, RuntimeEvent → OutboundMessage

**Config:** `agents.defaults.runtime = "kun" | "legacy"` (default: `kun`)

**Tests:** `tests/kun_runtime/test_legacy_loop_replacement.py`

---

### Phase 11 — Legacy Loop Retirement

**Conditions:** All KUN runtime tests stable, CLI/gateway paths verified, MCP/filesystem/shell/message/spawn/cron tools working, error paths observable.

**Actions:**
1. Mark `miqi.agent.loop.AgentLoop` as deprecated
2. Remove old loop from main import paths
3. Move to legacy package or delete (after confirming zero imports)

---

## 8. Testing Strategy

### Isolation Rules

- **ALL tests MUST use `tmp_path`** (pytest fixture) or explicit temporary config directories
- **No test may read/write `~/.forge`** or any real user directory
- Tests that need workspace/config must create them under `tmp_path`
- Use pytest `monkeypatch` to override any path-resolving functions

### Test Categories

| Category | Scope | Framework |
|---|---|---|
| Contract tests | Round-trip JSON serialization, discriminator validation | pytest + Pydantic |
| Unit tests | Individual services, stores, gates | pytest |
| Integration tests | End-to-end turn lifecycle with fake provider | pytest + asyncio |
| HTTP tests | Route handling, SSE encoding/replay | httpx AsyncClient or Starlette TestClient |
| Migration tests | session_key → threadId mapping, legacy fallback | pytest |
| Channel adapter tests | InboundMessage → StartTurnRequest, RuntimeEvent → OutboundMessage | pytest |

### Fake/Test Doubles

- `FakeModelClient` — returns configurable text chunks and tool calls without real API calls
- `FakeToolHost` — returns configurable tool results
- `InMemoryApprovalGate`, `InMemoryUserInputGate` — for testing gate flow
- `FakeEventBus` — captures events for assertion

---

## 9. Recommended PR Split

| PR | Scope | Files | Dependencies |
|---|---|---|---|
| PR1 | Contracts + Event model | `contracts.py`, tests | None |
| PR2 | EventBus + SSE + EventRecorder | `event_bus.py`, `sse.py`, `event_recorder.py`, tests | PR1 |
| PR3 | Stores + Usage | `stores.py`, `usage.py`, tests | PR1 |
| PR4 | Thread/Turn services + Runtime composition | `thread_service.py`, `turn_service.py`, `cancellation.py`, `runtime.py`, `migration_adapter.py`, tests | PR2, PR3 |
| PR5 | Model client + Tool host adapters | `model_client.py`, `tool_host.py`, tests | PR1, PR4 |
| PR6 | Agent loop core + Tool loop | `loop.py`, `history_repair.py`, `history_hygiene.py`, `token_economy.py`, `tool_call_repair.py`, `tool_storm_breaker.py`, tests | PR4, PR5 |
| PR7 | Gates + Interrupt/Steer | `approval_gate.py`, `user_input_gate.py`, tests | PR4 |
| PR8 | Compaction + Context estimation | `compactor.py`, `context_estimator.py`, `auto_model_router.py`, tests | PR6 |
| PR9 | HTTP/SSE Runtime API | `router.py`, `auth.py`, `runtime.py` (extend), tests | PR4, PR6 |
| PR10 | CLI/Gateway integration | `agent_cmd.py`, `gateway_cmd.py`, `migration_adapter.py` (extend), tests | PR9 |
| PR11 | Legacy deprecation | Mark deprecated, config switch, docs | PR10 |

---

## 10. Key Design Decisions

1. **Pydantic v2** over dataclasses: provides discriminated unions, built-in JSON schema, and serialization matching KUN's Zod schemas.

2. **File-first storage** (JSONL) before SQLite: matches KUN's append-only model; lower risk; migrate to SQLite later if needed.

3. **Pseudo-streaming before real streaming**: `LLMProvider.chat()` returns a complete `LLMResponse` — adapt to emit single `assistant_text_delta` + `completed` chunks. Real streaming for OpenAI-compatible providers comes in Phase 5b.

4. **Starlette + Uvicorn** for HTTP: lightweight, async-native, complements MiQi's existing httpx/websockets dependencies.

5. **Config-based runtime switch**: `agents.defaults.runtime = "kun" | "legacy"` — allows gradual rollout and instant rollback.

6. **No TypeScript mechanical translation**: behavior and protocol equivalence, idiomatic Python.

7. **Keep original `AgentLoop` intact**: never directly modify or delete the old loop during migration phases.

---

## 11. File Layout

```
miqi/kun_runtime/
  __init__.py              # Package init
  contracts.py             # Pydantic models for Thread, Turn, TurnItem, RuntimeEvent, requests/responses
  event_bus.py             # In-memory per-thread event bus with seq
  event_recorder.py        # RuntimeEventRecorder
  sse.py                   # SSE encoder
  stores.py                # FileThreadStore, FileSessionStore
  usage.py                 # UsageService
  thread_service.py        # Thread CRUD
  turn_service.py          # Turn lifecycle
  cancellation.py          # AbortController equivalent
  runtime.py               # Composition root (factory)
  router.py                # HTTP route handlers (Starlette)
  auth.py                  # Bearer token auth
  model_client.py          # MiQiModelClient wrapping LLMProvider
  tool_host.py             # MiQiToolHost wrapping ToolRegistry
  approval_gate.py         # Approval gate
  user_input_gate.py       # User input gate
  loop.py                  # KUN AgentLoop port
  compactor.py             # ContextCompactor
  context_estimator.py     # Token estimation, model profiles
  history_repair.py        # History healing
  history_hygiene.py       # Request history hygiene
  token_economy.py         # Token economy
  tool_call_repair.py      # Tool argument repair
  tool_storm_breaker.py    # Repeat-loop guard
  auto_model_router.py     # Auto model routing
  migration_adapter.py     # Legacy MiQi ↔ KUN adapter

tests/kun_runtime/
  test_contracts.py
  test_event_bus.py
  test_stores.py
  test_turn_service.py
  test_model_client.py
  test_tool_host.py
  test_agent_loop_basic.py
  test_agent_loop_tools.py
  test_agent_loop_gates.py
  test_context_compaction.py
  test_history_repair.py
  test_http_runtime.py
  test_legacy_loop_replacement.py
```

---

## 12. Next Steps

After Phase 0 approval:
1. **Phase 1:** Create `miqi/kun_runtime/contracts.py` with Pydantic v2 models
2. **Phase 1 tests:** `tests/kun_runtime/test_contracts.py`
3. No other module changes until contracts are stable

**Waiting for user confirmation before proceeding to Phase 1.**
