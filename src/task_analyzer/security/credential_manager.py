"""
Security — Credential management using OS keychain.

All secrets (API tokens, passwords, connection strings) are stored in the
operating system's native credential store via the `keyring` library:

  - macOS  → Keychain
  - Windows → Windows Credential Locker
  - Linux  → Secret Service (GNOME Keyring / KWallet)

No secret is ever written to disk in plaintext.
"""

from __future__ import annotations

import keyring
import structlog

logger = structlog.get_logger(__name__)

SERVICE_NAMESPACE = "traceai"


class CredentialManager:
    """
    Thin wrapper around the OS keychain for storing and retrieving secrets.

    Every credential is namespaced under ``traceai/<connector>/<key>``
    so multiple connector instances can coexist without collision.
    """

    def __init__(self, namespace: str = SERVICE_NAMESPACE) -> None:
        self._namespace = namespace

    def _build_service_name(self, connector: str, key: str) -> str:
        return f"{self._namespace}/{connector}/{key}"

    # ── Public API ────────────────────────────────────────────────────────

    def store(self, connector: str, key: str, value: str) -> None:
        """Store a credential in the OS keychain."""
        service = self._build_service_name(connector, key)
        keyring.set_password(service, key, value)
        logger.info(
            "credential_stored",
            connector=connector,
            key=key,
            service=service,
        )

    def retrieve(self, connector: str, key: str) -> str | None:
        """Retrieve a credential from the OS keychain. Returns None if missing."""
        service = self._build_service_name(connector, key)
        value = keyring.get_password(service, key)
        if value is None:
            logger.warning("credential_not_found", connector=connector, key=key)
        return value

    def delete(self, connector: str, key: str) -> bool:
        """Delete a credential from the OS keychain."""
        service = self._build_service_name(connector, key)
        try:
            keyring.delete_password(service, key)
            logger.info("credential_deleted", connector=connector, key=key)
            return True
        except keyring.errors.PasswordDeleteError:
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
