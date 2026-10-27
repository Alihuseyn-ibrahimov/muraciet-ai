"""Vətəndaş dialoqunun qısamüddətli vəziyyəti (Redis, spec 4-cü bölmə).

Burada yalnız forma addımı və müraciət layihəsi saxlanılır. Formda yazılan şəxsi məlumatlar (telefon,
ad, FİN, ünvan) Redis-ə düşmür: dərhal şifrələnib bazaya yazılır. Sessiya 24 saat yaşayır
(sla.yaml: sessions.citizen_ttl_hours).

Eyni interfeysin yaddaşdakı variantı testlər üçündür.
"""
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Protocol

STEPS = ("idle", "intake", "consent", "contact", "name", "fin", "address", "confirm_profile", "confirm",
         "feedback_reason", "choose_appeal")
UPDATE_TTL_SECONDS = 24 * 3600


@dataclass
class CitizenSession:
    step: str = "idle"
    draft: dict | None = None  # {"subject", "summary", "description"}
    last_question: str | None = None  # "Müraciət etmək istəyirəm" düyməsi bu sualdan başlayır
    pending_reply: str | None = None  # choose_appeal: hansı müraciətə aid olduğu bilinməyən cavab
    feedback_appeal_id: int | None = None
    redo: bool = False  # profil yenilənir: bütün sahələr yenidən soruşulur
    profile_only: bool = False  # /melumatlarim → Yenilə: müraciətsiz bitir
    profile_ok: bool = False  # vətəndaş profili bu dəfə yazıb və ya təsdiqləyib
    extra: dict = field(default_factory=dict)

    def reset(self) -> None:
        self.__dict__.update(asdict(CitizenSession()))


class SessionStore(Protocol):
    def load(self, user_id: int) -> CitizenSession: ...
    def save(self, user_id: int, session: CitizenSession) -> None: ...


class Limiter(Protocol):
    def hit(self, user_id: int) -> int:
        """Bu dəqiqədə vətəndaşın neçənci hadisəsidir."""

    def first_time(self, update_id: int) -> bool:
        """Update ilk dəfə gəlibsə True (runner təkrar göndərə bilər, emal bir dəfə olmalıdır)."""

    def forget(self, update_id: int) -> None:
        """Emal xəta ilə bitəndə: runner-in təkrar göndərdiyi update yenidən emal olunsun."""


def _decode(raw: str | bytes | None, cls: type = CitizenSession):
    if not raw:
        return cls()
    data = json.loads(raw)
    return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class RedisSessionStore:
    """cls: sessiyanın dataclass-ı (vətəndaş və ya əməkdaş). Saxlanılan köhnə sahələr nəzərə alınmır."""

    def __init__(self, redis, *, ttl_seconds: int, prefix: str = "citizen:session", cls: type = CitizenSession):
        self.redis, self.ttl, self.prefix, self.cls = redis, ttl_seconds, prefix, cls

    def load(self, user_id: int):
        return _decode(self.redis.get(f"{self.prefix}:{user_id}"), self.cls)

    def save(self, user_id: int, session: CitizenSession) -> None:
        self.redis.set(f"{self.prefix}:{user_id}", json.dumps(asdict(session), ensure_ascii=False), ex=self.ttl)


class RedisLimiter:
    def __init__(self, redis, *, prefix: str = "citizen"):
        self.redis, self.prefix = redis, prefix

    def hit(self, user_id: int) -> int:
        key = f"{self.prefix}:rate:{user_id}:{int(time.time() // 60)}"
        count = self.redis.incr(key)
        if count == 1:
            self.redis.expire(key, 120)
        return int(count)

    def first_time(self, update_id: int) -> bool:
        return bool(self.redis.set(f"{self.prefix}:update:{update_id}", 1, nx=True, ex=UPDATE_TTL_SECONDS))

    def forget(self, update_id: int) -> None:
        self.redis.delete(f"{self.prefix}:update:{update_id}")


class MemorySessionStore:
    def __init__(self, cls: type = CitizenSession):
        self.data: dict[int, str] = {}
        self.cls = cls

    def load(self, user_id: int):
        return _decode(self.data.get(user_id), self.cls)

    def save(self, user_id: int, session: CitizenSession) -> None:
        self.data[user_id] = json.dumps(asdict(session), ensure_ascii=False)


class MemoryLimiter:
    def __init__(self):
        self.counts: dict[int, int] = {}
        self.updates: set[int] = set()

    def hit(self, user_id: int) -> int:
        self.counts[user_id] = self.counts.get(user_id, 0) + 1
        return self.counts[user_id]

    def first_time(self, update_id: int) -> bool:
        if update_id in self.updates:
            return False
        self.updates.add(update_id)
        return True

    def forget(self, update_id: int) -> None:
        self.updates.discard(update_id)
