"""YAML konfiqurasiyası (config/): kodu dəyişmədən idarə olunan parametrlər (spec 12-ci bölmə).

Hər fayl yüklənəndə Pydantic ilə yoxlanılır. Xəta olarsa sistem işə düşmür və mesajda faylın
adı ilə problem göstərilir. Səhv konfiqurasiya ilə səssizcə işləməkdənsə dayanmaq daha yaxşıdır.
"""
import datetime as dt
import re
import string
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveFloat,
    PositiveInt,
    ValidationError,
    field_validator,
    model_validator,
)

from app.config import ConfigError, settings
from app.workflow.states import Category, Outcome, Priority

PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENT_NAMES = ("guard", "router", "faq", "intake", "triage", "routing", "drafter", "qa", "curator", "analyst")
Provider = Literal["anthropic", "openai", "ollama", "vllm"]
Effort = Literal["low", "medium", "high", "xhigh", "max"]
Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAY_INDEX = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

# Hər şablonda icazə verilən yer tutucular. Yeni şablon əlavə olunanda bura da yazılır.
TEMPLATE_FIELDS: dict[str, frozenset[str]] = {
    "greeting": frozenset({"org_name"}),
    "off_topic": frozenset({"org_name"}),
    "blocked": frozenset({"org_name"}),
    "human_handoff": frozenset({"hotline"}),
    "rate_limited": frozenset(),
    "llm_unavailable": frozenset(),
    "no_answer_offer_appeal": frozenset(),
    "consent_prompt": frozenset(),
    "consent_declined": frozenset(),
    "appeal_registered": frozenset({"number", "due_date"}),
    "staff_question": frozenset({"number", "question"}),
    "waiting_reminder": frozenset({"number", "question"}),
    "official_answer": frozenset({"number", "answer", "signature"}),
    "answer_signature": frozenset({"org_name"}),
    "appeal_procedure": frozenset(),
    "extension_notice": frozenset({"number", "due_date", "reason"}),
    "redirect_notice": frozenset({"number", "target", "sent_date", "letter_ref"}),
    "not_reviewed_notice": frozenset({"number", "reason", "appeal_procedure"}),
    "reopened": frozenset({"number"}),
    "status_line": frozenset({"number", "subject", "status", "due_date"}),
    "no_appeals": frozenset(),
    "feedback_thanks": frozenset(),
    # müraciət formu (Faza 6)
    "ask_phone": frozenset(),
    "contact_not_own": frozenset(),
    "ask_name": frozenset(),
    "invalid_name": frozenset(),
    "ask_fin": frozenset(),
    "invalid_fin": frozenset(),
    "ask_address": frozenset(),
    "invalid_address": frozenset(),
    "confirm_profile": frozenset({"profile"}),
    "confirm_appeal": frozenset({"subject", "summary", "profile"}),
    "appeal_cancelled": frozenset(),
    "registration_failed": frozenset(),
    "my_data": frozenset({"profile"}),
    "no_profile": frozenset(),
    "choose_appeal": frozenset(),
    "reply_forwarded": frozenset({"number"}),
    "question_closed": frozenset({"number"}),
    "feedback_reason_prompt": frozenset(),
    "dismissed": frozenset(),
    "use_buttons": frozenset(),
    "web_use_telegram": frozenset({"bot"}),
    "text_only": frozenset(),
    "attachment_saved": frozenset({"count", "limit"}),
    "attachment_limit": frozenset({"limit"}),
    "attachment_type": frozenset(),
    "attachment_too_big": frozenset({"mb"}),
    "attachment_failed": frozenset(),
    "live_chat_waiting": frozenset(),
    "live_chat_connected": frozenset(),
    "live_chat_closed": frozenset(),
    "live_chat_expired": frozenset({"hotline"}),
    "live_chat_offline": frozenset({"hotline"}),
    "live_chat_text_only": frozenset(),
    "human_handoff_live": frozenset(),
    "describe_problem": frozenset(),
}
CONSENT_FIELDS = frozenset({"org_name"})


def _placeholders(text: str) -> set[str]:
    return {field for _, field, _, _ in string.Formatter().parse(text) if field}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------- departments.yaml ----------
class Department(_Strict):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1)
    competence: str = Field(min_length=1)
    active: bool = True


class _DepartmentsFile(_Strict):
    departments: list[Department]

    @model_validator(mode="after")
    def _check(self):
        codes = [d.code for d in self.departments]
        duplicates = sorted({c for c in codes if codes.count(c) > 1})
        if duplicates:
            raise ValueError(f"təkrar şöbə kodu: {', '.join(duplicates)}")
        general = next((d for d in self.departments if d.code == "general"), None)
        if general is None or not general.active:
            raise ValueError("'general' (Ümumi) şöbəsi mütləq olmalı və aktiv olmalıdır")
        return self


# ---------- approval_rules.yaml ----------
class ApprovalRules(_Strict):
    """Cavab bu şərtlərdən BİRİ ödənəndə koordinator təsdiqinə düşür (spec 7-ci bölmə)."""

    categories: list[Category] = []
    priorities: list[Priority] = []
    outcomes: list[Outcome] = []
    legal_issue: bool = True
    qa_critical: bool = True
    agent_failure: bool = True


# ---------- sla.yaml ----------
class WorkHours(_Strict):
    start: dt.time
    end: dt.time

    @field_validator("start", "end", mode="before")
    @classmethod
    def _quoted(cls, value: Any):
        # PyYAML dırnaqsız 18:00-ı 60-lıq say kimi oxuyur (1080), Pydantic isə onu səhv vaxta çevirərdi
        if not isinstance(value, str | dt.time):
            raise ValueError('vaxtı dırnaq içində yazın, məsələn: "18:00"')
        return value

    @model_validator(mode="after")
    def _order(self):
        if self.start >= self.end:
            raise ValueError("iş gününün başlanğıcı sonundan əvvəl olmalıdır")
        return self


class LegalTerms(_Strict):
    review_workdays: PositiveInt  # m. 10.1
    max_extension_workdays: PositiveInt  # m. 10.2
    urgent_hours: PositiveInt  # m. 10.4
    redirect_workdays: PositiveInt  # m. 7.10
    not_reviewed_notice_workdays: PositiveInt  # m. 8.2
    count_receipt_day: bool = True  # m. 10.5: müddət daxil olduğu gündən başlayır


class Escalation(_Strict):
    unassigned_coordinator_work_hours: PositiveFloat
    unassigned_head_work_hours: PositiveFloat
    urgent_unassigned_coordinator_hours: PositiveFloat
    urgent_unassigned_head_hours: PositiveFloat
    due_reminder_workdays: list[PositiveInt] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self):
        if self.unassigned_coordinator_work_hours >= self.unassigned_head_work_hours:
            raise ValueError("koordinatora xəbərdarlıq rəhbərə eskalasiyadan əvvəl olmalıdır")
        if self.urgent_unassigned_coordinator_hours >= self.urgent_unassigned_head_hours:
            raise ValueError("təcili müraciətdə koordinatora xəbərdarlıq rəhbərə eskalasiyadan əvvəl olmalıdır")
        days = self.due_reminder_workdays
        if any(a <= b for a, b in zip(days, days[1:])):
            raise ValueError("due_reminder_workdays azalan sırada olmalıdır, məsələn [3, 1]")
        return self


class CitizenTimers(_Strict):
    waiting_reminder_workdays: PositiveInt
    waiting_staff_prompt_workdays: PositiveInt
    auto_close_days: PositiveInt

    @model_validator(mode="after")
    def _check(self):
        if self.waiting_staff_prompt_workdays <= self.waiting_reminder_workdays:
            raise ValueError("icraçıya təklif vətəndaşa xatırlatmadan sonra olmalıdır")
        return self


class OutboxConfig(_Strict):
    retry_delays_seconds: list[PositiveInt] = Field(min_length=1)
    stale_sending_minutes: PositiveInt


class SessionConfig(_Strict):
    citizen_ttl_hours: PositiveInt
    staff_ttl_minutes: PositiveInt


class SlaConfig(_Strict):
    timezone: str
    work_hours: WorkHours
    work_days: list[Weekday] = Field(min_length=1)
    legal: LegalTerms
    escalation: Escalation
    citizen: CitizenTimers
    outbox: OutboxConfig
    sessions: SessionConfig

    @field_validator("timezone")
    @classmethod
    def _tz(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"naməlum vaxt zonası: {value}") from None
        return value

    @field_validator("work_days")
    @classmethod
    def _unique_days(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("iş günləri təkrarlanmamalıdır")
        return value

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def workday_indexes(self) -> frozenset[int]:
        return frozenset(WEEKDAY_INDEX[d] for d in self.work_days)


# ---------- holidays.yaml ----------
class Holiday(_Strict):
    date: dt.date
    name: str = Field(min_length=1)


class HolidayCalendar(_Strict):
    complete_years: list[int] = []  # bütün bayramları və yerdəyişmələri rəsmi təsdiqlənmiş illər
    days: list[Holiday]

    @model_validator(mode="after")
    def _unique(self):
        dates = [h.date for h in self.days]
        duplicates = sorted({d for d in dates if dates.count(d) > 1})
        if duplicates:
            raise ValueError(f"təkrar tarix: {', '.join(d.isoformat() for d in duplicates)}")
        return self

    @property
    def dates(self) -> frozenset[dt.date]:
        return frozenset(h.date for h in self.days)


# ---------- agents.yaml ----------
class AgentModel(_Strict):
    provider: Provider | None = None
    model: str = Field(min_length=1)
    effort: Effort | None = None  # yalnız anthropic: output_config.effort, verilməsə modelin öz standartı


class AgentsConfig(_Strict):
    default_provider: Provider = "anthropic"
    agents: dict[str, AgentModel]

    @model_validator(mode="after")
    def _all_agents(self):
        missing = sorted(set(AGENT_NAMES) - set(self.agents))
        unknown = sorted(set(self.agents) - set(AGENT_NAMES))
        if missing or unknown:
            raise ValueError(
                f"çatışmayan agentlər: {', '.join(missing) or '-'}; naməlum agentlər: {', '.join(unknown) or '-'}"
            )
        return self

    def model_for(self, agent: str) -> tuple[str, str]:
        cfg = self.agents[agent]
        return cfg.provider or self.default_provider, cfg.model

    def effort_for(self, agent: str) -> str | None:
        return self.agents[agent].effort


# ---------- templates.yaml ----------
class Templates(_Strict):
    texts: dict[str, str]

    @model_validator(mode="after")
    def _check(self):
        problems = []
        missing = sorted(TEMPLATE_FIELDS.keys() - self.texts.keys())
        unknown = sorted(self.texts.keys() - TEMPLATE_FIELDS.keys())
        if missing:
            problems.append(f"çatışmayan şablonlar: {', '.join(missing)}")
        if unknown:
            problems.append(f"naməlum şablon açarları: {', '.join(unknown)}")
        for key, text in self.texts.items():
            if key not in TEMPLATE_FIELDS:
                continue
            try:
                bad = _placeholders(text) - TEMPLATE_FIELDS[key]
            except ValueError as e:  # bağlanmamış { və s.
                problems.append(f"{key}: şablon sintaksisi yanlışdır ({e})")
                continue
            if bad:
                allowed = ", ".join(sorted(TEMPLATE_FIELDS[key])) or "yoxdur"
                problems.append(f"{key}: icazəsiz yer tutucu {sorted(bad)} (icazəlilər: {allowed})")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def render(self, key: str, **values: Any) -> str:
        return self.texts[key].format(**values)


# ---------- consent.md ----------
class Consent(_Strict):
    version: PositiveInt
    text: str = Field(min_length=50)

    @model_validator(mode="after")
    def _check(self):
        bad = _placeholders(self.text) - CONSENT_FIELDS
        if bad:
            raise ValueError(f"icazəsiz yer tutucu {sorted(bad)} (icazəli: org_name)")
        return self

    def render(self, org_name: str) -> str:
        return self.text.format(org_name=org_name)


_VERSION_RE = re.compile(r"^<!--\s*version:\s*(\d+)\s*-->\s*$")


# ---------- hamısı birlikdə ----------
class AppConfig(BaseModel):
    departments: list[Department]
    approval: ApprovalRules
    sla: SlaConfig
    holidays: HolidayCalendar
    agents: AgentsConfig
    templates: Templates
    consent: Consent

    def department(self, code: str) -> Department | None:
        return next((d for d in self.departments if d.code == code), None)


def _format_errors(error: ValidationError) -> str:
    parts = []
    for item in error.errors():
        loc = ".".join(str(p) for p in item["loc"])
        msg = item["msg"].removeprefix("Value error, ")
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(parts)


def _read(directory: Path, name: str) -> str:
    path = directory / name
    if not path.is_file():
        raise ConfigError(f"{name}: fayl tapılmadı ({path})")
    return path.read_text(encoding="utf-8")


def _yaml(directory: Path, name: str) -> Any:
    try:
        return yaml.safe_load(_read(directory, name)) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"{name}: YAML sintaksis xətası: {e}") from None


def _parse(model: type[BaseModel], data: Any, name: str):
    try:
        return model.model_validate(data)
    except ValidationError as e:
        raise ConfigError(f"{name}: {_format_errors(e)}") from None


def _consent(directory: Path) -> Consent:
    lines = _read(directory, "consent.md").splitlines()
    match = _VERSION_RE.match(lines[0]) if lines else None
    if not match:
        raise ConfigError("consent.md: birinci sətir <!-- version: N --> formatında olmalıdır")
    return _parse(Consent, {"version": int(match.group(1)), "text": "\n".join(lines[1:]).strip()}, "consent.md")


def load_config(config_dir: Path | str | None = None) -> AppConfig:
    directory = Path(config_dir or settings.config_dir)
    if not directory.is_absolute():
        directory = PROJECT_ROOT / directory
    return AppConfig(
        departments=_parse(_DepartmentsFile, _yaml(directory, "departments.yaml"), "departments.yaml").departments,
        approval=_parse(ApprovalRules, _yaml(directory, "approval_rules.yaml"), "approval_rules.yaml"),
        sla=_parse(SlaConfig, _yaml(directory, "sla.yaml"), "sla.yaml"),
        holidays=_parse(HolidayCalendar, _yaml(directory, "holidays.yaml"), "holidays.yaml"),
        agents=_parse(AgentsConfig, _yaml(directory, "agents.yaml"), "agents.yaml"),
        templates=_parse(Templates, {"texts": _yaml(directory, "templates.yaml")}, "templates.yaml"),
        consent=_consent(directory),
    )


@lru_cache
def get_config() -> AppConfig:
    return load_config()
