"""Veb-vidcet: yalnız ümumi suallara cavab verir (spec 6-E).

Müraciət qeydiyyatı təsdiqlənmiş telefon tələb edir, ona görə müraciət, status və aydınlaşdırma
niyyətlərində vətəndaş Telegram botuna yönləndirilir. Vidcetdə şəxsi profil yoxdur.
"""
import zlib

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.api.deps import get_web_limiter, get_web_turn
from app.config import settings
from app.privacy.masking import Pseudonymizer
from app.yaml_config import get_config

router = APIRouter(prefix="/api")
TELEGRAM_ONLY = {"new_appeal", "status", "clarification_reply"}


class ChatIn(BaseModel):
    session_id: str = Field(min_length=1, max_length=64, pattern=r"^[\w-]+$")
    message: str = Field(min_length=1, max_length=4000)


class ChatOut(BaseModel):
    reply: str
    intent: str


@router.post("/chat", response_model=ChatOut)
def chat(body: ChatIn, request: Request, turn=Depends(get_web_turn), limiter=Depends(get_web_limiter)):
    # Açıq endpoint-dir və hər mesaj LLM çağırışıdır: limit IP-yə görədir (yeni sessiya ondan qaçmır).
    # Reverse proxy arxasında X-Forwarded-For proxy-də etibarlı şəkildə qurulmalıdır.
    client = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0]
    if limiter.hit(zlib.crc32(client.strip().encode())) > settings.rate_limit_per_minute:
        return JSONResponse({"reply": get_config().templates.render("rate_limited"), "intent": "rate_limited"},
                            status_code=429)
    result = turn(citizen_key=f"web:{body.session_id}", text=body.message, step="idle", has_open_question=False,
                  pseudo=Pseudonymizer())
    templates = get_config().templates
    if result.intent in TELEGRAM_ONLY or result.effects:
        bot = f"@{settings.citizen_bot_username}" if settings.citizen_bot_username else "Telegram"
        return ChatOut(reply=templates.render("web_use_telegram", bot=bot), intent=result.intent)
    return ChatOut(reply=result.reply or templates.render("llm_unavailable"), intent=result.intent)
