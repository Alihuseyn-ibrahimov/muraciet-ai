"""Admin API (MCP server onu Claude Desktop/Code-dan çağırır, `X-API-Key` ilə).

MCP-nin cavabları LLM-ə gedir, ona görə burada şəxsi məlumat yoxdur: siyahılarda yalnız metadata,
müraciətin mətnləri vətəndaşın profili və regex ilə maskalanır ([SOYAD_1], [FIN_1] ...).

Müraciəti dəyişən əməliyyat yoxdur. Yeganə "yazma" təklifdir (`/proposals/assign`): koordinatorlara əməkdaş
botunda adi təyinat düyməsi olan kart gedir, qərarı koordinator verir (icazə, versiya və audit adi axındadır).
Cavab yalnız əməkdaş botunda, insan tərəfindən yazılır.
"""
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import get_crypto_dep, get_session_factory_dep, require_admin_key
from app.db.tables import Appeal, AppealEvent, Citizen, Department, Staff
from app.notifier import cards, recipients
from app.notifier.outbox import enqueue_rendered
from app.privacy.profile import pseudonymizer_for
from app.workflow.calendar import get_calendar
from app.workflow.states import ActorType, AppealStatus, BotKind, StaffRole
from app.workflow.suggest import open_appeal_count

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin_key)])
OPEN = (AppealStatus.new, AppealStatus.assigned, AppealStatus.waiting_citizen, AppealStatus.pending_approval)


def _today():
    return datetime.now(UTC).astimezone(get_calendar().tz).date()


def _row(appeal: Appeal, department: str | None, assignee: str | None) -> dict:
    return {"number": appeal.number, "status": appeal.status.value,
            "category": appeal.category.value if appeal.category else None, "priority": appeal.priority.value,
            "department": department, "due_date": appeal.due_date.isoformat(),
            "created_at": appeal.created_at.isoformat(), "assignee": assignee}


def _listing(s, *where, limit: int) -> list[dict]:
    query = (select(Appeal, Department.code, Staff.full_name)
             .outerjoin(Department, Appeal.department_id == Department.id)
             .outerjoin(Staff, Appeal.assigned_staff_id == Staff.id)
             .where(*where).order_by(Appeal.id.desc()).limit(limit))
    return [_row(a, dept, name) for a, dept, name in s.execute(query)]


@router.get("/stats")
def stats(sessions=Depends(get_session_factory_dep)) -> dict:
    with sessions() as s:
        by_status = dict(s.execute(select(Appeal.status, func.count()).group_by(Appeal.status)).all())
        overdue = s.scalar(select(func.count()).select_from(Appeal)
                           .where(Appeal.status.in_(OPEN), Appeal.due_date < _today()))
    return {"by_status": {k.value: v for k, v in by_status.items()}, "overdue": overdue}


@router.get("/appeals")
def appeals(status: AppealStatus | None = None, limit: int = Query(20, ge=1, le=200),
            sessions=Depends(get_session_factory_dep)) -> list[dict]:
    with sessions() as s:
        return _listing(s, *([Appeal.status == status] if status else []), limit=limit)


@router.get("/appeals/overdue")
def overdue(sessions=Depends(get_session_factory_dep)) -> list[dict]:
    with sessions() as s:
        return _listing(s, Appeal.status.in_(OPEN), Appeal.due_date < _today(), limit=200)


@router.get("/appeals/{number}")
def appeal(number: str, sessions=Depends(get_session_factory_dep), crypto=Depends(get_crypto_dep)) -> dict:
    with sessions() as s:
        row = s.execute(select(Appeal, Department.code, Staff.full_name)
                        .outerjoin(Department, Appeal.department_id == Department.id)
                        .outerjoin(Staff, Appeal.assigned_staff_id == Staff.id)
                        .where(Appeal.number == number.upper())).first()
        if row is None:
            raise HTTPException(status_code=404, detail="müraciət tapılmadı")
        found, department, assignee = row
        pseudo = pseudonymizer_for(s.get(Citizen, found.citizen_id), crypto)
        events = s.scalars(select(AppealEvent).where(AppealEvent.appeal_id == found.id).order_by(AppealEvent.id))
        return _row(found, department, assignee) | {
            "subject": pseudo.mask(found.subject), "summary": pseudo.mask(found.summary),
            "description": pseudo.mask(found.description),
            "reopen_count": found.reopen_count, "outcome": found.outcome.value if found.outcome else None,
            "events": [{"action": e.action, "at": e.created_at.isoformat(), "actor": e.actor_type.value,
                        "from": e.from_status.value if e.from_status else None,
                        "to": e.to_status.value if e.to_status else None} for e in events],
        }


@router.get("/report")
def report(days: int = Query(7, ge=1, le=31), sessions=Depends(get_session_factory_dep)) -> dict:
    """Həftəlik hesabat: aqreqat göstəricilər və mətn (hesabat analitikinin təhlili ilə). LLM çağırır."""
    from app.api.deps import weekly_report

    text, stats, ai = weekly_report(sessions, now=datetime.now(UTC), days=days)
    return {"stats": stats, "text": text, "ai": ai}


@router.get("/knowledge-gaps")
def knowledge_gaps(days: int = Query(30, ge=1, le=365), limit: int = Query(50, ge=1, le=200),
                   sessions=Depends(get_session_factory_dep)) -> list[dict]:
    """AI-nin cavab tapmadığı suallar, ən çox veriləndən başlayaraq (bilik bazasına nə əlavə olunmalıdır)."""
    from app.knowledge.gaps import knowledge_gaps as find
    from app.yaml_config import get_config

    with sessions() as s:
        return find(s, since=datetime.now(UTC) - timedelta(days=days),
                    no_answer_text=get_config().templates.render("no_answer_offer_appeal"), limit=limit)


# ---------- MCP-nin yeganə yazma əməliyyatı: təyinat təklifi ----------
ASSIGNABLE_ROLES = (StaffRole.staff, StaffRole.coordinator)
PROPOSABLE = (AppealStatus.new, AppealStatus.assigned, AppealStatus.waiting_citizen)
MCP_PROPOSAL = "mcp_proposal"


@router.get("/staff")
def staff_list(sessions=Depends(get_session_factory_dep)) -> list[dict]:
    """Müraciət təyin oluna bilən aktiv əməkdaşlar (əlaqə məlumatı yoxdur)."""
    with sessions() as s:
        rows = s.execute(select(Staff, Department.name).outerjoin(Department, Staff.department_id == Department.id)
                         .where(Staff.is_active, Staff.role.in_(ASSIGNABLE_ROLES)).order_by(Staff.full_name)).all()
        return [{"id": staff.id, "name": staff.full_name, "role": staff.role.value, "department": department,
                 "open": open_appeal_count(s, staff.id)} for staff, department in rows]


class AssignProposal(BaseModel):
    number: str = Field(min_length=5, max_length=20)
    staff_id: int
    reason: str = Field(default="", max_length=500)


@router.post("/proposals/assign")
def propose_assignment(body: AssignProposal, sessions=Depends(get_session_factory_dep)) -> dict:
    """Koordinatorlara "✅ Təyin et" kartı göndərir. Müraciət dəyişmir; təsdiq adi təyinat axınıdır."""
    with sessions() as s, s.begin():
        appeal = s.scalar(select(Appeal).where(Appeal.number == body.number.upper()))
        if appeal is None:
            raise HTTPException(status_code=404, detail="müraciət tapılmadı")
        if appeal.status not in PROPOSABLE:
            raise HTTPException(status_code=409, detail=f"müraciət {appeal.status.value} vəziyyətindədir")
        staff = s.get(Staff, body.staff_id)
        if staff is None or not staff.is_active or staff.role not in ASSIGNABLE_ROLES:
            raise HTTPException(status_code=422, detail="bu əməkdaşa müraciət təyin etmək olmaz")
        now = datetime.now(UTC)
        reason = body.reason.strip()
        s.add(AppealEvent(appeal_id=appeal.id, actor_type=ActorType.system, action="assignment_proposed",
                          from_status=appeal.status, to_status=appeal.status, created_at=now,
                          payload={"staff_id": staff.id, "reason": reason, "source": "mcp"}))
        rendered = cards.assignment_proposal(appeal, staff_id=staff.id, staff_name=staff.full_name,
                                             open_count=open_appeal_count(s, staff.id), reason=reason)
        coordinators = recipients.coordinators(s)
        for coordinator in coordinators:
            enqueue_rendered(s, bot=BotKind.staff, chat_id=coordinator.telegram_user_id, rendered=rendered,
                             appeal_id=appeal.id, purpose=MCP_PROPOSAL, now=now)
        return {"sent_to": len(coordinators)}
