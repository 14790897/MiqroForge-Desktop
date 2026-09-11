"""Task trace CLI commands."""

from __future__ import annotations

import base64
import json
from dataclasses import asdict
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from miqi.agent.trace.model import TaskStep, TaskTrace
from miqi.agent.trace.store import TraceStore

trace_app = typer.Typer(help="Task tracing (git-like workflow history).")
console = Console()


def _new_store() -> TraceStore:
    from miqi.config.loader import load_config

    config = load_config()
    cfg = config.agents.self_improvement
    return TraceStore(
        workspace=config.workspace_path,
        enabled=cfg.trace_enabled,
        embedding_model=cfg.embedding_model,
    )


def _short(text: str, limit: int = 80) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _trace_to_dict(trace: TaskTrace) -> dict:
    return {
        "trace_hash": trace.trace_hash,
        "parent_hash": trace.parent_hash,
        "session_id": trace.session_id,
        "task_name": trace.task_name,
        "goal": trace.goal,
        "tool_calls": [asdict(step) for step in trace.tool_calls],
        "outcome": trace.outcome,
        "outcome_notes": trace.outcome_notes,
        "embedding": (
            base64.b64encode(trace.embedding).decode("ascii") if trace.embedding else None
        ),
        "created_at": trace.created_at,
        "ended_at": trace.ended_at,
        "metadata": trace.metadata,
    }


def _trace_from_dict(data: dict) -> TaskTrace:
    embedding_raw = data.get("embedding")
    embedding = base64.b64decode(embedding_raw) if embedding_raw else None
    return TaskTrace(
        trace_hash=data["trace_hash"],
        parent_hash=data.get("parent_hash"),
        session_id=data.get("session_id", "imported"),
        task_name=data.get("task_name", "imported"),
        goal=data.get("goal", ""),
        tool_calls=[
            TaskStep(
                tool_name=str(step.get("tool_name", "")),
                args_summary=str(step.get("args_summary", "")),
                result_summary=str(step.get("result_summary", "")),
                timestamp=float(step.get("timestamp", 0.0) or 0.0),
            )
            for step in data.get("tool_calls", [])
            if isinstance(step, dict)
        ],
        outcome=data.get("outcome", "success"),
        outcome_notes=data.get("outcome_notes", ""),
        embedding=embedding,
        created_at=float(data.get("created_at", 0.0) or 0.0),
        ended_at=(
            float(data["ended_at"])
            if data.get("ended_at") is not None
            else None
        ),
        metadata=data.get("metadata", {}) if isinstance(data.get("metadata"), dict) else {},
    )


@trace_app.command("log")
def trace_log(
    n: int = typer.Option(20, "--n", "-n", help="Maximum traces to show"),
    outcome: str | None = typer.Option(None, "--outcome", help="Filter by outcome"),
    session: str | None = typer.Option(None, "--session", help="Filter by session id"),
):
    store = _new_store()
    traces = store.list_recent(n=max(n * 5, n), outcome=outcome)
    if session:
        traces = [trace for trace in traces if trace.session_id == session]
    traces = traces[:n]
    if not traces:
        console.print("[dim]No task traces found[/dim]")
        return

    table = Table(title="Task Traces")
    table.add_column("Hash", style="cyan")
    table.add_column("Task")
    table.add_column("Outcome")
    table.add_column("Session")
    table.add_column("Goal")

    for trace in traces:
        table.add_row(
            trace.trace_hash[:12],
            trace.task_name,
            trace.outcome,
            trace.session_id,
            _short(trace.goal),
        )
    console.print(table)


@trace_app.command("show")
def trace_show(trace_hash: str = typer.Argument(..., help="Trace hash")):
    store = _new_store()
    trace = store.get_trace(trace_hash)
    if trace is None:
        console.print(f"[red]Trace not found:[/red] {trace_hash}")
        raise typer.Exit(1)

    console.print_json(json.dumps(_trace_to_dict(trace), ensure_ascii=False))


@trace_app.command("search")
def trace_search(
    query: str = typer.Argument(..., help="Search query"),
    limit: int = typer.Option(5, "--limit", "-l", help="Maximum results"),
):
    store = _new_store()
    traces = store.search_traces(query, limit=limit)
    if not traces:
        console.print("[dim]No matching traces[/dim]")
        return

    table = Table(title="Trace Search")
    table.add_column("Score", justify="right")
    table.add_column("Hash", style="cyan")
    table.add_column("Task")
    table.add_column("Outcome")
    table.add_column("Goal")

    for trace in traces:
        table.add_row(
            f"{trace.similarity_score:.3f}",
            trace.trace_hash[:12],
            trace.task_name,
            trace.outcome,
            _short(trace.goal),
        )
    console.print(table)


@trace_app.command("export")
def trace_export(
    output: str = typer.Option("traces.jsonl", "--output", "-o", help="Output JSONL path"),
):
    store = _new_store()
    output_path = Path(output).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    traces = store.list_recent(n=100_000)
    with output_path.open("w", encoding="utf-8") as f:
        for trace in traces:
            f.write(json.dumps(_trace_to_dict(trace), ensure_ascii=False) + "\n")
    console.print(f"[green]✓[/green] Exported {len(traces)} trace(s) to {output_path}")


@trace_app.command("import")
def trace_import(input: str = typer.Argument(..., help="Input JSONL path")):
    store = _new_store()
    input_path = Path(input).expanduser()
    if not input_path.exists():
        console.print(f"[red]Input not found:[/red] {input_path}")
        raise typer.Exit(1)

    count = 0
    with input_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            trace = _trace_from_dict(json.loads(line))
            if store.get_trace(trace.trace_hash) is None:
                store.upsert_trace(trace)
                count += 1
    console.print(f"[green]✓[/green] Imported {count} trace(s) from {input_path}")
