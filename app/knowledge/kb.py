"""Qurumun rəsmi bilik bazası (RAG): yükləmə və axtarış (spec 5-ci bölmə).

- Yükləmə (ingest) sahib rolu ilə edilir: kolleksiya sıfırdan qurulur, ona görə təkrar yükləmə
  dublikat yaratmır.
- Axtarışı tətbiq rolu edir. Yalnız kifayət qədər oxşar parçalar qaytarılır (max_distance),
  zəif uyğunluq "məlumat yoxdur" sayılır. Uydurma cavabın qarşısı ilk növbədə burada alınır.
- Hər parçanın başlığı sənədin ilk "# " başlığından götürülür, vətəndaşa mənbə kimi göstərilir.

Sorğu maskalanmış mətndir (agentlər bunu təmin edir): embedding provayderi lokal olmasa belə,
vətəndaşın şəxsi məlumatı oraya getmir.
"""
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_postgres import PGVector
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import settings

COLLECTION = "knowledge_base"
KNOWLEDGE_DIR = Path(__file__).resolve().parents[2] / "data" / "knowledge"


@dataclass(frozen=True)
class KnowledgeHit:
    index: int  # cavabda [1], [2] kimi istinad üçün
    title: str
    source: str
    text: str
    distance: float


def _title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def load_documents(folder: Path = KNOWLEDGE_DIR) -> list[Document]:
    documents = []
    for path in sorted(folder.rglob("*")):
        if path.suffix.lower() in (".md", ".txt"):
            text = path.read_text(encoding="utf-8")
            documents.append(Document(page_content=text,
                                      metadata={"source": path.name, "title": _title(text, path.stem)}))
    return documents


def entry_document(entry_id: int, question: str, answer: str) -> Document:
    """Kurator təklifi (admin təsdiqləyib): sual başlıq, cavab mətn."""
    return Document(page_content=f"# {question}\n\n{answer}",
                    metadata={"source": f"kurator-{entry_id}", "title": question})


def approved_entries(url: str) -> list[Document]:
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from app.db.tables import KbEntry

    engine = create_engine(url)
    try:
        with Session(engine) as s:
            rows = s.execute(select(KbEntry.id, KbEntry.question, KbEntry.answer)
                             .where(KbEntry.status == "approved").order_by(KbEntry.id)).all()
    finally:
        engine.dispose()
    return [entry_document(*row) for row in rows]


def split_documents(documents: list[Document]) -> list[Document]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=800, chunk_overlap=120, separators=["\n## ", "\n### ", "\n\n", "\n", ". ", " "]
    )
    return splitter.split_documents(documents)  # metadata (başlıq, mənbə) hər parçaya keçir


def make_store(url: str, embeddings: Embeddings) -> PGVector:
    return PGVector(embeddings=embeddings, collection_name=COLLECTION, connection=url, use_jsonb=True,
                    create_extension=False)


def ingest(owner_url: str, *, embeddings: Embeddings | None = None, folder: Path = KNOWLEDGE_DIR) -> int:
    if embeddings is None:
        from app.llm import get_embedding_model

        embeddings = get_embedding_model()
    # Sıfırdan qurulur: təsdiqlənmiş kurator təklifləri də (bazada saxlanılır) yenidən əlavə olunur
    chunks = split_documents(load_documents(folder) + approved_entries(owner_url))
    store = make_store(owner_url, embeddings)
    store.delete_collection()
    store.create_collection()
    store.add_documents(chunks)
    return len(chunks)


class KnowledgeBase:
    def __init__(self, store: PGVector, *, top_k: int, max_distance: float):
        self._store = store
        self._top_k = top_k
        self._max_distance = max_distance

    def add_entry(self, entry_id: int, question: str, answer: str) -> None:
        """Təsdiqlənmiş kurator təklifi: yenidən yükləmə gözlənilmədən dərhal axtarışa düşür (tətbiq rolu)."""
        self._store.add_documents([entry_document(entry_id, question, answer)], ids=[f"kurator-{entry_id}"])

    def search(self, query: str) -> list[KnowledgeHit]:
        results = self._store.similarity_search_with_score(query, k=self._top_k)
        relevant = [(doc, distance) for doc, distance in results if distance <= self._max_distance]
        return [
            KnowledgeHit(i, doc.metadata.get("title", "Mənbə"), doc.metadata.get("source", ""), doc.page_content,
                         float(distance))
            for i, (doc, distance) in enumerate(relevant, start=1)
        ]


def format_hits(hits: list[KnowledgeHit]) -> str:
    return "\n\n".join(f"[{hit.index}] {hit.title}\n{hit.text}" for hit in hits)


@lru_cache
def get_knowledge_base() -> KnowledgeBase:
    from app.llm import get_embedding_model

    settings.require("app_database_url")
    return KnowledgeBase(make_store(settings.app_database_url, get_embedding_model()),
                         top_k=settings.rag_top_k, max_distance=settings.rag_max_distance)
