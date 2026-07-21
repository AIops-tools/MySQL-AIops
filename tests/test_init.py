"""Tests for the ``mysql-aiops init`` onboarding wizard.

The wizard is driven end-to-end through Typer's CliRunner with every path
(config.yaml, secrets.enc) isolated under tmp_path. The master
password comes from MYSQL_AIOPS_MASTER_PASSWORD (the non-interactive path)
and the hidden target-password prompt is patched at the getpass boundary.
"""

from __future__ import annotations

import getpass as getpass_mod

import pytest
import yaml
from typer.testing import CliRunner

import mysql_aiops.cli.init as init_mod
import mysql_aiops.config as config_mod
import mysql_aiops.doctor as doctor_mod
import mysql_aiops.secretstore as ss

MASTER_PW = "init-master-pw"
DB_PW = "db-account-pa55word"

# Wizard answers: name, host, then accept defaults for port/database/user/
# ssl_mode, no second target, decline the trailing doctor run.
WIZARD_INPUT = "primary\ndb1.example.com\n\n\n\n\nn\nn\n"


@pytest.fixture
def init_home(tmp_path, monkeypatch):
    """Isolate config + secret store + governance home under tmp_path."""
    config_file = tmp_path / "config.yaml"
    secrets_file = tmp_path / "secrets.enc"
    monkeypatch.setenv("MYSQL_AIOPS_HOME", str(tmp_path))
    monkeypatch.setenv("MYSQL_AIOPS_MASTER_PASSWORD", MASTER_PW)
    monkeypatch.setattr(init_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(init_mod, "CONFIG_FILE", config_file)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_mod, "CONFIG_FILE", config_file)
    monkeypatch.setattr(ss, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(ss, "SECRETS_FILE", secrets_file)
    monkeypatch.setattr(ss, "LEGACY_ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(ss, "_cached", None)
    # The hidden per-target password prompt bypasses CliRunner stdin.
    monkeypatch.setattr(getpass_mod, "getpass", lambda prompt="": DB_PW)
    return tmp_path


def _run_init(input_text: str = WIZARD_INPUT):
    from mysql_aiops.cli import app

    return CliRunner().invoke(app, ["init"], input=input_text)


@pytest.mark.unit
def test_init_writes_config_with_entered_values(init_home):
    result = _run_init()
    assert result.exit_code == 0, result.output
    raw = yaml.safe_load((init_home / "config.yaml").read_text("utf-8"))
    assert raw["targets"] == [
        {
            "name": "primary",
            "host": "db1.example.com",
            "port": 3306,
            "database": "mysql",
            "user": "root",
            "ssl_mode": "preferred",  # accepted default must land in config
        }
    ]


@pytest.mark.unit
def test_init_tls_default_is_preferred_with_lab_hint(init_home):
    result = _run_init()
    assert result.exit_code == 0, result.output
    assert "preferred" in result.output
    assert "lab" in result.output  # the "disabled only for a lab" hint


@pytest.mark.unit
def test_init_verify_ca_prompts_for_ca_path(init_home):
    # name, host, port, database, user, ssl_mode=verify_ca, ca path, no more, no doctor
    result = _run_init("primary\ndb1.example.com\n\n\n\nverify_ca\n/etc/ssl/ca.pem\nn\nn\n")
    assert result.exit_code == 0, result.output
    raw = yaml.safe_load((init_home / "config.yaml").read_text("utf-8"))
    assert raw["targets"][0]["ssl_mode"] == "verify_ca"
    assert raw["targets"][0]["ssl_ca"] == "/etc/ssl/ca.pem"


@pytest.mark.unit
def test_init_stores_secret_encrypted_not_in_config(init_home):
    result = _run_init()
    assert result.exit_code == 0, result.output
    # Password is readable back through the secret store API...
    assert ss.SecretStore.unlock(MASTER_PW).get("primary") == DB_PW
    # ...and never lands in plaintext in config.yaml or secrets.enc.
    assert DB_PW not in (init_home / "config.yaml").read_text("utf-8")
    assert DB_PW not in (init_home / "secrets.enc").read_text("utf-8")


@pytest.mark.unit
def test_init_writes_no_policy_rules(init_home):
    """The skill no longer authorizes, so init seeds no rules.yaml — a fresh
    install delivers full functionality and leaves permission to the account."""
    result = _run_init()
    assert result.exit_code == 0, result.output
    assert not (init_home / "rules.yaml").exists()


@pytest.mark.unit
def test_init_accepting_doctor_confirm_runs_doctor(init_home, monkeypatch):
    calls: list[bool] = []
    monkeypatch.setattr(doctor_mod, "run_doctor", lambda: calls.append(True) or 0)
    # Empty last answer accepts the confirm's default=True.
    result = _run_init("primary\ndb1.example.com\n\n\n\n\nn\n\n")
    assert result.exit_code == 0, result.output
    assert calls == [True]


@pytest.mark.unit
def test_init_overwrite_existing_target(init_home):
    result = _run_init()
    assert result.exit_code == 0, result.output
    # Same name again: confirm overwrite, new host, accept defaults.
    result = _run_init("primary\ny\ndb2.example.com\n\n\n\n\nn\nn\n")
    assert result.exit_code == 0, result.output
    raw = yaml.safe_load((init_home / "config.yaml").read_text("utf-8"))
    assert [t["host"] for t in raw["targets"]] == ["db2.example.com"]
