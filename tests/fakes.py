"""Testlər üçün saxta LLM.

İki rolu var:
1. Ssenari üzrə cavab verir: mətn, AIMessage (tool_calls ilə), Pydantic obyekti və ya dict
   (structured output üçün). Ssenaridəki Exception atılır, bununla provayder xətası yoxlanılır.
2. "Casus" kimi işləyir: modelə göndərilən bütün mesajları `received`-də saxlayır.
   Privacy testi `all_text()` ilə LLM-ə şəxsi məlumat getmədiyini yoxlayır.
"""
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, convert_to_messages
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel, Field


def _to_messages(input_: Any) -> list[BaseMessage]:
    if isinstance(input_, str):
        return [HumanMessage(input_)]
    if hasattr(input_, "to_messages"):
        return input_.to_messages()
    return convert_to_messages(input_)


def _text(message: BaseMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    return " ".join(b.get("text", "") for b in message.content if isinstance(b, dict))


class FakeChatModel(BaseChatModel):
    script: list[Any] = Field(default_factory=list)
    received: list[list[BaseMessage]] = Field(default_factory=list)
    structured_methods: list[str | None] = Field(default_factory=list)  # with_structured_output(method=...)

    @property
    def _llm_type(self) -> str:
        return "fake-chat"

    def _next(self, messages: list[BaseMessage]) -> Any:
        self.received.append(list(messages))
        if not self.script:
            raise AssertionError("FakeChatModel: ssenari bitdi, gözlənilməz LLM çağırışı")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        item = self._next(messages)
        message = item if isinstance(item, AIMessage) else AIMessage(content=str(item))
        return ChatResult(generations=[ChatGeneration(message=message)])

    def bind_tools(self, tools, **kwargs):
        # Ssenaridəki AIMessage tool_calls daşıya bilər, ona görə ayrıca bağlama lazım deyil
        return self

    def with_structured_output(self, schema: type[BaseModel], **kwargs):
        self.structured_methods.append(kwargs.get("method"))

        def run(input_: Any) -> BaseModel:
            item = self._next(_to_messages(input_))
            if isinstance(item, schema):
                return item
            if isinstance(item, dict):
                return schema.model_validate(item)
            raise TypeError(f"Ssenari elementi {schema.__name__} deyil: {type(item).__name__}")

        return RunnableLambda(run)

    def all_text(self) -> str:
        """Modelə göndərilən bütün mesajların mətni (privacy yoxlaması üçün)."""
        return "\n".join(_text(m) for batch in self.received for m in batch)


class FakeTelegramClient:
    """Telegram-ın yerinə işləyir: göndərilənləri yadda saxlayır, ssenari üzrə xəta verir.
    `failures`-in hər elementi bir çağırışa uyğundur: None uğur deməkdir, Exception isə atılır."""

    def __init__(self, failures: list | None = None, first_message_id: int = 1000, files: dict | None = None):
        self.failures = list(failures or [])
        self.files = dict(files or {})  # file_id → məzmun (download_file); yoxdursa BadRequest
        self.sent: list[tuple[str, int, dict]] = []
        self._next_id = first_message_id

    def _maybe_fail(self) -> None:
        if self.failures:
            failure = self.failures.pop(0)
            if failure is not None:
                raise failure

    def send_message(self, chat_id, text, *, reply_markup=None, reply_to_message_id=None) -> int:
        self._maybe_fail()
        self._next_id += 1
        self.sent.append(("send_message", chat_id, {"text": text, "reply_markup": reply_markup}))
        return self._next_id

    def edit_message_text(self, chat_id, message_id, text, *, reply_markup=None) -> None:
        self._maybe_fail()
        self.sent.append(("edit_message_text", chat_id,
                          {"message_id": message_id, "text": text, "reply_markup": reply_markup}))

    def download_file(self, file_id: str) -> bytes:
        from app.notifier.telegram_api import BadRequest

        if file_id not in self.files:
            raise BadRequest("fayl tapılmadı")
        return self.files[file_id]

    def send_document(self, chat_id, filename, content, *, caption=None) -> int:
        self._maybe_fail()
        self._next_id += 1
        self.sent.append(("send_document", chat_id, {"filename": filename, "content": content, "caption": caption}))
        return self._next_id

    def answer_callback_query(self, callback_query_id, text=None, show_alert=False) -> None:
        self.sent.append(("answer_callback_query", 0, {"id": callback_query_id, "text": text,
                                                         "show_alert": show_alert}))


class RecordingNotifier:
    """Servisin bildirişlərini yadda saxlayır (real göndərmə Faza 4-də outbox ilə olur)."""

    def __init__(self):
        self.events: list[tuple[str, int, dict]] = []

    def appeal_event(self, appeal, event: str, **details) -> None:
        self.events.append((event, appeal.id, details))

    def names(self) -> list[str]:
        return [name for name, _, _ in self.events]

    def last(self, name: str) -> dict:
        return next(details for event, _, details in reversed(self.events) if event == name)
