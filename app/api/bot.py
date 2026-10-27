"""Botların update-ləri (runner → API). Telegram-a heç bir port açılmır: runner polling edir və bura ötürür."""
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.exc import OperationalError

from app.api.deps import get_citizen_controller, get_staff_controller, require_service_token

log = logging.getLogger(__name__)
router = APIRouter(prefix="/bot", dependencies=[Depends(require_service_token)])

# Bu xətalar keçicidir (baza və ya Redis əlçatan deyil): runner update-i sonra yenidən göndərsin
RETRYABLE = (OperationalError, RedisConnectionError)


@router.post("/citizen/update")
def citizen_update(update: dict, controller=Depends(get_citizen_controller)):
    return _handle(controller, update)


@router.post("/staff/update")
def staff_update(update: dict, controller=Depends(get_staff_controller)):
    return _handle(controller, update)


def _handle(controller, update: dict):
    try:
        controller.handle(update)
    except RETRYABLE as e:
        log.warning("update %s emal olunmadı, runner təkrar göndərəcək (%s)", update.get("update_id"),
                    type(e).__name__)
        return JSONResponse({"ok": False, "retry": True}, status_code=503)
    except Exception:  # pozuq update botu bütün vətəndaşlar üçün dayandırmamalıdır
        log.exception("update %s atıldı", update.get("update_id"))
        return {"ok": False}
    return {"ok": True}
