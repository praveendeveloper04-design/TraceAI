"""
CLI Main — Typer-based command-line interface for Task Analyzer.

Commands:
  setup       — Run the configuration wizard
  investigate — Investigate a specific task
  tasks       — List assigned tasks
  status      — Show current configuration status
  profile     — Show or regenerate project profiles
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import structlog
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from task_analyzer import __version__

logger = structlog.get_logger(__name__)
console = Console()

app = typer.Typer(
    name="task-analyzer",
    help="AI-Powered Developer Investigation Platform",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


@app.callback()
def main_callback(
    version: bool = typer.Option(False, "--version", "-v", help="Show version"),
) -> None:
    """Task Analyzer — AI-powered developer investigation platform."""
    if version:
        console.print(f"task-analyzer v{__version__}")
        raise typer.Exit()


# ── Setup Command ─────────────────────────────────────────────────────────────

@app.command()
def setup() -> None:
    """Run the interactive setup wizard to configure Task Analyzer."""
    from task_analyzer.cli.wizard import SetupWizard

    wizard = SetupWizard()
    asyncio.run(wizard.run())


# ── Tasks Command ─────────────────────────────────────────────────────────────

@app.command()
def tasks(
    assigned_to: str = typer.Option(None, "--assigned-to", "-a", help="Filter by assignee"),
    query: str = typer.Option(None, "--query", "-q", help="Search query"),
    max_results: int = typer.Option(20, "--max", "-m", help="Maximum results"),
) -> None:
    """Fetch and list tasks from the configured ticket source."""
    asyncio.run(_fetch_tasks(assigned_to, query, max_results))


async def _fetch_tasks(assigned_to: str | None, query: str | None, max_results: int) -> None:
    from task_analyzer.connectors import create_default_registry
    from task_analyzer.storage.local_store import LocalStore

    store = LocalStore()
    config = store.load_config()
    if not config or not config.ticket_source:
        console.print("[red]No ticket source configured. Run 'task-analyzer setup' first.[/red]")
        raise typer.Exit(1)

    registry = create_default_registry()
    connector = registry.create(config.ticket_source)

    with console.status("[bold cyan]Fetching tasks..."):
        try:
            await connector.validate_connection()
            task_list = await connector.fetch_tasks(
                assigned_to=assigned_to,
                query=query,
                max_results=max_results,
            )
        except Exception as exc:
            console.print(f"[red]Error fetching tasks: {exc}[/red]")
            raise typer.Exit(1)

    if not task_list:
        console.print("[yellow]No tasks found.[/yellow]")
        return

    table = Table(title=f"Tasks ({len(task_list)} found)")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Type", style="magenta")
    table.add_column("Severity", style="red")
    table.add_column("Title", style="white")
    table.add_column("Status", style="green")
    table.add_column("Assigned To", style="dim")

    for task in task_list:
        table.add_row(
            task.external_id,
            task.task_type.value,
            task.severity.value,
            task.title[:60],
            task.status.value,
            task.assigned_to or "-",
        )

    console.print(table)
    await connector.disconnect()


# ── Investigate Command ───────────────────────────────────────────────────────

@app.command()
def investigate(
    task_id: str = typer.Argument(help="Task ID to investigate"),
    output: str = typer.Option(None, "--output", "-o", help="Output file path for the report"),
) -> None:
    """Run an AI investigation on a specific task."""
    asyncio.run(_investigate(task_id, output))


async def _investigate(task_id: str, output: str | None) -> None:
    from task_analyzer.connectors import create_default_registry
    from task_analyzer.investigation.engine import InvestigationEngine
    from task_analyzer.storage.local_store import LocalStore

    store = LocalStore()
    config = store.load_config()
    if not config or not config.ticket_source:
        console.print("[red]No ticket source configured. Run 'task-analyzer setup' first.[/red]")
        raise typer.Exit(1)

    registry = create_default_registry()

    # Create ticket source connector
    ticket_connector = registry.create(config.ticket_source)

    # Create optional connectors
    for conn_config in config.connectors:
        if conn_config.enabled:
            try:
                registry.create(conn_config)
            except Exception as exc:
                console.print(f"[yellow]Warning: Could not initialize {conn_config.name}: {exc}[/yellow]")

    # Fetch the task
    with console.status(f"[bold cyan]Fetching task {task_id}..."):
        try:
            await ticket_connector.validate_connection()
            task = await ticket_connector.get_task_detail(task_id)
        except Exception as exc:
            console.print(f"[red]Error fetching task: {exc}[/red]")
            raise typer.Exit(1)

    if not task:
        console.print(f"[red]Task '{task_id}' not found.[/red]")
        raise typer.Exit(1)

    console.print(f"\n[bold]Investigating: {task.title}[/bold]")
    console.print(f"[dim]Type: {task.task_type.value} | Severity: {task.severity.value} | Status: {task.status.value}[/dim]\n")

    # Load project profiles
    profiles = []
    for repo_path in config.repositories:
        profile = store.load_profile(Path(repo_path).name)
        if profile:
            profiles.append(profile)

    # Run investigation
    engine = InvestigationEngine(config=config, registry=registry, profiles=profiles)

    with console.status("[bold cyan]Running AI investigation... This may take a minute."):
        report = await engine.investigate(task)

    # Save report
    store.save_investigation(report)

    # Display report
    console.print("\n")
    console.print(Panel(
        report.to_markdown(),
        title="Investigation Report",
        border_style="green" if report.status.value == "completed" else "red",
        padding=(1, 2),
    ))

    if output:
        Path(output).write_text(report.to_markdown(), encoding="utf-8")
        console.print(f"\n[green]Report saved to: {output}[/green]")

    console.print(f"\n[dim]Report ID: {report.id}[/dim]")
    console.print(f"[dim]Saved to: {store.investigations_dir / f'{report.id}.json'}[/dim]")

    await registry.disconnect_all()


# ── Status Command ────────────────────────────────────────────────────────────

@app.command()
def status() -> None:
    """Show current Task Analyzer configuration status."""
    from task_analyzer.storage.local_store import LocalStore

    store = LocalStore()
    config = store.load_config()

    if not config:
        console.print("[yellow]Task Analyzer is not configured. Run 'task-analyzer setup'.[/yellow]")
        return

    table = Table(title="Task Analyzer Status")
    table.add_column("Component", style="cyan")
    table.add_column("Status", style="white")

    table.add_row("Version", config.version)
    table.add_row("Repositories", str(len(config.repositories)))
    table.add_row(
        "Ticket Source",
        config.ticket_source.connector_type.value if config.ticket_source else "[red]Not configured[/red]",
    )
    table.add_row("Connectors", str(len(config.connectors)))
    table.add_row("LLM Model", config.llm_model)
    table.add_row("Profiles", str(len(store.list_profiles())))
    table.add_row("Investigations", str(len(store.list_investigations())))

    console.print(table)


# ── Profile Command ───────────────────────────────────────────────────────────

@app.command()
def profile(
    repo_path: str = typer.Argument(None, help="Repository path to scan/rescan"),
) -> None:
    """Show or regenerate project knowledge profiles."""
    from task_analyzer.knowledge.scanner import RepositoryScanner
    from task_analyzer.storage.local_store import LocalStore

    store = LocalStore()

    if repo_path:
        # Rescan a specific repository
        with console.status(f"[bold cyan]Scanning {repo_path}..."):
            scanner = RepositoryScanner(repo_path)
            p = scanner.scan()
            store.save_profile(p)
        console.print(f"[green]Profile updated for: {p.repo_name}[/green]")
        console.print(p.context_summary)
    else:
        # List all profiles
        profiles = store.list_profiles()
        if not profiles:
            console.print("[yellow]No profiles found. Run 'task-analyzer setup' to scan repositories.[/yellow]")
            return

        for name in profiles:
            p = store.load_profile(name)
            if p:
                console.print(Panel(
                    p.context_summary,
                    title=f"Profile: {p.repo_name}",
                    border_style="cyan",
                ))


# ── History Command ───────────────────────────────────────────────────────────

@app.command()
def history(
    limit: int = typer.Option(10, "--limit", "-l", help="Number of investigations to show"),
) -> None:
    """Show recent investigation history."""
    from task_analyzer.storage.local_store import LocalStore

    store = LocalStore()
    investigations = store.list_investigations()[:limit]

    if not investigations:
        console.print("[yellow]No investigations found.[/yellow]")
        return

    table = Table(title="Recent Investigations")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Task", style="white")
    table.add_column("Status", style="green")
    table.add_column("Date", style="dim")

    for inv in investigations:
        table.add_row(
            inv["id"][:8] + "...",
            inv.get("task_title", "Unknown")[:50],
            inv.get("status", "unknown"),
            inv.get("started_at", "")[:19],
        )

    console.print(table)


# ── Serve Command ─────────────────────────────────────────────────────────────

@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="Server host"),
    port: int = typer.Option(7420, "--port", "-p", help="Server port"),
) -> None:
    """Start the Task Analyzer API server for the VS Code extension."""
    from task_analyzer.api.server import start_server

    console.print(f"[bold cyan]Starting Task Analyzer API server on {host}:{port}[/bold cyan]")
    console.print("[dim]Press Ctrl+C to stop.[/dim]\n")
    start_server(host=host, port=port)


if __name__ == "__main__":
    app()
