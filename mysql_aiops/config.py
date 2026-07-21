"""Configuration management for MySQL AIops.

Loads connection targets from a YAML config file. The secret (the MySQL
account **password**) is NEVER stored in the config file and never on disk in
plaintext: it lives in the encrypted store ``~/.mysql-aiops/secrets.enc``
(see :mod:`mysql_aiops.secretstore`). For backward compatibility a legacy
plaintext env var (``MYSQL_<TARGET>_PASSWORD``) is still honoured as a fallback,
with a warning nudging migration to the encrypted store.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from mysql_aiops.governance.paths import ops_home
from mysql_aiops.secretstore import (
    MasterPasswordError,
    SecretStoreError,
    get_secret,
    has_store,
)

CONFIG_DIR = ops_home()
CONFIG_FILE = CONFIG_DIR / "config.yaml"
ENV_FILE = CONFIG_DIR / ".env"

DEFAULT_PORT = 3306
DEFAULT_DATABASE = "mysql"
DEFAULT_USER = "root"
DEFAULT_SSL_MODE = "preferred"
PROGRAM_NAME = "mysql-aiops"

# ssl_mode follows MySQL client semantics; each maps to PyMySQL ssl kwargs.
SSL_MODES = ("disabled", "preferred", "required", "verify_ca", "verify_identity")

# Legacy env-var prefix/suffix; also used by the migration helper.
SECRET_ENV_PREFIX = "MYSQL_"  # nosec B105 — env-var name, not a secret
SECRET_ENV_SUFFIX = "_PASSWORD"  # nosec B105 — env-var name, not a secret

_log = logging.getLogger("mysql-aiops.config")


def _secret_env_key(name: str) -> str:
    """Legacy per-target password env var name, e.g. MYSQL_PRIMARY_PASSWORD."""
    return f"{SECRET_ENV_PREFIX}{name.upper().replace('-', '_')}{SECRET_ENV_SUFFIX}"


def _resolve_secret(name: str) -> str:
    """Return a target's password: encrypted store first, then legacy env var."""
    if has_store():
        try:
            return get_secret(name)
        except MasterPasswordError:
            # A wrong or missing master password is NOT "this target has no
            # secret". Falling through resurfaced it as "No API key for target
            # X", sending the operator to add a credential that is already
            # there. MasterPasswordError subclasses SecretStoreError, so the
            # broad catch below would swallow it — re-raise first.
            raise
        except SecretStoreError:
            pass  # no secret stored for this target — try the legacy env var
    legacy = os.environ.get(_secret_env_key(name))
    if legacy:
        _log.warning(
            "Using plaintext env var %s. Migrate to the encrypted store with "
            "'mysql-aiops secret migrate'.",
            _secret_env_key(name),
        )
        return legacy
    raise OSError(
        f"No password for target '{name}'. Add one with "
        f"'mysql-aiops secret set {name}' (stored encrypted), or run "
        f"'mysql-aiops init'."
    )


def _ssl_kwargs(ssl_mode: str, ssl_ca: str | None) -> dict:
    """Map an ssl_mode to PyMySQL TLS keyword args.

    disabled → TLS off; preferred → negotiate (PyMySQL default); required →
    force TLS without certificate verification; verify_ca / verify_identity →
    force TLS and verify the server certificate (identity also checks the
    hostname) against ``ssl_ca``.
    """
    mode = (ssl_mode or DEFAULT_SSL_MODE).lower()
    if mode not in SSL_MODES:
        raise ValueError(f"Unknown ssl_mode '{ssl_mode}'. Allowed: {', '.join(SSL_MODES)}.")
    if mode == "disabled":
        return {"ssl_disabled": True}
    if mode == "preferred":
        return {}
    if mode == "required":
        # A provided ssl dict forces TLS; no CA → no certificate verification.
        return {"ssl": {}}
    if not ssl_ca:
        raise ValueError(f"ssl_mode '{mode}' requires 'ssl_ca' (path to the CA certificate).")
    kwargs: dict = {"ssl_ca": ssl_ca, "ssl_verify_cert": True}
    if mode == "verify_identity":
        kwargs["ssl_verify_identity"] = True
    return kwargs


@dataclass(frozen=True)
class TargetConfig:
    """A connection target for a MySQL / MariaDB server.

    The password is sourced from the encrypted secret store (see ``password``),
    never the config file. ``host``/``port`` locate the server; ``database`` is
    the default schema; ``ssl_mode`` follows MySQL client semantics
    (disabled/preferred/required/verify_ca/verify_identity).
    """

    name: str
    host: str
    port: int = DEFAULT_PORT
    database: str = DEFAULT_DATABASE
    user: str = DEFAULT_USER
    ssl_mode: str = DEFAULT_SSL_MODE
    ssl_ca: str | None = None

    @property
    def password(self) -> str:
        return _resolve_secret(self.name)

    @property
    def conn_kwargs(self) -> dict:
        """Connection keyword args for ``pymysql.connect`` (incl. password)."""
        kwargs = {
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "user": self.user,
            "password": self.password,
            "program_name": PROGRAM_NAME,
        }
        kwargs.update(_ssl_kwargs(self.ssl_mode, self.ssl_ca))
        return kwargs

    @property
    def dsn_redacted(self) -> str:
        """A human-readable DSN with the password redacted (for logs/doctor)."""
        return (
            f"mysql://{self.user}:***@{self.host}:{self.port}/"
            f"{self.database}?ssl_mode={self.ssl_mode}"
        )


@dataclass(frozen=True)
class AppConfig:
    """Top-level application config."""

    targets: tuple[TargetConfig, ...] = ()

    def get_target(self, name: str) -> TargetConfig:
        for t in self.targets:
            if t.name == name:
                return t
        available = ", ".join(t.name for t in self.targets) or "(none)"
        raise KeyError(f"Target '{name}' not found. Available: {available}")

    @property
    def default_target(self) -> TargetConfig:
        if not self.targets:
            raise ValueError("No targets configured. Check config.yaml")
        return self.targets[0]


def load_config(config_path: Path | None = None) -> AppConfig:
    """Load config from YAML; the password comes from the encrypted store."""
    path = config_path or CONFIG_FILE
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}\n"
            f"Run 'mysql-aiops init' to set up a target and store its password "
            f"encrypted, or create {CONFIG_FILE} with a 'targets' list."
        )

    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    targets = tuple(
        TargetConfig(
            name=t["name"],
            host=t["host"],
            port=t.get("port", DEFAULT_PORT),
            database=t.get("database", DEFAULT_DATABASE),
            user=t.get("user", DEFAULT_USER),
            ssl_mode=t.get("ssl_mode", DEFAULT_SSL_MODE),
            ssl_ca=t.get("ssl_ca"),
        )
        for t in raw.get("targets", [])
    )

    return AppConfig(targets=targets)
