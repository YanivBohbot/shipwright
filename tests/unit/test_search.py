from shipwright.knowledge.search import KnowledgeSearcher
from tests.fakes import FakeVectorStore


async def test_search_maps_metadata_to_chunks():
    store = FakeVectorStore.with_docs(
        [("AC-1 header", {"source": "s.md", "doc_id": "X", "section": "A"})]
    )
    [c] = await KnowledgeSearcher(store).search("header", k=1)
    assert (c.doc_id, c.section) == ("X", "A")
    assert c.text == "AC-1 header" and c.source == "s.md"
