"""Production konfiqurasiyası: .env tələb olunmur, port açılmır, deploy skripti bütün sirləri yoxlayır."""
import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def load(name: str) -> dict:
    return yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))


def test_dev_env_file_is_optional_so_prod_server_needs_only_env_prod():
    app = load("docker-compose.yml")["x-app"]
    assert app["env_file"] == [{"path": ".env", "required": False}]


def test_no_service_publishes_ports_outside_the_dev_override():
    for name in ("docker-compose.yml", "docker-compose.prod.yml"):
        services = load(name)["services"]
        assert not [s for s, cfg in services.items() if cfg and "ports" in cfg], name


def test_deploy_script_checks_every_production_secret():
    script = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    listed = set(re.search(r'SECRETS="([^"]+)"', script).group(1).split())
    assert listed == set(load("docker-compose.prod.yml")["secrets"]) | {"backup_passphrase"}
    assert "set -eu" in script and "\r\n" not in script and "python -m app.manage preflight" in script
