import httpx
import pytest
from fastapi.testclient import TestClient

from lit_agent import documents, store
from lit_agent.webui import app


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


def test_pdf_renderer_assets_use_browser_compatible_mime_types(client):
    for name in ("pdf.min.mjs", "pdf.worker.min.mjs"):
        response = client.get(f"/static/vendor/pdfjs/{name}")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/javascript")


def add_paper(arxiv_id="1706.03762", pdf_url="https://arxiv.org/pdf/1706.03762"):
    with store.connect() as db:
        return db.execute("""INSERT INTO papers(title,authors,abstract,arxiv_id,pdf_url,created_at)
                             VALUES('Preview fixture','[]','',?,?,datetime('now'))""",
                          (arxiv_id, pdf_url)).lastrowid


def test_uploaded_pdf_previews_without_indexing(client, pdf_bytes):
    paper_id, _ = documents.upload(pdf_bytes, "Uploaded PDF")
    info = client.get(f"/api/papers/{paper_id}/preview").json()
    assert info["title"] == "Uploaded PDF"
    response = client.get(info["pdf_url"])
    assert response.content == pdf_bytes
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline;")
    partial = client.get(info["pdf_url"], headers={"Range": "bytes=0-4"})
    assert partial.status_code == 206 and partial.content == b"%PDF-"
    assert not store.rows("SELECT * FROM chunks")


def test_arxiv_preview_is_cached_without_model_or_index(client, pdf_bytes, monkeypatch):
    paper_id = add_paper()
    calls = []

    def download(arxiv_id, guard):
        calls.append(arxiv_id)
        return pdf_bytes

    monkeypatch.setattr(documents, "download_arxiv", download)
    info = client.get(f"/api/papers/{paper_id}/preview").json()
    assert info["can_download"] and info["pdf_url"] is None
    first = client.post(f"/api/papers/{paper_id}/preview")
    assert first.status_code == 200 and first.json()["page_count"] == 3
    assert client.post(f"/api/papers/{paper_id}/preview").json() == first.json()
    assert calls == ["1706.03762"]
    assert len(store.rows("SELECT * FROM documents")) == 1
    assert store.one("SELECT status,source FROM documents") == {"status": "uploaded", "source": "arxiv"}
    assert not store.rows("SELECT * FROM tasks") and not store.rows("SELECT * FROM chunks")
    assert client.get(first.json()["pdf_url"]).content == pdf_bytes


def test_preview_missing_file_is_not_reported_available(client, pdf_bytes):
    paper_id, digest = documents.upload(pdf_bytes, "Missing file")
    (documents.DATA / "pdf" / f"{digest}.pdf").unlink()
    info = client.get(f"/api/papers/{paper_id}/preview").json()
    assert info["pdf_url"] is None and not info["can_download"]
    assert client.get("/api/papers/99999/preview").status_code == 404
    assert client.post("/api/papers/99999/preview").status_code == 404


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///C:/secret.pdf", "//evil.example/a.pdf"])
def test_preview_does_not_embed_unsafe_source_or_proxy_arbitrary_url(client, url, monkeypatch):
    paper_id = add_paper(arxiv_id="bad/id/../../file", pdf_url=url)
    monkeypatch.setattr(documents, "download_arxiv", lambda *args: pytest.fail("Unexpected download"))
    info = client.get(f"/api/papers/{paper_id}/preview").json()
    assert not info["source_url"] and not info["can_download"]
    assert client.post(f"/api/papers/{paper_id}/preview").status_code == 422


def test_failed_preview_can_retry_without_bad_document(client, pdf_bytes, monkeypatch):
    paper_id = add_paper()

    def fail(*args):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(documents, "download_arxiv", fail)
    assert client.post(f"/api/papers/{paper_id}/preview").status_code == 502
    assert not store.rows("SELECT * FROM documents")
    monkeypatch.setattr(documents, "download_arxiv", lambda *args: pdf_bytes)
    assert client.post(f"/api/papers/{paper_id}/preview").status_code == 200


def test_invalid_download_is_not_cached(client, monkeypatch):
    paper_id = add_paper()
    monkeypatch.setattr(documents, "download_arxiv", lambda *args: b"<html>Error</html>")
    assert client.post(f"/api/papers/{paper_id}/preview").status_code == 422
    assert not store.rows("SELECT * FROM documents")
