"""Subagent manager for background task execution."""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from miqi.agent.tools.filesystem import (
    EditFileTool,
    ListDirTool,
    ReadFileTool,
    WriteFileTool,
)
from miqi.agent.tools.papers import PaperDownloadTool, PaperGetTool, PaperSearchTool
from miqi.agent.tools.registry import ToolRegistry
from miqi.agent.tools.shell import ExecTool
from miqi.agent.tools.web import WebFetchTool, WebSearchTool
from miqi.bus.events import InboundMessage
from miqi.bus.queue import MessageBus
from miqi.config.schema import ExecToolConfig, PapersToolConfig, WebToolsConfig
from miqi.providers.base import LLMProvider


class SubagentManager:
    """
    Manages background subagent execution.

    Subagents are lightweight agent instances that run in the background
    to handle specific tasks. They share the same LLM provider but have
    isolated context and a focused system prompt.
    """

    def __init__(
        self,
        provider: LLMProvider,
        workspace: Path,
        bus: MessageBus,
        model: str | None = None,
        web_config: WebToolsConfig | None = None,
        paper_config: PapersToolConfig | None = None,
        temperature: float = 0.1,
        max_tokens: int = 8192,
        brave_api_key: str | None = None,
        exec_config: ExecToolConfig | None = None,
        restrict_to_workspace: bool = False,
        max_concurrent: int = 3,
        max_tool_result_chars: int = 8000,
        context_limit_chars: int = 200000,
        executor: Any | None = None,
        result_callback: Callable[..., None] | None = None,
    ):
        self.provider = provider
        self.workspace = workspace
        self.bus = bus
        self.model = model or provider.get_default_model()
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.brave_api_key = brave_api_key
        self.web_config = web_config or WebToolsConfig()
        self.paper_config = paper_config or PapersToolConfig()
        self.exec_config = exec_config or ExecToolConfig()
        self.restrict_to_workspace = restrict_to_workspace
        self.max_concurrent = max_concurrent
        self.max_tool_result_chars = max_tool_result_chars
        self.context_limit_chars = context_limit_chars
        self._executor = executor  # BackgroundExecutor for Desktop mode
        self._result_callback = result_callback  # Desktop mode: send stdout event
        self._running_tasks: dict[str, asyncio.Task[None] | Future] = {}
        self._abort_events: dict[str, threading.Event] = {}

    async def spawn(
        self,
        task: str,
        label: str | None = None,
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
    ) -> str:
        """
        Spawn a subagent to execute a task in the background.

        Args:
            task: The task description for the subagent.
            label: Optional human-readable label for the task.
            origin_channel: The channel to announce results to.
            origin_chat_id: The chat ID to announce results to.

        Returns:
            Status message indicating the subagent was started.
        """
        # Concurrency limit
        if len(self._running_tasks) >= self.max_concurrent:
            return (
                f"Cannot spawn: {self.max_concurrent} subagents already running. "
                "Wait for one to finish before starting another."
            )

        task_id = str(uuid.uuid4())[:8]
        display_label = label or task[:30] + ("..." if len(task) > 30 else "")

        origin = {
            "channel": origin_channel,
            "chat_id": origin_chat_id,
        }

        # Create background task — use executor (Desktop) or create_task (Gateway)
        if self._executor is not None and self._executor.is_running:
            # Desktop mode: submit to persistent background event loop
            future = self._executor.submit(
                self._run_subagent(task_id, task, display_label, origin)
            )
            self._running_tasks[task_id] = future
            future.add_done_callback(lambda _: self._running_tasks.pop(task_id, None))
        else:
            # Gateway mode: create task on current (persistent) event loop
            bg_task = asyncio.create_task(
                self._run_subagent(task_id, task, display_label, origin)
            )
            self._running_tasks[task_id] = bg_task
            bg_task.add_done_callback(lambda _: self._running_tasks.pop(task_id, None))

        logger.info("Spawned subagent [{}]: {}", task_id, display_label)
        return f"Subagent [{display_label}] started (id: {task_id}). I'll notify you when it completes."

    async def _run_subagent(
        self,
        task_id: str,
        task: str,
        label: str,
        origin: dict[str, str],
    ) -> None:
        """Execute the subagent task and announce the result."""
        logger.info("Subagent [{}] starting task: {}", task_id, label)

        # Create abort event for this task (threading.Event for cross-thread safety)
        abort_event = threading.Event()
        self._abort_events[task_id] = abort_event

        try:
            # Build subagent tools (no message tool, no spawn tool)
            tools = ToolRegistry()
            allowed_dir = self.workspace if self.restrict_to_workspace else None
            tools.register(ReadFileTool(workspace=self.workspace, allowed_dir=allowed_dir))
            tools.register(WriteFileTool(workspace=self.workspace, allowed_dir=allowed_dir))
            tools.register(EditFileTool(workspace=self.workspace, allowed_dir=allowed_dir))
            tools.register(ListDirTool(workspace=self.workspace, allowed_dir=allowed_dir))
            tools.register(ExecTool(
                working_dir=str(self.workspace),
                timeout=self.exec_config.timeout,
                max_timeout=self.exec_config.max_timeout,
                idle_timeout=self.exec_config.idle_timeout,
                heartbeat_interval=self.exec_config.heartbeat_interval,
                kill_grace_seconds=self.exec_config.kill_grace_seconds,
                restrict_to_workspace=self.restrict_to_workspace,
                env_passthrough=list(self.exec_config.env_passthrough),
            ))
            tools.register(WebSearchTool(
                provider=self.web_config.search.provider,
                api_key=self.web_config.search.api_key or None,
                tavily_api_key=self.web_config.search.tavily_api_key or None,
                brave_api_key=self.web_config.search.brave_api_key or None,
                max_results=self.web_config.search.max_results,
            ))
            tools.register(WebFetchTool(
                provider=self.web_config.fetch.provider,
                ollama_api_key=self.web_config.fetch.ollama_api_key or None,
                ollama_api_base=self.web_config.fetch.ollama_api_base,
            ))
            tools.register(PaperSearchTool(
                provider=self.paper_config.provider,
                semantic_scholar_api_key=self.paper_config.semantic_scholar_api_key or None,
                timeout_seconds=self.paper_config.timeout_seconds,
                default_limit=self.paper_config.default_limit,
                max_limit=self.paper_config.max_limit,
            ))
            tools.register(PaperGetTool(
                provider=self.paper_config.provider,
                semantic_scholar_api_key=self.paper_config.semantic_scholar_api_key or None,
                timeout_seconds=self.paper_config.timeout_seconds,
            ))
            tools.register(PaperDownloadTool(
                workspace=self.workspace,
                provider=self.paper_config.provider,
                semantic_scholar_api_key=self.paper_config.semantic_scholar_api_key or None,
                timeout_seconds=self.paper_config.timeout_seconds,
            ))

            # Build messages with subagent-specific prompt
            system_prompt = self._build_subagent_prompt(task)
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": task},
            ]

            # Run agent loop (limited iterations)
            max_iterations = 15
            iteration = 0
            final_result: str | None = None

            while iteration < max_iterations:
                if abort_event.is_set():
                    final_result = "Task was cancelled."
                    break
                iteration += 1

                # Context size management: trim if too large
                if self.context_limit_chars > 0:
                    est = self._estimate_chars(messages)
                    if est > self.context_limit_chars:
                        logger.warning(
                            "Subagent [{}] context too large (~{} chars, limit {}); trimming",
                            task_id, est, self.context_limit_chars,
                        )
                        messages = self._trim_context(messages, self.context_limit_chars, est)

                response = await self.provider.chat(
                    messages=messages,
                    tools=tools.get_definitions(),
                    model=self.model,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                )

                if response.has_tool_calls:
                    # Add assistant message with tool calls
                    tool_call_dicts = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                            },
                        }
                        for tc in response.tool_calls
                    ]
                    messages.append({
                        "role": "assistant",
                        "content": response.content or "",
                        "tool_calls": tool_call_dicts,
                    })

                    # Execute tools
                    for tool_call in response.tool_calls:
                        args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                        logger.debug("Subagent [{}] executing: {} with arguments: {}", task_id, tool_call.name, args_str)
                        try:
                            result = await tools.execute(tool_call.name, tool_call.arguments)
                        except Exception as exc:
                            result = f"[Tool Error] {tool_call.name} failed: {type(exc).__name__}: {exc}"
                            logger.warning("Subagent [{}] tool {} failed: {}", task_id, tool_call.name, exc)
                        # Ensure string + truncate large results
                        if not isinstance(result, str):
                            result = str(result)
                        if (
                            self.max_tool_result_chars > 0
                            and len(result) > self.max_tool_result_chars
                        ):
                            result = (
                                result[: self.max_tool_result_chars]
                                + f"\n\n结果已被截断，内容不完整（原始 {len(result)} 字符）。"
                                "禁止根据截断内容推断、补全、恢复或声称任务已完成；"
                                "如需完整文件请重新调用下载/文件系统工具获取。"
                            )
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": tool_call.name,
                            "content": result,
                        })
                else:
                    final_result = response.content
                    break

            if final_result is None:
                final_result = "Task completed but no final response was generated."

            logger.info("Subagent [{}] completed successfully", task_id)
            await self._announce_result(task_id, label, task, final_result, origin, "ok")

        except Exception as e:
            error_msg = f"Error: {str(e)}"
            logger.error("Subagent [{}] failed: {}", task_id, e)
            await self._announce_result(task_id, label, task, error_msg, origin, "error")
        finally:
            self._abort_events.pop(task_id, None)

    async def _announce_result(
        self,
        task_id: str,
        label: str,
        task: str,
        result: str,
        origin: dict[str, str],
        status: str,
    ) -> None:
        """Announce the subagent result."""
        status_text = "completed successfully" if status == "ok" else "failed"
        logger.info("Subagent [{}] _announce_result: status={} _result_callback={}", task_id, status, self._result_callback)

        if self._result_callback is not None:
            # Desktop mode: send result via stdout event callback
            try:
                self._result_callback(
                    task_id, label, task, result, status,
                    origin.get("channel", "cli"), origin.get("chat_id", "direct"),
                )
            except Exception as exc:
                logger.warning("Subagent [{}] result_callback failed: {}", task_id, exc)
            return

        # Gateway mode: inject as system message to trigger main agent
        announce_content = f"""[Subagent '{label}' {status_text}]

Task: {task}

Result:
{result}

Summarize this naturally for the user. Keep it brief (1-2 sentences). Do not mention technical details like "subagent" or task IDs."""

        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id=f"{origin['channel']}:{origin['chat_id']}",
            content=announce_content,
        )

        await self.bus.publish_inbound(msg)
        logger.debug("Subagent [{}] announced result to {}:{}", task_id, origin['channel'], origin['chat_id'])

    @staticmethod
    def _estimate_chars(messages: list[dict]) -> int:
        """Rough character count of all message content."""
        total = 0
        for m in messages:
            c = m.get("content")
            if isinstance(c, str):
                total += len(c)
        return total

    @staticmethod
    def _trim_context(
        messages: list[dict], limit_chars: int, current_chars: int
    ) -> list[dict]:
        """Drop oldest assistant+tool pairs until under limit.

        Always keeps the system prompt (index 0) and the last user message.
        """
        if limit_chars <= 0 or current_chars <= limit_chars:
            return messages

        work = list(messages)
        while current_chars > limit_chars and len(work) > 2:
            # Find the first assistant message after system prompt
            cut_start = None
            for i in range(1, len(work) - 1):
                if work[i].get("role") == "assistant":
                    cut_start = i
                    break
            if cut_start is None:
                break

            # Collect consecutive assistant + tool messages to drop together
            cut_end = cut_start + 1
            while cut_end < len(work) - 1 and work[cut_end].get("role") == "tool":
                cut_end += 1

            removed = work[cut_start:cut_end]
            removed_chars = sum(
                len(m.get("content", "")) if isinstance(m.get("content"), str) else 0
                for m in removed
            )
            work = work[:cut_start] + work[cut_end:]
            current_chars -= removed_chars

        return work

    def _build_subagent_prompt(self, task: str) -> str:
        """Build a focused system prompt for the subagent."""
        import time as _time
        from datetime import datetime
        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        tz = _time.strftime("%Z") or "UTC"

        return f"""# Subagent

## Current Time
{now} ({tz})

You are a subagent spawned by the main agent to complete a specific task.

## Rules
1. Stay focused - complete only the assigned task, nothing else
2. Your final response will be reported back to the main agent
3. Do not initiate conversations or take on side tasks
4. Be concise but informative in your findings

## What You Can Do
- Read and write files in the workspace
- Execute shell commands
- Search the web and fetch web pages
- Complete the task thoroughly

## What You Cannot Do
- Send messages directly to users (no message tool available)
- Spawn other subagents
- Access the main agent's conversation history

## Workspace
Your workspace is at: {self.workspace}
Skills are available at: {self.workspace}/skills/ (read SKILL.md files as needed)

When you have completed the task, provide a clear summary of your findings or actions."""

    def cancel_task(self, task_id: str) -> bool:
        """Cancel a running subagent by task_id."""
        event = self._abort_events.get(task_id)
        if event is not None:
            event.set()
            logger.info("Subagent [{}] cancel requested", task_id)
            return True
        return False

    def cancel_all(self) -> int:
        """Cancel all running subagents. Returns count cancelled."""
        count = 0
        for task_id, event in self._abort_events.items():
            event.set()
            count += 1
        if count:
            logger.info("Cancelled {} running subagent(s)", count)
        return count

    def get_running_count(self) -> int:
        """Return the number of currently running subagents."""
        return len(self._running_tasks)
