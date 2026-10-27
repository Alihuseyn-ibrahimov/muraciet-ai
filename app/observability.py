"""LangSmith ilə izləmə (#observability): yalnız maskalanmış LLM çağırışları.

LangChain-in avtomatik izləməsi (LANGSMITH_TRACING mühit dəyişəni) söndürülür: o, LangGraph qrafının
vəziyyətini, yəni vətəndaşın xam mətnini də xarici xidmətə göndərərdi. Bunun əvəzinə `PrivateLLM` hər
çağırışı `traced()` daxilində edir: izə yalnız maskalanmış mesajlar və bərpadan əvvəlki cavab düşür.
İkinci qat: müştəri `hide_inputs/hide_outputs` ilə hər şeyi regex + NER ilə bir daha maskalayır.

Qoşmaq üçün: LANGSMITH_TRACING=true və LANGSMITH_API_KEY (pilotda hüquqi razılıqdan sonra: məlumat
xaricə gedir, maskalanmış formada olsa da).
"""
from contextlib import nullcontext
from functools import lru_cache
from typing import Any

from app.config import AUTO_TRACING_VARS, disable_auto_tracing, settings  # noqa: F401 (testlər üçün)


def scrub(data: Any) -> Any:
    from app.privacy.masking import Pseudonymizer

    return Pseudonymizer().mask_obj(data)


def enabled() -> bool:
    return bool(settings.langsmith_tracing and settings.langsmith_api_key)


@lru_cache
def get_client():
    from langsmith import Client

    return Client(api_key=settings.langsmith_api_key, hide_inputs=scrub, hide_outputs=scrub)


def traced(agent: str):
    if not enabled():
        return nullcontext()
    from langsmith import tracing_context

    return tracing_context(enabled=True, client=get_client(), project_name=settings.langsmith_project,
                           tags=[agent], metadata={"agent": agent})
