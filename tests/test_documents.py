import io

import pytest
from pypdf import PdfWriter

from lit_agent import documents, retrieval, store
from lit_agent.runtime import Guard


def test_pdf_extracts_pages_and_provenance(indexed):
    paper_id, result = indexed
    assert result["pages"] == 3
    assert result["chunks"] >= 3
    chunks = store.rows("SELECT * FROM chunks WHERE paper_id=?", (paper_id,))
    assert {c["page"] for c in chunks} == {1, 2, 3}
    assert all(c["document_id"] == result["document_id"] for c in chunks)


def test_upload_and_processing_are_idempotent(indexed, pdf_bytes):
    paper_id, result = indexed
    assert documents.upload(pdf_bytes, "Duplicate")[0] == paper_id
    task = store.enqueue("ingest", {"paper_id": paper_id, "file_hash": documents.save_pdf(pdf_bytes)})
    again = documents.process(task, Guard())
    assert again["reused"]
    assert again["document_id"] == result["document_id"]
    assert store.one("SELECT count(*) AS n FROM papers")["n"] == 1


def test_duplicate_before_worker_does_not_create_second_paper(pdf_bytes):
    assert documents.upload(pdf_bytes, "First")[0] == documents.upload(pdf_bytes, "Second")[0]


def test_force_reindex_removes_old_chunks(indexed, pdf_bytes):
    paper_id, result = indexed
    count = store.one("SELECT count(*) AS n FROM chunks")["n"]
    task = store.enqueue("ingest", {"paper_id": paper_id, "file_hash": documents.save_pdf(pdf_bytes), "force": True})
    documents.process(task, Guard())
    assert store.one("SELECT count(*) AS n FROM chunks")["n"] == count
    assert store.one("SELECT count(*) AS n FROM chunks_fts")["n"] == count


@pytest.mark.parametrize("mode", ["keyword", "dense", "hybrid"])
def test_retrieval_modes_use_real_qdrant_and_fts(indexed, mode):
    paper_id, _ = indexed
    hits = retrieval.search("method", [paper_id], mode=mode)
    assert hits and all(c["paper_id"] == paper_id for c in hits)


def test_scope_and_fts_literals(indexed):
    paper_id, _ = indexed
    assert retrieval.search("method", [paper_id + 999], mode="keyword") == []
    assert isinstance(retrieval.search('method OR ")* --', [paper_id], mode="keyword"), list)


def test_retired_version_not_searchable(indexed):
    paper_id, result = indexed
    with store.connect() as db:
        db.execute("UPDATE documents SET status='retired' WHERE id=?", (result["document_id"],))
    assert retrieval.search("method", [paper_id]) == []


def test_delete_removes_sql_and_vector_results(indexed):
    paper_id, _ = indexed
    documents.delete_paper(paper_id)
    assert not retrieval.search("method", [paper_id])
    for table in ("documents", "pages", "chunks", "chunks_fts"):
        assert store.one(f"SELECT count(*) AS n FROM {table}")["n"] == 0


def test_blank_scan_rejected():
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    stream = io.BytesIO()
    writer.write(stream)
    paper_id, digest = documents.upload(stream.getvalue(), "Blank scan")
    task = store.enqueue("ingest", {"paper_id": paper_id, "file_hash": digest})
    with pytest.raises(ValueError, match="扫描件"):
        documents.process(task, Guard())


def test_encrypted_upload_rejected():
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt("secret")
    stream = io.BytesIO()
    writer.write(stream)
    with pytest.raises(ValueError, match="加密"):
        documents.upload(stream.getvalue(), "Encrypted")


def test_invalid_header_rejected():
    with pytest.raises(ValueError):
        documents.save_pdf(b"not-pdf")
