"""İnteqrasiya testləri üçün ortaq məlumatlar: qurum (şöbələr, əməkdaşlar), vətəndaşlar, saat."""
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import manage
from app.db.tables import Citizen, Department, Staff
from app.privacy.crypto import Crypto
from app.workflow.authz import Actor
from app.workflow.calendar import WorkCalendar
from app.workflow.states import StaffRole
from app.yaml_config import load_config

BAKU = ZoneInfo("Asia/Baku")
MONDAY_10 = datetime(2026, 10, 19, 10, 0, tzinfo=BAKU)
CFG = load_config()
CAL = WorkCalendar(CFG.sla, CFG.holidays.dates, CFG.holidays.complete_years)
CRYPTO = Crypto([Fernet.generate_key().decode()], hmac_key="test-hmac")
ORG = "Test Qurumu"

# Telegram chat id-ləri (şəxsi çatda chat_id = istifadəçinin Telegram ID-si)
COORDINATOR_TG, DEPUTY_TG, STAFF_A_TG, STAFF_B_TG, HEAD_TG, OFF_COORD_TG = 100, 101, 102, 103, 104, 105
CITIZEN_TG, OTHER_CITIZEN_TG = 5001, 5002


class Clock:
    def __init__(self, moment: datetime):
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment


@dataclass
class World:
    coordinator: Actor
    deputy: Actor
    staff_a: Actor
    staff_b: Actor
    head: Actor
    citizen_id: int
    other_citizen_id: int
    pensiya_id: int


def staff_actor(row: Staff) -> Actor:
    return Actor.staff(row.id, row.role, is_admin=row.is_admin, is_active=row.is_active)


def complete_citizen(telegram_user_id: int, **overrides) -> Citizen:
    fields = dict(
        telegram_user_id=telegram_user_id, last_name="Məmmədov", first_name="Əli", patronymic="Vəli oğlu",
        phone_enc=CRYPTO.encrypt("+994501234567"), phone_hash=CRYPTO.blind_index("phone", "+994501234567"),
        fin_enc=CRYPTO.encrypt("5ABC12D"), fin_hash=CRYPTO.blind_index("fin", "5ABC12D"),
        address_enc=CRYPTO.encrypt("Nizami küç. 5, m. 12"), consent_version=1, consent_at=MONDAY_10,
    )
    return Citizen(**(fields | overrides))


def build_world(session: Session) -> World:
    manage.sync_departments(session, CFG.departments)
    pensiya = session.scalar(select(Department).where(Department.code == "pensiya"))
    coordinator = Staff(telegram_user_id=COORDINATOR_TG, full_name="Leyla Məmmədova", role=StaffRole.coordinator,
                        is_admin=True)
    deputy = Staff(telegram_user_id=DEPUTY_TG, full_name="Orxan Əliyev", role=StaffRole.coordinator)
    staff_a = Staff(telegram_user_id=STAFF_A_TG, full_name="Rəşad Hüseynov", role=StaffRole.staff,
                    department_id=pensiya.id)
    staff_b = Staff(telegram_user_id=STAFF_B_TG, full_name="Nigar Quliyeva", role=StaffRole.staff,
                    department_id=pensiya.id)
    head = Staff(telegram_user_id=HEAD_TG, full_name="Elçin Rəhimov", role=StaffRole.head)
    off = Staff(telegram_user_id=OFF_COORD_TG, full_name="Köhnə Koordinator", role=StaffRole.coordinator,
                is_active=False)
    citizens = [complete_citizen(CITIZEN_TG), complete_citizen(OTHER_CITIZEN_TG)]
    session.add_all([coordinator, deputy, staff_a, staff_b, head, off, *citizens])
    session.flush()
    return World(
        staff_actor(coordinator), staff_actor(deputy), staff_actor(staff_a), staff_actor(staff_b),
        staff_actor(head), citizens[0].id, citizens[1].id, pensiya.id,
    )
