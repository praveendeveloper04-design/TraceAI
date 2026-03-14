"""
CLI Setup Wizard — Step-by-step configuration for Task Analyzer.

Guides the user through:
  Step 1: Repository configuration
  Step 2: Ticket source (mandatory)
  Step 3: Optional connectors
  Step 4: MCP detection

Uses Rich for beautiful terminal output and Questionary for interactive prompts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import questionary
import structlog
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from task_analyzer.connectors import (
    CONTEXT_CONNECTORS,
    TICKET_CONNECTORS,
    create_default_registry,
)
from task_analyzer.connectors.base.connector import BaseConnector
from task_analyzer.connectors.mcp.connector import McpConnector
from task_analyzer.knowledge.scanner import RepositoryScanner
from task_analyzer.models.schemas import ConnectorConfig, ConnectorType, PlatformConfig
from task_analyzer.security.credential_manager import CredentialManager
from task_analyzer.storage.local_store import LocalStore

logger = structlog.get_logger(__name__)
console = Console()


class SetupWizard:
    """
    Interactive setup wizard that configures Task Analyzer step by step.

    Each step is idempotent — the wizard can be re-run to update configuration.
    """

    def __init__(
        self,
        store: LocalStore | None = None,
        cred_manager: CredentialManager | None = None,
    ) -> None:
        self.store = store or LocalStore()
        self.creds = cred_manager or CredentialManager()
        self.config = self.store.load_config() or PlatformConfig()

    async def run(self) -> PlatformConfig:
        """Run the full setup wizard."""
        self._print_welcome()

        # Step 1: Repository
        await self._step_repository()

        # Step 2: Ticket Source (mandatory)
        await self._step_ticket_source()

        # Step 3: Optional Connectors
        await self._step_optional_connectors()

        # Step 4: MCP Detection
        await self._step_mcp_detection()

        # Save and confirm
        self.store.save_config(self.config)
        self._print_summary()

        return self.config

    # ── Step 1: Repository ────────────────────────────────────────────────

    async def _step_repository(self) -> None:
        console.print("\n")
        console.print(Panel(
            "[bold cyan]Step 1 of 4: Repository Configuration[/bold cyan]\n\n"
            "Provide the path to one or more Git repositories that you want\n"
            "the AI to analyze during investigations.",
            title="Repository Setup",
            border_style="cyan",
        ))

        while True:
            repo_path = await questionary.text(
                "Enter the path to a Git repository:",
                default=str(Path.cwd()),
            ).ask_async()

            if not repo_path:
                break

            repo_path = str(Path(repo_path).resolve())

            # Validate it's a git repo
            git_dir = Path(repo_path) / ".git"
            if not git_dir.exists():
                console.print(f"[red]Error: '{repo_path}' is not a Git repository (.git not found)[/red]")
                continue

            console.print(f"[green]Valid Git repository found at: {repo_path}[/green]")

            # Scan the repository
            with console.status("[bold cyan]Scanning repository structure..."):
                try:
                    scanner = RepositoryScanner(repo_path)
                    profile = scanner.scan()
                    self.store.save_profile(profile)

                    # Display scan results
                    table = Table(title=f"Project Profile: {profile.repo_name}")
                    table.add_column("Property", style="cyan")
                    table.add_column("Value", style="white")
                    table.add_row("Primary Language", profile.primary_language or "Unknown")
                    table.add_row("Languages", ", ".join(f"{k} ({v:.0%})" for k, v in list(profile.languages.items())[:5]))
                    table.add_row("Services/Modules", str(len(profile.services)))
                    table.add_row("Database Models", str(len(profile.database_models)))
                    table.add_row("Key Files", str(len(profile.key_files)))
                    console.print(table)

                except Exception as exc:
                    console.print(f"[red]Error scanning repository: {exc}[/red]")
                    continue

            if repo_path not in self.config.repositories:
                self.config.repositories.append(repo_path)

            add_more = await questionary.confirm(
                "Would you like to add another repository?",
                default=False,
            ).ask_async()

            if not add_more:
                break

        if not self.config.repositories:
            console.print("[yellow]Warning: No repositories configured. You can add them later.[/yellow]")

    # ── Step 2: Ticket Source ─────────────────────────────────────────────

    async def _step_ticket_source(self) -> None:
        console.print("\n")
        console.print(Panel(
            "[bold cyan]Step 2 of 4: Ticket Source (Required)[/bold cyan]\n\n"
            "Configure your primary ticket tracking system.\n"
            "At least one ticket source is required for Task Analyzer to work.",
            title="Ticket Source",
            border_style="cyan",
        ))

        choices = [
            questionary.Choice(f"{cls.display_name} — {cls.description}", value=cls)
            for cls in TICKET_CONNECTORS
        ]

        selected = await questionary.select(
            "Which ticket system do you use?",
            choices=choices,
        ).ask_async()

        if selected:
            connector_config = await self._configure_connector(selected)
            if connector_config:
                self.config.ticket_source = connector_config
                console.print(f"[green]Ticket source configured: {selected.display_name}[/green]")
            else:
                console.print("[red]Ticket source configuration failed. Please re-run setup.[/red]")

    # ── Step 3: Optional Connectors ───────────────────────────────────────

    async def _step_optional_connectors(self) -> None:
        console.print("\n")
        console.print(Panel(
            "[bold cyan]Step 3 of 4: Optional Connectors[/bold cyan]\n\n"
            "Configure additional integrations for richer investigations.\n"
            "These are optional — you can skip any or all of them.",
            title="Optional Connectors",
            border_style="cyan",
        ))

        for cls in CONTEXT_CONNECTORS:
            if cls.connector_type == ConnectorType.MCP:
                continue  # MCP is handled in Step 4

            configure = await questionary.confirm(
                f"Configure {cls.display_name}? ({cls.description})",
                default=False,
            ).ask_async()

            if configure:
                connector_config = await self._configure_connector(cls)
                if connector_config:
                    # Remove existing config for this type
                    self.config.connectors = [
                        c for c in self.config.connectors
                        if c.connector_type != cls.connector_type
                    ]
                    self.config.connectors.append(connector_config)
                    console.print(f"[green]{cls.display_name} configured successfully.[/green]")
            else:
                console.print(f"[dim]Skipped {cls.display_name}[/dim]")

    # ── Step 4: MCP Detection ─────────────────────────────────────────────

    async def _step_mcp_detection(self) -> None:
        console.print("\n")
        console.print(Panel(
            "[bold cyan]Step 4 of 4: MCP (Model Context Protocol)[/bold cyan]\n\n"
            "If you have MCP servers configured locally, Task Analyzer can\n"
            "read that configuration and use them during investigations.",
            title="MCP Configuration",
            border_style="cyan",
        ))

        has_mcp = await questionary.confirm(
            "Do you have MCP configured locally?",
            default=False,
        ).ask_async()

        if has_mcp:
            # Try auto-detection
            from task_analyzer.connectors.mcp.connector import MCP_CONFIG_PATHS

            detected = False
            for path in MCP_CONFIG_PATHS:
                if path.exists():
                    console.print(f"[green]Found MCP configuration at: {path}[/green]")
                    detected = True
                    break

            if detected:
                use_detected = await questionary.confirm(
                    "Use the detected MCP configuration?",
                    default=True,
                ).ask_async()

                if use_detected:
                    mcp_config = ConnectorConfig(
                        connector_type=ConnectorType.MCP,
                        name="mcp-local",
                        enabled=True,
                        settings={"auto_detect": True},
                    )
                    self.config.connectors.append(mcp_config)
                    console.print("[green]MCP configured from local settings.[/green]")
                    return

            # Manual configuration
            console.print("[yellow]No MCP configuration auto-detected.[/yellow]")

        configure_manual = await questionary.confirm(
            "Would you like to manually configure an MCP server?",
            default=False,
        ).ask_async()

        if configure_manual:
            server_url = await questionary.text(
                "MCP server URL:",
            ).ask_async()

            if server_url:
                mcp_config = ConnectorConfig(
                    connector_type=ConnectorType.MCP,
                    name="mcp-manual",
                    enabled=True,
                    settings={"servers": [{"name": "manual", "url": server_url}]},
                )
                self.config.connectors.append(mcp_config)
                console.print("[green]MCP server configured.[/green]")
        else:
            console.print("[dim]Skipped MCP configuration.[/dim]")

    # ── Connector Configuration Helper ────────────────────────────────────

    async def _configure_connector(
        self, connector_class: type[BaseConnector]
    ) -> ConnectorConfig | None:
        """Ask setup questions for a connector and store credentials."""
        questions = connector_class.get_setup_questions()
        if not questions:
            return ConnectorConfig(
                connector_type=connector_class.connector_type,
                name=connector_class.connector_type.value,
                enabled=True,
            )

        settings: dict[str, Any] = {}
        credential_keys: list[str] = []
        connector_name = connector_class.connector_type.value

        console.print("\n[bold]Please provide the following information:[/bold]")
        console.print(
            Panel(
                "[dim]Credentials will be stored securely in your OS keychain.\n"
                "They are never written to disk in plaintext.[/dim]",
                border_style="blue",
            )
        )

        for q in questions:
            key = q["key"]
            prompt = q["prompt"]
            required = q.get("required", False)
            is_secret = q.get("secret", False)
            default = q.get("default")

            if is_secret:
                value = await questionary.password(
                    f"{prompt}:",
                ).ask_async()
            else:
                value = await questionary.text(
                    f"{prompt}:",
                    default=default or "",
                ).ask_async()

            if required and not value:
                console.print(f"[red]'{prompt}' is required. Configuration aborted.[/red]")
                return None

            if value:
                if is_secret:
                    self.creds.store(connector_name, key, value)
                    credential_keys.append(key)
                    console.print(f"  [green]Credential '{key}' stored securely in OS keychain.[/green]")
                else:
                    settings[key] = value

        return ConnectorConfig(
            connector_type=connector_class.connector_type,
            name=connector_name,
            enabled=True,
            settings=settings,
            credential_keys=credential_keys,
        )

    # ── Display Helpers ───────────────────────────────────────────────────

    def _print_welcome(self) -> None:
        console.print("\n")
        console.print(Panel(
            Text.from_markup(
                "[bold white]Task Analyzer[/bold white]\n"
                "[dim]AI-Powered Developer Investigation Platform[/dim]\n\n"
                "This wizard will guide you through the initial setup.\n"
                "You can re-run this wizard at any time to update your configuration.\n\n"
                "[bold]Steps:[/bold]\n"
                "  1. Configure Git repositories\n"
                "  2. Set up your ticket source (required)\n"
                "  3. Configure optional integrations\n"
                "  4. MCP detection and configuration"
            ),
            title="Welcome to Task Analyzer Setup",
            border_style="bright_blue",
            padding=(1, 2),
        ))

    def _print_summary(self) -> None:
        console.print("\n")
        table = Table(title="Configuration Summary", border_style="green")
        table.add_column("Component", style="cyan")
        table.add_column("Status", style="white")
        table.add_column("Details", style="dim")

        # Repositories
        repo_count = len(self.config.repositories)
        table.add_row(
            "Repositories",
            f"[green]{repo_count} configured[/green]" if repo_count else "[yellow]None[/yellow]",
            ", ".join(Path(r).name for r in self.config.repositories[:3]),
        )

        # Ticket source
        if self.config.ticket_source:
            table.add_row(
                "Ticket Source",
                "[green]Configured[/green]",
                self.config.ticket_source.connector_type.value,
            )
        else:
            table.add_row("Ticket Source", "[red]Not configured[/red]", "Required!")

        # Optional connectors
        for conn in self.config.connectors:
            table.add_row(
                f"Connector: {conn.connector_type.value}",
                "[green]Enabled[/green]" if conn.enabled else "[yellow]Disabled[/yellow]",
                conn.name,
            )

        console.print(table)
        console.print("\n[green bold]Setup complete![/green bold]")
        console.print(f"[dim]Configuration saved to: {self.store.config_path}[/dim]")
        console.print("[dim]Run 'task-analyzer investigate' to start your first investigation.[/dim]\n")
