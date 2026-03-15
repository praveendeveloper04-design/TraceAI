#!/usr/bin/env python3
"""
TraceAI Setup Helper — Interactive configuration for TraceAI.

Usage:
    python traceai_setup.py
    # or, if installed:
    traceai setup

This script is **idempotent** — running it multiple times will never
overwrite existing credentials. It detects what is already configured
and only prompts for missing items.

Steps:
  1. Initialize ~/.traceai directory structure
  2. Configure Claude API key (via PDI AI Gateway)
  3. Configure Azure DevOps authentication
  4. Configure SQL MCP database servers
  5. Validate all connections
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from getpass import getpass
from pathlib import Path

# ─── Constants ────────────────────────────────────────────────────────────────

TRACEAI_DIR = Path.home() / ".traceai"
CREDENTIALS_FILE = TRACEAI_DIR / "credentials.json"
CONFIG_FILE = TRACEAI_DIR / "config.json"

BANNER = r"""
+==============================================================+
|                                                              |
|                    TraceAI Setup Helper                       |
|          AI-Powered Developer Investigation Platform          |
|                                                              |
+==============================================================+
"""

TRUST_NOTICE = """
+--------------------------------------------------------------+
|              TraceAI Secure Credential Store                  |
|                                                              |
|  Your credentials are stored locally on your machine.        |
|  They are never transmitted externally or committed to        |
|  source control.                                             |
|                                                              |
|  Location: {path:<44s} |
+--------------------------------------------------------------+
"""

# ─── Credential Store ─────────────────────────────────────────────────────────


def load_credentials() -> dict:
    """Load existing credentials, or return empty dict."""
    if CREDENTIALS_FILE.exists():
        try:
            data = json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def save_credentials(creds: dict) -> None:
    """Save credentials to disk."""
    CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_FILE.write_text(
        json.dumps(creds, indent=2) + "\n", encoding="utf-8"
    )
    # Restrict permissions on Unix
    if platform.system() != "Windows":
        os.chmod(CREDENTIALS_FILE, 0o600)


def load_config() -> dict:
    """Load existing config, or return empty dict."""
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def save_config(config: dict) -> None:
    """Save config to disk."""
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        json.dumps(config, indent=2) + "\n", encoding="utf-8"
    )


# ─── Helpers ──────────────────────────────────────────────────────────────────


def print_header(text: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {text}")
    print(f"{'=' * 60}\n")


def print_ok(text: str) -> None:
    print(f"  [OK] {text}")


def print_warn(text: str) -> None:
    print(f"  [!!] {text}")


def print_fail(text: str) -> None:
    print(f"  [FAIL] {text}")


def print_info(text: str) -> None:
    print(f"  [..] {text}")


def prompt_secret(prompt_text: str) -> str:
    """Prompt for a secret value (hidden input)."""
    try:
        return getpass(f"  {prompt_text}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


def prompt_input(prompt_text: str, default: str = "") -> str:
    """Prompt for a text value."""
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"  {prompt_text}{suffix}: ").strip()
        return value or default
    except (EOFError, KeyboardInterrupt):
        print()
        return default


def prompt_choice(prompt_text: str, choices: list[str]) -> str:
    """Prompt user to pick from a list."""
    print(f"  {prompt_text}\n")
    for i, choice in enumerate(choices, 1):
        print(f"    {i}. {choice}")
    print()
    while True:
        try:
            raw = input(f"  Enter choice [1-{len(choices)}]: ").strip()
            idx = int(raw) - 1
            if 0 <= idx < len(choices):
                return choices[idx]
        except (ValueError, EOFError, KeyboardInterrupt):
            print()
            return choices[0]
        print(f"  Please enter a number between 1 and {len(choices)}")


# ─── Step 1: Initialize Directory ────────────────────────────────────────────


def step_init_directory() -> None:
    """Create ~/.traceai directory structure."""
    print_header("Step 1 — Initialize TraceAI Directory")

    dirs = [
        TRACEAI_DIR,
        TRACEAI_DIR / "cache",
        TRACEAI_DIR / "profiles",
        TRACEAI_DIR / "investigations",
        TRACEAI_DIR / "logs",
        TRACEAI_DIR / "runtime",
    ]

    created = 0
    for d in dirs:
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            created += 1

    if created > 0:
        print_ok(f"Created {created} directories under {TRACEAI_DIR}")
    else:
        print_ok(f"Directory structure already exists at {TRACEAI_DIR}")

    # Show trust notice
    print(TRUST_NOTICE.format(path=str(CREDENTIALS_FILE)))

    # Show structure
    print("  Directory structure:")
    print(f"    {TRACEAI_DIR}/")
    for d in dirs[1:]:
        print(f"    +-- {d.name}/")
    print(f"    +-- config.json")
    print(f"    +-- credentials.json")
    print()


# ─── Step 2: Credential Storage ──────────────────────────────────────────────


def step_credential_storage() -> dict:
    """Ensure credential file exists and return current credentials."""
    print_header("Step 2 — Secure Credential Storage")

    creds = load_credentials()

    if CREDENTIALS_FILE.exists():
        sections = [k for k in creds.keys() if creds[k]]
        if sections:
            print_ok(f"Credential store exists with sections: {', '.join(sections)}")
            print_info("Existing credentials will NOT be overwritten.")
        else:
            print_ok("Credential store exists (empty).")
    else:
        save_credentials(creds)
        print_ok(f"Created credential store at {CREDENTIALS_FILE}")

    print(f"\n  File: {CREDENTIALS_FILE}")
    print(f"  Size: {CREDENTIALS_FILE.stat().st_size} bytes")
    print()

    return creds


# ─── Step 3: Claude API Configuration ────────────────────────────────────────


def step_claude_api(creds: dict) -> dict:
    """Configure and test the Claude API key."""
    print_header("Step 3 — Claude API Configuration")

    # Check existing sources
    existing_key = None
    source = None

    # Check credentials.json
    anthropic_creds = creds.get("anthropic", {})
    if isinstance(anthropic_creds, dict) and anthropic_creds.get("api_key"):
        existing_key = anthropic_creds["api_key"]
        source = "credentials.json"

    # Check environment variable
    if not existing_key:
        env_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if env_key:
            existing_key = env_key
            source = "environment variable"

    # Check Windows registry
    if not existing_key and platform.system() == "Windows":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as reg:
                val, _ = winreg.QueryValueEx(reg, "ANTHROPIC_API_KEY")
                val = str(val).strip()
                if val:
                    existing_key = val
                    source = "Windows registry"
        except Exception:
            pass

    if existing_key:
        print_ok(f"Claude API key found (source: {source})")
        print_info(f"Key: {existing_key[:4]}... (length: {len(existing_key)})")

        change = prompt_input("Replace existing key? (y/N)", "N")
        if change.lower() != "y":
            # Also check for base URL
            _check_base_url()
            return creds
    else:
        print_info("No Claude API key found.")

    print()
    print("  TraceAI requires your Claude API key.")
    print()
    print("  Please open your OKTA dashboard and navigate to:")
    print()
    print("    PDI AI Gateway")
    print()
    print("  Generate your personal Claude API key and paste it below.")
    print()

    api_key = prompt_secret("Enter your Claude API key")
    if not api_key:
        print_warn("No key entered. Skipping Claude API configuration.")
        return creds

    # Store in credentials.json
    if "anthropic" not in creds:
        creds["anthropic"] = {}
    creds["anthropic"]["api_key"] = api_key
    save_credentials(creds)
    print_ok("Claude API key stored in credentials.json")

    # Check for base URL
    _check_base_url()

    # Test the key
    _test_claude_api(api_key)

    return creds


def _check_base_url() -> None:
    """Check if ANTHROPIC_BASE_URL is configured."""
    base_url = os.environ.get("ANTHROPIC_BASE_URL", "").strip()
    if not base_url and platform.system() == "Windows":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as reg:
                val, _ = winreg.QueryValueEx(reg, "ANTHROPIC_BASE_URL")
                base_url = str(val).strip()
        except Exception:
            pass

    if base_url:
        print_ok(f"AI Gateway URL: {base_url}")
    else:
        print_info("No custom ANTHROPIC_BASE_URL set (using default api.anthropic.com)")


def _test_claude_api(api_key: str) -> None:
    """Test the Claude API connection."""
    print()
    print("  Testing Claude API connection...")

    base_url = os.environ.get("ANTHROPIC_BASE_URL", "").strip()
    if not base_url and platform.system() == "Windows":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as reg:
                val, _ = winreg.QueryValueEx(reg, "ANTHROPIC_BASE_URL")
                base_url = str(val).strip()
        except Exception:
            pass

    try:
        import httpx

        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        target = base_url or "https://api.anthropic.com"
        url = f"{target}/v1/messages"
        payload = {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "Say OK"}],
        }

        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, json=payload, headers=headers)

        if resp.status_code == 200:
            print_ok("Claude API connection successful!")
        elif resp.status_code == 401:
            print_fail("Authentication failed. Check your API key.")
            print_info(f"Response: {resp.text[:200]}")
        else:
            print_warn(f"Unexpected response: HTTP {resp.status_code}")
            print_info(f"Response: {resp.text[:200]}")

    except ImportError:
        print_warn("httpx not installed. Skipping API test.")
    except Exception as exc:
        print_fail(f"Connection failed: {type(exc).__name__}: {exc}")
        print_info("Check your network connection and API gateway URL.")


# ─── Step 4: Azure DevOps Authentication ─────────────────────────────────────


def _find_az() -> str | None:
    """Find the Azure CLI executable, checking PATH and known install locations."""
    is_win = platform.system() == "Windows"
    if is_win:
        az = shutil.which("az.cmd") or shutil.which("az")
    else:
        az = shutil.which("az")
    if az:
        return az
    if is_win:
        candidates = [
            r"C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd",
            r"C:\Program Files (x86)\Microsoft SDKs\Azure\CLI2\wbin\az.cmd",
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Azure CLI\wbin\az.cmd"),
        ]
        for path in candidates:
            if os.path.isfile(path):
                return path
    return None


def step_azure_devops(creds: dict) -> dict:
    """Configure Azure DevOps authentication using Azure CLI."""
    print_header("Step 4 -- Azure DevOps Authentication")

    print("  TraceAI authenticates with Azure DevOps using Azure CLI.")
    print("  No tokens or passwords are stored -- authentication is live.")
    print()

    # 4a. Check if Azure CLI is installed
    print("  Checking Azure CLI installation...")
    az = _find_az()
    if not az:
        print_fail("Azure CLI is not installed.")
        print()
        print("  Azure CLI is required for Azure DevOps authentication.")
        print()
        print("  Install it from:")
        print("    https://learn.microsoft.com/en-us/cli/azure/install-azure-cli")
        print()
        print("  After installing, restart your terminal and rerun setup.")
        print()
        prompt_input("Press Enter after installing Azure CLI...", "")
        az = _find_az()
        if not az:
            print_warn("Azure CLI still not detected. Skipping Azure DevOps setup.")
            print_info("Rerun setup after installing Azure CLI.")
            return creds
        print_ok("Azure CLI detected after install.")
    else:
        try:
            result = subprocess.run([az, "--version"], capture_output=True, text=True, timeout=10)
            version_line = result.stdout.strip().split("\n")[0]
            print_ok(f"Azure CLI installed: {version_line}")
        except Exception:
            print_ok(f"Azure CLI found at: {az}")

    # 4b. Check if user is logged in
    print()
    print("  Checking Azure login status...")
    try:
        account = subprocess.run(
            [az, "account", "show", "--query", "{name:user.name,tenant:tenantId}", "-o", "json"],
            capture_output=True, text=True, timeout=15,
        )
        if account.returncode != 0:
            raise ValueError("not logged in")
        import json as _json
        info = _json.loads(account.stdout)
        print_ok(f"Logged in as: {info.get('name', 'unknown')}")
    except (ValueError, Exception):
        print_warn("You are not logged in to Azure CLI.")
        print()
        print("  Please run the following command in a separate terminal:")
        print()
        print("    az login")
        print()
        prompt_input("Press Enter after completing az login...", "")
        try:
            recheck = subprocess.run(
                [az, "account", "show", "--query", "user.name", "-o", "tsv"],
                capture_output=True, text=True, timeout=15,
            )
            if recheck.returncode == 0 and recheck.stdout.strip():
                print_ok(f"Logged in as: {recheck.stdout.strip()}")
            else:
                print_warn("Still not logged in. Skipping Azure DevOps setup.")
                print_info("Run 'az login' and rerun setup.")
                return creds
        except Exception:
            print_warn("Login check failed. Skipping Azure DevOps setup.")
            return creds

    # 4c. Acquire token
    print()
    print("  Acquiring Azure DevOps access token...")
    try:
        token_result = subprocess.run(
            [
                az, "account", "get-access-token",
                "--resource", "499b84ac-1321-427f-aa17-267ca6975798",
                "--query", "accessToken",
                "-o", "tsv",
            ],
            capture_output=True, text=True, timeout=15,
        )
        if token_result.returncode != 0 or not token_result.stdout.strip():
            print_fail(f"Token acquisition failed: {token_result.stderr.strip()[:200]}")
            print_info("Try: az login --allow-no-subscriptions")
            print_info("Skipping Azure DevOps setup. Rerun setup after fixing.")
            return creds
        token = token_result.stdout.strip()
        print_ok(f"Access token acquired (length: {len(token)})")
    except subprocess.TimeoutExpired:
        print_fail("Token acquisition timed out.")
        return creds

    # 4d. Select project
    print()
    projects = [
        "PLC -- pdidev / LogisticsCloud",
        "OilDroid -- PDIDevWEUR / oildroid-itm",
    ]
    selected = prompt_choice("Select your Azure DevOps project:", projects)

    if "PLC" in selected:
        org, project = "pdidev", "LogisticsCloud"
    else:
        org, project = "PDIDevWEUR", "oildroid-itm"

    print()
    print_info(f"Organization: {org}")
    print_info(f"Project: {project}")

    # Update config -- no credential_keys, auth is live via Azure CLI
    config = load_config()
    config["ticket_source"] = {
        "connector_type": "azure_devops",
        "name": "azure_devops",
        "enabled": True,
        "settings": {
            "organization": org,
            "project": project,
        },
        "credential_keys": [],
    }
    save_config(config)

    # 4e. Validate access
    print()
    print(f"  Validating access to {org}/{project}...")
    try:
        import httpx

        url = f"https://dev.azure.com/{org}/_apis/projects?api-version=7.1"
        headers = {"Authorization": f"Bearer {token}"}
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(url, headers=headers)

        if resp.status_code == 200:
            projects_data = resp.json().get("value", [])
            names = [p["name"] for p in projects_data]
            if project in names:
                print_ok(f"Access verified: {org}/{project}")
            else:
                available = ", ".join(names[:5])
                print_warn(f"Project '{project}' not found. Available: {available}")
        elif resp.status_code == 401:
            print_warn("Token was rejected by Azure DevOps.")
            print_info("You currently do not have access to this project. Please request access and rerun setup.")
        elif resp.status_code == 403:
            print_warn("Access denied for this organization.")
            print_info("You currently do not have access to this project. Please request access and rerun setup.")
        else:
            print_warn(f"Unexpected response: HTTP {resp.status_code}")
            print_info("You currently do not have access to this project. Please request access and rerun setup.")

    except ImportError:
        print_warn("httpx not installed. Skipping validation.")
    except Exception as exc:
        print_warn(f"Validation failed: {exc}")
        print_info("You currently do not have access to this project. Please request access and rerun setup.")

    return creds


# ─── Step 5: SQL MCP Configuration ───────────────────────────────────────────

SQL_SERVERS = [
    {"name": "MT Dev",       "host": "10.166.39.43",                                                    "port": 1433},
    {"name": "ADNOC UAT",    "host": "10.166.244.102",                                                  "port": 1433},
    {"name": "MT Stage",     "host": "plc-mt-nlb-2d2da30ba2ae1d12.elb.eu-central-1.amazonaws.com",      "port": 1433},
    {"name": "MT Test",      "host": "10.166.43.36",                                                    "port": 1433},
    {"name": "BP Stage",     "host": "bpstagedb.stage.logistics.pditechnologies.com",                   "port": 1433},
]


def step_sql_mcp(creds: dict) -> dict:
    """Configure SQL MCP database servers."""
    print_header("Step 5 -- SQL MCP Database Configuration")

    existing_sql = creds.get("sql_database", {})
    if isinstance(existing_sql, dict) and existing_sql.get("connection_string"):
        print_ok("SQL database credentials found.")
        print_info(f"Server: {existing_sql.get('server_name', 'unknown')}")
        print_info("Existing credentials will NOT be overwritten.")
        change = prompt_input("Reconfigure SQL databases? (y/N)", "N")
        if change.lower() != "y":
            return creds

    print("  Available SQL servers:\n")
    for i, srv in enumerate(SQL_SERVERS, 1):
        print(f"    {i}. {srv['name']:<15s} {srv['host']}:{srv['port']}")
    print()

    # Select server
    while True:
        try:
            raw = prompt_input(f"Select server [1-{len(SQL_SERVERS)}]", "1")
            idx = int(raw) - 1
            if 0 <= idx < len(SQL_SERVERS):
                selected = SQL_SERVERS[idx]
                break
        except ValueError:
            pass
        print(f"  Please enter a number between 1 and {len(SQL_SERVERS)}")

    print()
    print_info(f"Selected: {selected['name']} ({selected['host']}:{selected['port']})")
    print()

    # Test raw TCP connectivity first
    _test_tcp_connectivity(selected["host"], selected["port"])

    # Prompt for credentials
    username = prompt_input("Database username")
    if not username:
        print_warn("No username entered. Skipping SQL configuration.")
        return creds

    print()
    print("  Enter your database password.")
    print("  You can paste using:")
    print("    - Right-click in terminal")
    print("    - Ctrl+V (in some terminals)")
    print()
    password = prompt_secret("Database password (hidden)")
    if not password:
        print_warn("No password entered. Skipping SQL configuration.")
        return creds

    # Prompt for database name
    database = prompt_input("Database name", "master")

    # Build connection string with proper URL-encoding for special characters
    from urllib.parse import quote_plus
    conn_str = (
        f"mssql+pyodbc://{quote_plus(username)}:{quote_plus(password)}@"
        f"{selected['host']}:{selected['port']}/{database}"
        f"?driver=ODBC+Driver+17+for+SQL+Server&TrustServerCertificate=yes"
    )

    # Store credentials
    if "sql_database" not in creds:
        creds["sql_database"] = {}
    creds["sql_database"]["connection_string"] = conn_str
    creds["sql_database"]["server_name"] = selected["name"]
    creds["sql_database"]["host"] = selected["host"]
    save_credentials(creds)
    print_ok("SQL credentials stored.")

    # Update config
    config = load_config()
    connectors = config.get("connectors", [])
    connectors = [c for c in connectors if c.get("connector_type") != "sql_database"]
    connectors.append({
        "connector_type": "sql_database",
        "name": "sql_database",
        "enabled": True,
        "settings": {
            "database_name": selected["name"],
        },
        "credential_keys": ["connection_string"],
    })
    config["connectors"] = connectors
    save_config(config)

    # Test full SQL connectivity
    _test_sql_connection(conn_str, selected["name"])

    return creds


def _test_tcp_connectivity(host: str, port: int) -> None:
    """Test raw TCP connectivity to the SQL server."""
    import socket

    print(f"  Testing network connectivity to {host}:{port}...")
    try:
        sock = socket.create_connection((host, port), timeout=5)
        sock.close()
        print_ok(f"Network reachable: {host}:{port}")
    except socket.timeout:
        print_warn(f"Connection timed out. Check VPN connection.")
        print_info(f"Server {host}:{port} is not reachable from this network.")
    except OSError as exc:
        print_warn(f"Network unreachable: {exc}")
        print_info("Check VPN connection. SQL will be unavailable until connectivity is restored.")


def _test_sql_connection(conn_str: str, server_name: str) -> None:
    """Test SQL server connectivity."""
    print()
    print(f"  Testing connection to {server_name}...")

    try:
        from sqlalchemy import create_engine, text

        engine = create_engine(conn_str, connect_args={"timeout": 10})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print_ok(f"Connected to {server_name} successfully!")
        engine.dispose()

    except ImportError:
        print_warn("sqlalchemy not installed. Skipping connection test.")
    except Exception as exc:
        error_msg = str(exc)
        print_fail(f"Connection failed: {type(exc).__name__}")
        if "pyodbc" in error_msg.lower():
            print_info("ODBC driver not installed. Install 'ODBC Driver 17 for SQL Server'.")
        elif "timeout" in error_msg.lower() or "unreachable" in error_msg.lower():
            print_info("Server unreachable. Check VPN connection.")
        else:
            print_info(f"Details: {error_msg[:200]}")
        print_info("SQL will be unavailable until connectivity is restored.")


# ─── Claude Key Detection ─────────────────────────────────────────────────────


def _detect_claude_key() -> bool:
    """Check if the Claude API key is available from any source."""
    # Check credentials.json
    creds = load_credentials()
    if creds.get("anthropic", {}).get("api_key"):
        return True

    # Check current environment
    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return True

    # Check Windows registry
    if platform.system() == "Windows":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as reg:
                val, _ = winreg.QueryValueEx(reg, "ANTHROPIC_API_KEY")
                if str(val).strip():
                    return True
        except Exception:
            pass

    return False


# ─── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    print(BANNER)
    print(f"  Platform: {platform.system()} {platform.release()}")
    print(f"  Python:   {sys.version.split()[0]}")
    print(f"  Home:     {Path.home()}")
    print(f"  Config:   {TRACEAI_DIR}")
    print()

    # Step 1
    step_init_directory()

    # Step 2
    creds = step_credential_storage()

    # Step 3
    creds = step_claude_api(creds)

    # Step 4
    creds = step_azure_devops(creds)

    # Step 5
    creds = step_sql_mcp(creds)

    # Ensure config has required fields
    config = load_config()
    config.setdefault("config_version", "1.0")
    config.setdefault("version", "1.0")
    config.setdefault("mode", "safe")
    config.setdefault("background_refresh", True)
    config.setdefault("repositories", [])
    config.setdefault("llm_model", "claude-sonnet-4-20250514")
    config.setdefault("llm_temperature", 0.1)
    config.setdefault("llm_max_tokens", 8192)
    config.setdefault("investigation_max_steps", 15)
    save_config(config)

    # Summary
    print()
    print("  ============================================================")
    print("  TraceAI Setup Complete")
    print("  ============================================================")
    print()
    print(f"  Configuration saved to:")
    print(f"    {CONFIG_FILE}")
    print()
    print(f"  Credentials stored securely in:")
    print(f"    {CREDENTIALS_FILE}")
    print()

    creds = load_credentials()
    has_claude = _detect_claude_key()
    has_ado = bool(load_config().get("ticket_source", {}).get("settings", {}).get("organization"))
    has_sql = bool(creds.get("sql_database", {}).get("connection_string"))

    print("  Component Status:")
    print()
    print(f"    Claude API       {'[OK] Configured' if has_claude else '[--] Not configured'}")
    print(f"    Azure DevOps     {'[OK] Configured' if has_ado else '[--] Not configured'}")
    print(f"    SQL Database     {'[OK] Configured' if has_sql else '[--] Not configured'}")
    print()
    print("  TraceAI is now ready to use.")
    print()
    print("  Next Steps:")
    print()
    print("    1. Open VS Code")
    print("    2. Open your repository folder")
    print("    3. Click a task in the TraceAI sidebar to begin investigation")
    print()
    print("  TraceAI will automatically start its backend services.")
    print()
    print("  To rerun setup: python traceai_setup.py")
    print()
    print()


if __name__ == "__main__":
    main()
