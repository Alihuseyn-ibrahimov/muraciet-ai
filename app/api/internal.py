"""n8n üçün daxili endpoint-lər (plan 8.2, 8.5). Servis tokeni tələb olunur; n8n şəxsi məlumat görmür."""
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import get_internal, require_service_token

router = APIRouter(prefix="/internal", dependencies=[Depends(require_service_token)])


class NotifyIn(BaseModel):
    target: Literal["head", "coordinators", "admins"]
    text: str = Field(min_length=1, max_length=4000)
    only_workday: bool = False  # gündəlik hesabat: bayram günlərində göndərilmir


@router.post("/sla/check")
def sla_check(internal=Depends(get_internal)) -> dict:
    """Hər saat (24/7): eskalasiyalar, son tarix xatırlatmaları, vətəndaşa xatırlatma, avtomatik bağlanma."""
    return internal.sla_check()


@router.post("/notify")
def notify(body: NotifyIn, internal=Depends(get_internal)) -> dict:
    """Gündəlik hesabat (rəhbərə) və n8n xəta bildirişi (adminlərə)."""
    return {"sent": internal.notify(body.target, body.text, body.only_workday)}


class ReportIn(BaseModel):
    days: int = Field(default=7, ge=1, le=31)


@router.post("/report/weekly")
def weekly_report(body: ReportIn | None = None, internal=Depends(get_internal)) -> dict:
    """Həftədə bir dəfə: rəhbərə göstəricilər və hesabat analitikinin təhlili (agent alınmasa yalnız rəqəmlər)."""
    return internal.weekly_report((body or ReportIn()).days)


@router.post("/retention/cleanup")
def retention_cleanup(internal=Depends(get_internal)) -> dict:
    return internal.retention()
