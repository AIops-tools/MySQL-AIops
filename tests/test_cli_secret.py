"""CLI ``secret`` command bodies (set / list / rm / migrate / rotate-password).

The secret store is redirected at ``mysql_aiops.secretstore``'s path constants
so nothing touches the real ``~/.mysql-aiops``; the master password is supplied
via the env var so ``resolve_master_password`` never blocks on ``getpass``.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

import mysql_aiops.secretstore as ss
from mysql_aiops.cli import app

runner = CliRunner()


@pytest.fixture
def secret_home(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(ss, "SECRETS_FILE", tmp_path / "secrets.enc")
    monkeypatch.setattr(ss, "LEGACY_ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(ss, "_cached", None)
    monkeypatch.setenv(ss.MASTER_PASSWORD_ENV, "master-pw")
    return tmp_path


@pytest.mark.unit
def test_secret_set_stores_encrypted(secret_home):
    result = runner.invoke(app, ["secret", "set", "primary", "--value", "s3cr3t"])
    assert result.exit_code == 0, result.output
    assert "Stored encrypted password for 'primary'" in result.output
    blob = (secret_home / "secrets.enc").read_text()
    assert "s3cr3t" not in blob and "ciphertext" in blob


@pytest.mark.unit
def test_secret_list_shows_names_only(secret_home):
    ss.SecretStore.unlock("master-pw").set("primary", "v").set("replica", "w")
    result = runner.invoke(app, ["secret", "list"])
    assert result.exit_code == 0, result.output
    assert "primary" in result.output and "replica" in result.output
    assert "v" not in result.output.replace("replica", "")  # value never shown


@pytest.mark.unit
def test_secret_list_empty_hint(secret_home):
    result = runner.invoke(app, ["secret", "list"])
    assert result.exit_code == 0, result.output
    assert "No secrets stored yet" in result.output


@pytest.mark.unit
def test_secret_rm(secret_home):
    ss.SecretStore.unlock("master-pw").set("primary", "v")
    result = runner.invoke(app, ["secret", "rm", "primary"])
    assert result.exit_code == 0, result.output
    assert "Deleted password for 'primary'" in result.output
    assert ss.SecretStore.unlock("master-pw").names() == ()


@pytest.mark.unit
def test_secret_rm_missing_raises_secret_store_error(secret_home):
    ss.SecretStore.unlock("master-pw").set("primary", "v")
    result = runner.invoke(app, ["secret", "rm", "ghost"])
    assert result.exit_code == 1
    # SecretStoreError is not in the cli_errors passthrough set, so it surfaces
    # as the raised exception rather than a red one-liner.
    assert isinstance(result.exception, ss.SecretStoreError)
    assert "ghost" in str(result.exception)


@pytest.mark.unit
def test_secret_migrate_imports_from_legacy_env(secret_home):
    (secret_home / ".env").write_text("MYSQL_PRIMARY_PASSWORD=legacy-pass\n")
    result = runner.invoke(app, ["secret", "migrate"])
    assert result.exit_code == 0, result.output
    assert "Imported 1 secret(s): primary" in result.output
    assert ss.SecretStore.unlock("master-pw").get("primary") == "legacy-pass"


@pytest.mark.unit
def test_secret_migrate_nothing_to_do(secret_home):
    result = runner.invoke(app, ["secret", "migrate"])
    assert result.exit_code == 0, result.output
    assert "Nothing to migrate" in result.output


@pytest.mark.unit
def test_secret_rotate_password_reencrypts(secret_home, monkeypatch):
    ss.SecretStore.unlock("master-pw").set("primary", "v")
    monkeypatch.setattr("mysql_aiops.cli.secret.getpass.getpass", lambda prompt="": "new-pw")
    result = runner.invoke(app, ["secret", "rotate-password"])
    assert result.exit_code == 0, result.output
    assert "Master password rotated" in result.output
    assert ss.SecretStore.unlock("new-pw").get("primary") == "v"


@pytest.mark.unit
def test_secret_rotate_password_mismatch_aborts(secret_home, monkeypatch):
    ss.SecretStore.unlock("master-pw").set("primary", "v")
    answers = iter(["new-pw", "different-pw"])
    monkeypatch.setattr(
        "mysql_aiops.cli.secret.getpass.getpass", lambda prompt="": next(answers)
    )
    result = runner.invoke(app, ["secret", "rotate-password"])
    assert result.exit_code == 1
    assert "did not match" in result.output
