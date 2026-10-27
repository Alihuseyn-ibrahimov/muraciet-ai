"""Bilik bazası: yükləmə (sahib rolu) və axtarış (tətbiq rolu), saxta deterministik embedding ilə."""
import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding

from app.knowledge.kb import KnowledgeBase, ingest, load_documents, make_store, split_documents

pytestmark = pytest.mark.integration

EMBEDDINGS = DeterministicFakeEmbedding(size=16)


def url(engine) -> str:
    return engine.url.render_as_string(hide_password=False)


def test_documents_have_titles_from_first_heading():
    titles = {d.metadata["title"] for d in load_documents()}
    assert "Vətəndaş müraciətlərinin qəbulu və baxılması qaydası" in titles


def test_ingest_is_idempotent_and_search_works_with_app_role(owner_engine, app_engine):
    first = ingest(url(owner_engine), embeddings=EMBEDDINGS)
    second = ingest(url(owner_engine), embeddings=EMBEDDINGS)  # təkrar yükləmə dublikat yaratmır
    assert first == second > 0

    chunk = split_documents(load_documents())[0]
    kb = KnowledgeBase(make_store(url(app_engine), EMBEDDINGS), top_k=3, max_distance=0.01)
    hits = kb.search(chunk.page_content)  # eyni mətn → məsafə 0
    assert len(hits) == 1 and hits[0].index == 1 and hits[0].title == chunk.metadata["title"]
    assert kb.search("tamamilə başqa mövzu") == []  # max_distance-dan uzaq olanlar atılır


def test_approved_curator_entries_are_searchable_and_survive_reingest(owner_engine, app_engine, world):
    from sqlalchemy.orm import Session

    from app.db.tables import KbEntry
    from app.knowledge.kb import entry_document

    with Session(app_engine) as s, s.begin():
        s.add_all([KbEntry(question="Pensiya kartı itəndə nə etmək lazımdır?", answer="Banka müraciət edin.",
                           status="approved"),
                   KbEntry(question="Rədd edilmiş sual", answer="Rədd edilmiş cavab", status="rejected")])
    ingest(url(owner_engine), embeddings=EMBEDDINGS)
    kb = KnowledgeBase(make_store(url(app_engine), EMBEDDINGS), top_k=3, max_distance=0.01)
    approved = entry_document(0, "Pensiya kartı itəndə nə etmək lazımdır?", "Banka müraciət edin.").page_content
    assert [h.title for h in kb.search(approved)] == ["Pensiya kartı itəndə nə etmək lazımdır?"]
    rejected = entry_document(0, "Rədd edilmiş sual", "Rədd edilmiş cavab").page_content
    assert kb.search(rejected) == []

    kb.add_entry(99, "Yeni təsdiqlənmiş sual", "Yeni cavab")  # tətbiq rolu ilə, dərhal
    assert kb.search(entry_document(99, "Yeni təsdiqlənmiş sual", "Yeni cavab").page_content)[0].source == "kurator-99"
