"""LLM və embedding provayder fabriki.

Eyni kod Claude (API), OpenAI, lokal Ollama və ya öz GPU-da serve olunan fine-tune modeli (vLLM)
ilə işləyir. Yeni sistemdə hər agentin modeli config/agents.yaml-dadır (get_chat_model).

Agentlər (app/agents/) bu modulu import etmir: LLM-ə yeganə yol maskalama qatıdır (app/privacy/llm.py).
Bunu tests/unit/test_private_llm.py yoxlayır. Bilik bazası (app/knowledge/) buradan yalnız lokal
embedding modelini götürür.
"""
from functools import lru_cache

from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel

from app.config import ConfigError, settings


@lru_cache
def get_embeddings() -> Embeddings:
    provider = settings.embedding_provider.lower()
    if provider == "ollama":
        from langchain_ollama import OllamaEmbeddings

        return OllamaEmbeddings(model=settings.embedding_model, base_url=settings.ollama_base_url)
    if provider == "openai":
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(model=settings.embedding_model, api_key=settings.openai_api_key)
    if provider == "huggingface":
        from langchain_huggingface import HuggingFaceEmbeddings

        return HuggingFaceEmbeddings(model_name=settings.embedding_model)

    raise ValueError(f"Naməlum EMBEDDING_PROVIDER: {settings.embedding_provider}")


# ================= yeni sistem =================
def build_chat_model(provider: str, model: str, effort: str | None = None) -> BaseChatModel:
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        # Claude 5.x modelləri temperature parametrini qəbul etmir, ona görə ötürülmür. 5.5 modellərində
        # thinking defolt olaraq açıqdır və max_tokens-ə daxildir: limit həm düşünməyə, həm cavaba çatmalıdır.
        # Ödəniş yalnız istifadə olunan tokenlər üçündür. Düşünmənin miqdarını effort idarə edir (agents.yaml).
        return ChatAnthropic(model=model, max_tokens=8192, max_retries=3, timeout=60,
                             api_key=settings.anthropic_api_key, effort=effort)
    if provider in ("openai", "vllm"):
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=model, max_retries=3, timeout=60, api_key=settings.openai_api_key or "EMPTY",
                          base_url=settings.openai_base_url)
    if provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(model=model, base_url=settings.ollama_base_url)
    raise ConfigError(f"naməlum LLM provayderi: {provider}")


@lru_cache
def get_chat_model(agent: str) -> BaseChatModel:
    from app.yaml_config import get_config

    agents = get_config().agents
    provider, model = agents.model_for(agent)
    return build_chat_model(provider, model, agents.effort_for(agent))


@lru_cache
def get_embedding_model() -> Embeddings:
    """Bilik bazası üçün. Hibrid rejimdə embedding lokal (Ollama) olmalıdır, ya da sorğu maskalanmalıdır."""
    if settings.embedding_provider.lower() == "ollama":
        from langchain_ollama import OllamaEmbeddings

        return OllamaEmbeddings(model=settings.embedding_model,
                                base_url=settings.embeddings_url or settings.ollama_base_url)
    return get_embeddings()
