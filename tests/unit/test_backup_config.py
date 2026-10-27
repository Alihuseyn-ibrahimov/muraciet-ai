"""Ehtiyat nüsxə: servis şifrə ilə işləyir, port açmır, nüsxələr və parol git-ə düşmür."""
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def compose(name: str) -> dict:
    return yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))


def test_backup_service_is_encrypted_and_closed():
    service = compose("docker-compose.yml")["services"]["backup"]
    assert "ports" not in service and service["secrets"] == ["backup_passphrase"]
    prod = compose("docker-compose.prod.yml")["services"]["backup"]
    assert set(prod["secrets"]) == {"backup_passphrase", "postgres_password"} and prod["environment"]["PGPASSWORD"] == ""

    script = (ROOT / "scripts" / "backup.sh").read_text(encoding="utf-8")
    assert "set -eu" in script and "openssl enc -aes-256-cbc -pbkdf2" in script and "\r\n" not in script


def test_backups_and_passphrase_are_ignored_by_git():
    result = subprocess.run(["git", "check-ignore", "backups/x.dump.enc", "secrets/backup_passphrase"], cwd=ROOT,
                            capture_output=True, text=True)
    assert result.stdout.split() == ["backups/x.dump.enc", "secrets/backup_passphrase"]
