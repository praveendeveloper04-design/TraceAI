"""
Security — Credential management with OS keychain + file-based fallback.

Credential resolution order (first match wins):

  1. OS keychain via ``keyring`` library
     - macOS  → Keychain
     - Windows → Windows Credential Locker
     - Linux  → Secret Service (GNOME Keyring / KWallet)

  2. File-based fallback: ``~/.traceai/credentials.json``
     Structure:
       {
         "anthropic": { "api_key": "sk-ant-..." },
         "sql_database": { "connection_string": "..." }
       }

  3. Connector config settings (last resort)
     If the setup wizard stored the PAT in config.json settings
     instead of the keychain, the connector can still find it.

No secret is ever written to disk by TraceAI itself — the file-based
fallback exists for users who manually create credentials.json or
whose OS keyring is unavailable.
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

SERVICE_NAMESPACE = "traceai"
CREDENTIALS_FILE = Path.home() / ".traceai" / "credentials.json"


class CredentialManager:
    """
    Credential manager with three-tier resolution:

      1. OS keychain (via keyring)
      2. ~/.traceai/credentials.json (file-based fallback)
      3. Returns None — caller should check config.settings as last resort

    Every credential is namespaced under ``traceai/<connector>/<key>``
    in the OS keychain so multiple connector instances can coexist.
    """

    def __init__(
        self,
        namespace: str = SERVICE_NAMESPACE,
        credentials_file: Path | None = None,
    ) -> None:
        self._namespace = namespace
        self._credentials_file = credentials_file or CREDENTIALS_FILE
        self._file_cache: dict | None = None

    def _build_service_name(self, connector: str, key: str) -> str:
        return f"{self._namespace}/{connector}/{key}"

    # ── File-based fallback ────────────────────────────────────────────────

    def _load_credentials_file(self) -> dict:
        """Load and cache ~/.traceai/credentials.json."""
        if self._file_cache is not None:
            return self._file_cache

        if not self._credentials_file.exists():
            self._file_cache = {}
            return self._file_cache

        try:
            data = json.loads(self._credentials_file.read_text(encoding="utf-8"))
            self._file_cache = data if isinstance(data, dict) else {}
            logger.info(
                "credentials_file_loaded",
                path=str(self._credentials_file),
                connectors=list(self._file_cache.keys()),
            )
        except Exception as exc:
            logger.warning(
                "credentials_file_load_failed",
                path=str(self._credentials_file),
                error=str(exc),
            )
            self._file_cache = {}

        return self._file_cache

    def _retrieve_from_file(self, connector: str, key: str) -> str | None:
        """Try to read a credential from the JSON file."""
        data = self._load_credentials_file()
        connector_data = data.get(connector, {})
        if isinstance(connector_data, dict):
            value = connector_data.get(key)
            if value:
                logger.debug(
                    "credential_from_file",
                    connector=connector,
                    key=key,
                )
                return str(value)
        return None

    # ── Public API ────────────────────────────────────────────────────────

    def store(self, connector: str, key: str, value: str) -> None:
        """Store a credential in the OS keychain."""
        try:
            import keyring
            service = self._build_service_name(connector, key)
            keyring.set_password(service, key, value)
            logger.info(
                "credential_stored",
                connector=connector,
                key=key,
                service=service,
            )
        except Exception as exc:
            logger.warning(
                "credential_store_failed",
                connector=connector,
                key=key,
                error=str(exc),
            )

    def retrieve(self, connector: str, key: str) -> str | None:
        """
        Retrieve a credential. Resolution order:
          1. OS keychain
          2. ~/.traceai/credentials.json
          3. None (caller should check config.settings)
        """
        # Tier 1: OS keychain
        try:
            import keyring
            service = self._build_service_name(connector, key)
            value = keyring.get_password(service, key)
            if value:
                logger.debug(
                    "credential_from_keyring",
                    connector=connector,
                    key=key,
                )
                return value
        except Exception as exc:
            logger.debug(
                "keyring_unavailable",
                connector=connector,
                key=key,
                error=str(exc),
            )

        # Tier 2: credentials.json file
        value = self._retrieve_from_file(connector, key)
        if value:
            return value

        logger.warning(
            "credential_not_found",
            connector=connector,
            key=key,
            checked=["keyring", "credentials.json"],
        )
        return None

    def delete(self, connector: str, key: str) -> bool:
        """Delete a credential from the OS keychain."""
        try:
            import keyring
            service = self._build_service_name(connector, key)
            keyring.delete_password(service, key)
            logger.info("credential_deleted", connector=connector, key=key)
            return True
        except Exception:
            logger.warning("credential_delete_failed", connector=connector, key=key)
            return False

    def exists(self, connector: str, key: str) -> bool:
        """Check whether a credential exists without returning its value."""
        return self.retrieve(connector, key) is not None

    def store_multiple(self, connector: str, credentials: dict[str, str]) -> None:
        """Store several credentials at once for a connector."""
        for key, value in credentials.items():
            self.store(connector, key, value)

    def retrieve_multiple(self, connector: str, keys: list[str]) -> dict[str, str | None]:
        """Retrieve several credentials at once."""
        return {key: self.retrieve(connector, key) for key in keys}


# Module-level singleton for convenience
credential_manager = CredentialManager()
