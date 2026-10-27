import shutil
from datetime import date
from pathlib import Path

import pytest
import yaml

from app.config import ConfigError
from app.yaml_config import AGENT_NAMES, load_config

pytestmark = pytest.mark.unit

REAL_CONFIG = Path(__file__).resolve().parents[2] / "config"


@pytest.fixture
def cfg_dir(tmp_path) -> Path:
    target = tmp_path / "config"
    shutil.copytree(REAL_CONFIG, target)
    return target


def edit(cfg_dir: Path, name: str, change) -> None:
    path = cfg_dir / name
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def load_error(cfg_dir: Path) -> str:
    with pytest.raises(ConfigError) as exc:
        load_config(cfg_dir)
    return str(exc.value)


# ---- real fayllar ----
def test_real_config_is_valid():
    cfg = load_config(REAL_CONFIG)
    assert "general" in {d.code for d in cfg.departments}
    assert set(cfg.agents.agents) == set(AGENT_NAMES)
    assert cfg.consent.version >= 1
    assert 2026 in cfg.holidays.complete_years


def test_official_2026_holidays_and_transferred_days_are_present():
    dates = load_config(REAL_CONFIG).holidays.dates
    # Nazirlər Kabinetinin №362 qərarı və 2026 istehsalat təqvimi (findings.md)
    for d in [date(2026, 1, 20), date(2026, 3, 9), date(2026, 3, 24), date(2026, 3, 30),
              date(2026, 5, 29), date(2026, 11, 10), date(2026, 12, 31)]:
        assert d in dates


def test_agent_model_lookup():
    cfg = load_config(REAL_CONFIG)
    assert cfg.agents.model_for("triage") == ("anthropic", "claude-haiku-5-5")
    assert cfg.agents.model_for("drafter") == ("anthropic", "claude-sonnet-5-5")
    # Claude 5.5 modellərində thinking defolt olaraq açıqdır: təsnifat agentləri üçün effort açıq yazılır
    assert cfg.agents.effort_for("router") == "low"


def test_invalid_effort(cfg_dir):
    edit(cfg_dir, "agents.yaml", lambda d: d["agents"]["qa"].update(effort="turbo"))
    assert "agents.yaml" in load_error(cfg_dir)


def test_template_render():
    text = load_config(REAL_CONFIG).templates.render(
        "appeal_registered", number="MR-2026-000123", due_date="11.11.2026"
    )
    assert "MR-2026-000123" in text and "11.11.2026" in text


# ---- şöbələr ----
def test_missing_general_department(cfg_dir):
    edit(cfg_dir, "departments.yaml",
         lambda d: d.update(departments=[x for x in d["departments"] if x["code"] != "general"]))
    msg = load_error(cfg_dir)
    assert "departments.yaml" in msg and "general" in msg


def test_duplicate_department_code(cfg_dir):
    edit(cfg_dir, "departments.yaml", lambda d: d["departments"].append(dict(d["departments"][0])))
    assert "təkrar" in load_error(cfg_dir)


def test_department_code_format(cfg_dir):
    edit(cfg_dir, "departments.yaml", lambda d: d["departments"][0].update(code="Kommunal Şöbə"))
    assert "departments.yaml" in load_error(cfg_dir)


# ---- agentlər ----
def test_agents_missing_and_unknown(cfg_dir):
    def change(d):
        d["agents"].pop("qa")
        d["agents"]["foo"] = {"model": "x"}
    edit(cfg_dir, "agents.yaml", change)
    msg = load_error(cfg_dir)
    assert "qa" in msg and "foo" in msg


def test_unknown_provider(cfg_dir):
    edit(cfg_dir, "agents.yaml", lambda d: d["agents"]["qa"].update(provider="gemini"))
    assert "agents.yaml" in load_error(cfg_dir)


# ---- SLA ----
def test_work_hours_start_after_end(cfg_dir):
    edit(cfg_dir, "sla.yaml", lambda d: d["work_hours"].update(start="19:00"))
    assert "sla.yaml" in load_error(cfg_dir)


def test_escalation_coordinator_must_come_before_head(cfg_dir):
    edit(cfg_dir, "sla.yaml", lambda d: d["escalation"].update(unassigned_head_work_hours=2))
    assert "sla.yaml" in load_error(cfg_dir)


def test_due_reminders_must_be_descending(cfg_dir):
    edit(cfg_dir, "sla.yaml", lambda d: d["escalation"].update(due_reminder_workdays=[1, 3]))
    assert "sla.yaml" in load_error(cfg_dir)


def test_invalid_timezone(cfg_dir):
    edit(cfg_dir, "sla.yaml", lambda d: d.update(timezone="Asia/Atlantis"))
    assert "sla.yaml" in load_error(cfg_dir)


# ---- təsdiq qaydaları ----
def test_unknown_category_in_approval_rules(cfg_dir):
    edit(cfg_dir, "approval_rules.yaml", lambda d: d["categories"].append("gossip"))
    assert "approval_rules.yaml" in load_error(cfg_dir)


# ---- bayramlar ----
def test_duplicate_holiday_date(cfg_dir):
    edit(cfg_dir, "holidays.yaml", lambda d: d["days"].append(dict(d["days"][0])))
    msg = load_error(cfg_dir)
    assert "holidays.yaml" in msg and "təkrar" in msg


# ---- şablonlar ----
def test_template_missing_key(cfg_dir):
    edit(cfg_dir, "templates.yaml", lambda d: d.pop("appeal_registered"))
    assert "appeal_registered" in load_error(cfg_dir)


def test_template_unknown_key(cfg_dir):
    edit(cfg_dir, "templates.yaml", lambda d: d.update(appeal_registred="yazı səhvi"))
    assert "appeal_registred" in load_error(cfg_dir)


def test_template_unknown_placeholder(cfg_dir):
    edit(cfg_dir, "templates.yaml", lambda d: d.update(appeal_registered="Nömrə: {numbr}"))
    msg = load_error(cfg_dir)
    assert "appeal_registered" in msg and "numbr" in msg


# ---- razılıq və fayllar ----
def test_consent_without_version(cfg_dir):
    path = cfg_dir / "consent.md"
    text = path.read_text(encoding="utf-8")
    path.write_text("\n".join(text.splitlines()[1:]), encoding="utf-8")
    assert "consent.md" in load_error(cfg_dir)


def test_missing_file(cfg_dir):
    (cfg_dir / "sla.yaml").unlink()
    assert "sla.yaml" in load_error(cfg_dir)
