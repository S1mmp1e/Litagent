import hashlib
import io
import re
import time
import uuid
from urllib.parse import urlsplit

import httpx
from pypdf import PdfReader

from . import retrieval, store
from .settings import DATA, INDEX_VERSION, MAX_PDF_BYTES, PARSER_VERSION, prepare_dirs

ARXIV_ID = re.compile(r"(?:\d{4}\.\d{4,5}|[a-zA-Z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?")


def save_pdf(content):
    if not content.startswith(b"%PDF-") or len(content) > MAX_PDF_BYTES:
        raise ValueError("仅支持不超过 25 MB 的 PDF 文件。")
    digest = hashlib.sha256(content).hexdigest()
    prepare_dirs()
    path = DATA / "pdf" / f"{digest}.pdf"
    if not path.exists():
        temp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            temp.write_bytes(content)
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)
    return digest


def preview_info(paper_id):
    paper = store.one("SELECT * FROM papers WHERE id=?", (paper_id,))
    if not paper:
        raise LookupError("论文不存在。")
    arxiv_id = paper["arxiv_id"] or ""
    can_download = bool(ARXIV_ID.fullmatch(arxiv_id))
    source_url = f"https://arxiv.org/pdf/{arxiv_id}" if can_download else paper["pdf_url"] or ""
    try:
        url = urlsplit(source_url)
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
            source_url = ""
    except ValueError:
        source_url = ""
    result = {"paper_id": paper_id, "title": paper["title"], "pdf_url": None,
              "source_url": source_url, "can_download": can_download, "page_count": None}
    # A downloaded/uploaded file can be viewed before text extraction or indexing.
    for doc in store.rows("SELECT * FROM documents WHERE paper_id=? ORDER BY id DESC", (paper_id,)):
        if (DATA / "pdf" / f"{doc['file_hash']}.pdf").is_file():
            result.update(pdf_url=f"/api/documents/{doc['id']}/pdf", page_count=doc["page_count"],
                          document_id=doc["id"], file_hash=doc["file_hash"])
            break
    return result


def download_arxiv(arxiv_id, guard):
    if not ARXIV_ID.fullmatch(arxiv_id):
        raise ValueError("请先上传 PDF；在线下载仅支持合法 arXiv ID。")
    content = bytearray()
    with httpx.stream("GET", f"https://arxiv.org/pdf/{arxiv_id}",
                      follow_redirects=False, timeout=min(30, guard.remaining())) as response:
        response.raise_for_status()
        for block in response.iter_bytes():
            guard.check()
            content.extend(block)
            if len(content) > MAX_PDF_BYTES:
                raise ValueError("PDF 超过 25 MB。")
    guard.check()
    return bytes(content)


def cache_preview(paper_id, guard):
    info = preview_info(paper_id)
    if info["pdf_url"]:
        return info
    if not info["can_download"]:
        raise ValueError("暂无本地 PDF，请导入文件或打开原文链接。")
    paper = store.one("SELECT arxiv_id FROM papers WHERE id=?", (paper_id,))
    if not paper:
        raise LookupError("论文不存在。")
    content = download_arxiv(paper["arxiv_id"], guard)
    reader = PdfReader(io.BytesIO(content))
    if reader.is_encrypted or not 0 < len(reader.pages) <= 250:
        raise ValueError("不支持加密 PDF 或超过 250 页的文件。")
    guard.check()
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT 1 FROM papers WHERE id=?", (paper_id,)).fetchone():
            raise LookupError("论文已不存在。")
        digest = save_pdf(content)
        db.execute("""INSERT OR IGNORE INTO documents
                      (paper_id,file_hash,source,parser,index_version,status,page_count,created_at)
                      VALUES(?,?,'arxiv',?,?,'uploaded',?,?)""",
                   (paper_id, digest, PARSER_VERSION, INDEX_VERSION, len(reader.pages), time.time()))
    return preview_info(paper_id)


def upload(content, title):
    # Validate before creating the library entry; detailed extraction happens in the worker.
    reader = PdfReader(io.BytesIO(content))
    if reader.is_encrypted or not 0 < len(reader.pages) <= 250:
        raise ValueError("不支持加密 PDF 或超过 250 页的文件。")
    digest = save_pdf(content)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT paper_id FROM documents WHERE file_hash=? LIMIT 1", (digest,)).fetchone()
        if existing:
            paper_id = existing["paper_id"]
        else:
            cursor = db.execute("""INSERT INTO papers(title,authors,abstract,created_at)
                                   VALUES(?,'[]','',datetime('now'))""", (title[:500],))
            paper_id = cursor.lastrowid
            db.execute("""INSERT INTO documents(paper_id,file_hash,source,parser,index_version,status,created_at)
                          VALUES(?,?,'upload',?,?,'uploaded',?)""",
                       (paper_id, digest, PARSER_VERSION, INDEX_VERSION, time.time()))
    return paper_id, digest


def process(task, guard):
    payload = task["payload"]
    paper_id = payload["paper_id"]
    paper = store.one("SELECT * FROM papers WHERE id=?", (paper_id,))
    if not paper:
        raise ValueError("论文已不存在。")
    state = task["checkpoint"] or {}
    digest = payload.get("file_hash") or state.get("file_hash")
    if not digest:
        store.checkpoint(task["id"], "downloading", state)
        digest = save_pdf(download_arxiv(paper["arxiv_id"] or "", guard))
        state["file_hash"] = digest
        store.checkpoint(task["id"], "downloaded", state)
    path = DATA / "pdf" / f"{digest}.pdf"
    if not path.is_file():
        raise ValueError("本地 PDF 文件不存在，请重新上传。")
    existing = store.one("""SELECT * FROM documents WHERE paper_id=? AND file_hash=?
                            AND parser=? AND index_version=?""",
                         (paper_id, digest, PARSER_VERSION, INDEX_VERSION))
    if existing and existing["status"] == "ready" and not payload.get("force"):
        return {"paper_id": paper_id, "document_id": existing["id"], "reused": True}
    guard.check()
    store.checkpoint(task["id"], "parsing", state)
    reader = PdfReader(path)
    if reader.is_encrypted or not 0 < len(reader.pages) <= 250:
        raise ValueError("不支持加密 PDF 或超过 250 页的文件。")
    pages = []
    for number, page in enumerate(reader.pages, 1):
        guard.check()
        pages.append((number, (page.extract_text() or "").strip()))
    if sum(len(text) for _, text in pages) < 100:
        raise ValueError("未提取到足够文字，可能是扫描件。第一版不支持 OCR。")
    if sum(len(text) for _, text in pages) > 1_000_000:
        raise ValueError("文档文本过长，请拆分后导入。")
    with store.connect() as db:
        if existing:
            document_id = existing["id"]
            db.execute("UPDATE documents SET status='processing' WHERE id=?", (document_id,))
            db.execute("DELETE FROM chunks_fts WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id=?)",
                       (document_id,))
            db.execute("DELETE FROM chunks WHERE document_id=?", (document_id,))
            db.execute("DELETE FROM pages WHERE document_id=?", (document_id,))
        else:
            cursor = db.execute("""INSERT INTO documents(paper_id,file_hash,source,parser,index_version,status,created_at)
                                   VALUES(?,?,?,?,?,'processing',?)""",
                                (paper_id, digest, "upload" if payload.get("file_hash") else "arxiv",
                                 PARSER_VERSION, INDEX_VERSION, time.time()))
            document_id = cursor.lastrowid
        db.executemany("INSERT INTO pages VALUES(?,?,?)", [(document_id, n, t) for n, t in pages])
    state.update({"document_id": document_id, "file_hash": digest})
    store.checkpoint(task["id"], "parsed", state)
    # Remove abandoned points before rebuilding the same version.
    retrieval.purge_documents([document_id])
    chunks = []
    for number, text in pages:
        guard.check()
        for ordinal, part in enumerate(retrieval.split_page(text)):
            if part.strip():
                chunk_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"lit:{document_id}:{number}:{ordinal}:{digest}"))
                chunks.append({"id": chunk_id, "document_id": document_id, "paper_id": paper_id,
                               "page": number, "ordinal": ordinal, "text": part})
    store.checkpoint(task["id"], "indexing", state)
    guard.check()
    retrieval.index_chunks(chunks)
    guard.check()
    with store.connect() as db:
        if not db.execute("SELECT 1 FROM papers WHERE id=?", (paper_id,)).fetchone():
            raise ValueError("论文在处理中被删除。")
        db.executemany("INSERT INTO chunks VALUES(:id,:document_id,:paper_id,:page,:ordinal,:text)", chunks)
        db.executemany("INSERT INTO chunks_fts(chunk_id,text) VALUES(?,?)", [(c["id"], c["text"]) for c in chunks])
        db.execute("UPDATE documents SET status='retired' WHERE paper_id=? AND id!=?",
                   (paper_id, document_id))
        db.execute("UPDATE documents SET status='ready',page_count=? WHERE id=?", (len(pages), document_id))
    old_ids = [r["id"] for r in store.rows(
        "SELECT id FROM documents WHERE paper_id=? AND status='retired'", (paper_id,))]
    if old_ids:
        store.enqueue("cleanup", {"document_ids": old_ids})
    return {"paper_id": paper_id, "document_id": document_id, "pages": len(pages), "chunks": len(chunks)}


def delete_paper(paper_id):
    doc_ids = [d["id"] for d in store.rows("SELECT id FROM documents WHERE paper_id=?", (paper_id,))]
    digests = [d["file_hash"] for d in store.rows("SELECT file_hash FROM documents WHERE paper_id=?", (paper_id,))]
    with store.connect() as db:
        db.execute("DELETE FROM chunks_fts WHERE chunk_id IN (SELECT id FROM chunks WHERE paper_id=?)", (paper_id,))
        db.execute("DELETE FROM paper_tags WHERE paper_id=?", (paper_id,))
        db.execute("DELETE FROM paper_categories WHERE paper_id=?", (paper_id,))
        db.execute("DELETE FROM papers WHERE id=?", (paper_id,))
    # SQL removal hides stale vector points even if physical cleanup needs retry.
    try:
        retrieval.purge_documents(doc_ids)
    except Exception:
        store.enqueue("cleanup", {"document_ids": doc_ids})
    for digest in digests:
        if (not store.one("SELECT 1 FROM documents WHERE file_hash=? LIMIT 1", (digest,)) and
                not store.one("SELECT 1 FROM note_sources WHERE file_hash=? LIMIT 1", (digest,))):
            (DATA / "pdf" / f"{digest}.pdf").unlink(missing_ok=True)
