"""Versioned PDF annotations and explicitly selected personal reading material."""

import hashlib
import json
import re
import time
import unicodedata
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pypdf import PdfReader

from . import retrieval, settings, store

router = APIRouter()
COLLECTION = "personal_e5_small_v1"


def initialize(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS annotations(
          id TEXT PRIMARY KEY, paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
          file_hash TEXT NOT NULL, page INTEGER NOT NULL, quote TEXT NOT NULL,
          prefix TEXT NOT NULL, suffix TEXT NOT NULL, rects TEXT NOT NULL,
          comment TEXT NOT NULL, color TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 0,
          revision INTEGER NOT NULL, anchor_status TEXT NOT NULL,
          created_at REAL NOT NULL, updated_at REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS annotations_source ON annotations(paper_id,file_hash,page);
        CREATE TABLE IF NOT EXISTS annotation_versions(
          annotation_id TEXT REFERENCES annotations(id) ON DELETE CASCADE,
          revision INTEGER NOT NULL, body TEXT NOT NULL, created_at REAL NOT NULL,
          PRIMARY KEY(annotation_id,revision));
        CREATE TABLE IF NOT EXISTS personal_notes(
          task_id TEXT PRIMARY KEY REFERENCES notebooks(task_id) ON DELETE CASCADE,
          note_version INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS personal_chunks(
          id TEXT PRIMARY KEY, source_key TEXT NOT NULL, signature TEXT NOT NULL, text TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS personal_chunk_source ON personal_chunks(source_key);
        CREATE VIRTUAL TABLE IF NOT EXISTS personal_fts USING fts5(
          chunk_id UNINDEXED,text,tokenize='unicode61');
    """)


class AnnotationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    paper_id: int = Field(ge=1)
    file_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    page: int = Field(ge=1, le=250)
    quote: str = Field(min_length=1, max_length=6000)
    prefix: str = Field(default="", max_length=200)
    suffix: str = Field(default="", max_length=200)
    rects: list[tuple[float, float, float, float]] = Field(min_length=1, max_length=200)
    comment: str = Field(default="", max_length=6000)
    color: Literal["yellow", "green", "pink"] = "yellow"
    enabled: bool = False

    @field_validator("quote")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("请选择非空文字。")
        return value.strip()


class AnnotationEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    comment: str = Field(max_length=6000)
    color: Literal["yellow", "green", "pink"]
    enabled: bool


class NoteSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    enabled: bool


def normalized(text):
    # PDF.js and pypdf may differ in ligatures, whitespace and soft hyphens.
    value = unicodedata.normalize("NFKC", text).replace("\u00ad", "")
    return re.sub(r"\s+", "", value)


def unpack(row):
    value = dict(row)
    value["rects"] = json.loads(value["rects"])
    value["enabled"] = bool(value["enabled"])
    return value


def annotation(annotation_id):
    value = store.one("SELECT * FROM annotations WHERE id=?", (annotation_id,))
    if not value:
        raise HTTPException(404, "批注已删除或不存在。")
    return unpack(value)


def record(db, annotation_id):
    value = dict(db.execute("SELECT * FROM annotations WHERE id=?", (annotation_id,)).fetchone())
    db.execute("INSERT INTO annotation_versions VALUES(?,?,?,?)",
               (annotation_id, value["revision"], store.dumps(unpack(value)), time.time()))


@router.get("/api/annotations")
def list_annotations(paper_id: int = Query(ge=1),
                     file_hash: str = Query(pattern=r"^[a-f0-9]{64}$")):
    return [unpack(row) for row in store.rows(
        "SELECT * FROM annotations WHERE paper_id=? AND file_hash=? ORDER BY page,created_at",
        (paper_id, file_hash))]


@router.post("/api/annotations", status_code=201)
def create_annotation(req: AnnotationCreate):
    doc = store.one("SELECT * FROM documents WHERE paper_id=? AND file_hash=? ORDER BY id DESC LIMIT 1",
                    (req.paper_id, req.file_hash))
    path = settings.DATA / "pdf" / f"{req.file_hash}.pdf"
    if not doc or not path.is_file():
        raise HTTPException(409, "此 PDF 已不在文献库中，无法新增批注。")
    reader = PdfReader(path)
    if req.page > len(reader.pages):
        raise HTTPException(422, "批注页码超出 PDF 范围。")
    page = reader.pages[req.page - 1]
    bounds = list(map(float, page.cropbox))
    for x1, y1, x2, y2 in req.rects:
        if not (x1 < x2 and y1 < y2 and bounds[0]-2 <= x1 < x2 <= bounds[2]+2 and
                bounds[1]-2 <= y1 < y2 <= bounds[3]+2):
            raise HTTPException(422, "批注位置超出页面范围。")
    quote_status = "matched" if normalized(req.quote) in normalized(page.extract_text() or "") else "unmatched"
    now, key = time.time(), str(uuid.uuid4())
    with store.connect() as db:
        db.execute("""INSERT INTO annotations VALUES(?,?,?,?,?,?,?,?,?,?,?,1,?,?,?)""",
                   (key, req.paper_id, req.file_hash, req.page, req.quote, req.prefix, req.suffix,
                    store.dumps(req.rects), req.comment.strip(), req.color, int(req.enabled),
                    quote_status, now, now))
        record(db, key)
    return annotation(key)


@router.get("/api/annotations/{annotation_id}")
def detail_annotation(annotation_id: str):
    return {**annotation(annotation_id), "versions": [
        json.loads(row["body"]) for row in store.rows(
            "SELECT body FROM annotation_versions WHERE annotation_id=? ORDER BY revision DESC",
            (annotation_id,))]}


@router.put("/api/annotations/{annotation_id}")
def edit_annotation(annotation_id: str, req: AnnotationEdit):
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM annotations WHERE id=?", (annotation_id,)).fetchone()
        if not row:
            raise HTTPException(404, "批注已删除或不存在。")
        if row["revision"] != req.expected_revision:
            raise HTTPException(409, "批注已被修改，请重新打开后保存。")
        db.execute("""UPDATE annotations SET comment=?,color=?,enabled=?,revision=revision+1,updated_at=?
                      WHERE id=?""",
                   (req.comment.strip(), req.color, int(req.enabled), time.time(), annotation_id))
        record(db, annotation_id)
    return annotation(annotation_id)


@router.delete("/api/annotations/{annotation_id}")
def delete_annotation(annotation_id: str, expected_revision: int = Query(ge=1)):
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT revision FROM annotations WHERE id=?", (annotation_id,)).fetchone()
        if not row:
            raise HTTPException(404, "批注已删除或不存在。")
        if row["revision"] != expected_revision:
            raise HTTPException(409, "批注已更新，请重新加载后删除。")
        db.execute("DELETE FROM annotations WHERE id=?", (annotation_id,))
    return {"deleted": True}


def note_selection(task_id):
    row = store.one("""SELECT p.note_version FROM personal_notes p JOIN notes n ON n.task_id=p.task_id
        AND n.version=p.note_version JOIN notebooks b ON b.task_id=p.task_id
        WHERE p.task_id=? AND b.trashed_at IS NULL
        AND n.version=(SELECT max(version) FROM notes WHERE task_id=p.task_id)""", (task_id,))
    return row["note_version"] if row else None


@router.put("/api/personal/notes/{task_id}")
def select_note(task_id: str, req: NoteSelection):
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        note = db.execute("SELECT * FROM notebooks WHERE task_id=?", (task_id,)).fetchone()
        if not note or note["trashed_at"] is not None:
            raise HTTPException(409, "笔记不存在或已在回收站。")
        latest = db.execute("SELECT max(version) FROM notes WHERE task_id=?", (task_id,)).fetchone()[0]
        if latest != req.version:
            raise HTTPException(409, "只能选择最新笔记版本，请重新加载。")
        if req.enabled:
            db.execute("INSERT OR REPLACE INTO personal_notes VALUES(?,?)", (task_id, req.version))
        else:
            db.execute("DELETE FROM personal_notes WHERE task_id=?", (task_id,))
    return {"version": req.version, "enabled": req.enabled}


def candidates(paper_ids, exclude_task=None, db=None):
    """Only current, opted-in material whose complete source scope is selected."""
    if db is None:
        with store.connect() as connection:
            return candidates(paper_ids, exclude_task, connection)
    if not paper_ids:
        return []
    placeholders = ",".join("?" for _ in paper_ids)
    docs = [dict(r) for r in db.execute(
        f"SELECT * FROM documents WHERE status='ready' AND paper_id IN ({placeholders})", paper_ids)]
    active = {(d["paper_id"], d["file_hash"]) for d in docs}
    result = []
    for row in db.execute(f"""SELECT a.*,p.title FROM annotations a JOIN papers p ON p.id=a.paper_id
        WHERE a.enabled=1 AND a.paper_id IN ({placeholders}) ORDER BY a.created_at""", paper_ids):
        if (row["paper_id"], row["file_hash"]) not in active:
            continue
        result.append({"source_key": "annotation:" + row["id"], "source_id": row["id"], "kind": "annotation",
                       "version": row["revision"], "title": row["title"], "paper_ids": [row["paper_id"]],
                       "file_hash": row["file_hash"], "page": row["page"], "anchor_status": row["anchor_status"],
                       "text": "选中文本（用户摘录）：" + row["quote"] + "\n个人批注：" + row["comment"]})
    for row in db.execute("""SELECT b.task_id,b.title,n.body,n.version FROM personal_notes p
        JOIN notebooks b ON b.task_id=p.task_id JOIN notes n ON n.task_id=p.task_id AND n.version=p.note_version
        WHERE b.trashed_at IS NULL AND n.version=(SELECT max(version) FROM notes WHERE task_id=p.task_id)"""):
        if row["task_id"] == exclude_task:
            continue
        sources = db.execute("SELECT paper_id,file_hash FROM note_sources WHERE task_id=?", (row["task_id"],)).fetchall()
        if not sources or any((s["paper_id"], s["file_hash"]) not in active for s in sources):
            continue
        # A selected note is personal material, not a way to promote old citation markers to evidence.
        body = re.sub(r"\[(?:cite|personal):[^\]]+\]", "", row["body"])
        result.append({"source_key": "note:" + row["task_id"], "source_id": row["task_id"], "kind": "note",
                       "version": row["version"], "title": row["title"],
                       "paper_ids": sorted({s["paper_id"] for s in sources}), "text": body})
    for item in result:
        item["signature"] = hashlib.sha256(store.dumps(item).encode()).hexdigest()
    return result


def scope_signature(paper_ids):
    return hashlib.sha256(store.dumps([(s["source_key"], s["signature"])
                                      for s in candidates(paper_ids)]).encode()).hexdigest()


def validate_sources(sources, paper_ids, exclude_task=None, db=None):
    if not sources:
        return
    active = {s["source_key"]: s["signature"] for s in candidates(paper_ids, exclude_task, db)}
    if any(active.get(s["source_key"]) != s["signature"] for s in sources):
        raise ValueError("使用的个人笔记或批注已修改、停用或删除，请重新发起阅读。")


def search(query, paper_ids, exclude_task=None, limit=5, guard=None):
    """Independent hybrid index; stale vectors are never a visibility authority."""
    from qdrant_client import models

    sources = candidates(paper_ids, exclude_task)
    if not sources:
        return []
    active = {s["source_key"]: s for s in sources}
    with retrieval._lock:
        client, model = retrieval.vector_client(), retrieval.get_embedder()
        created = not client.collection_exists(COLLECTION)
        if created:
            client.create_collection(COLLECTION, vectors_config=models.VectorParams(
                size=384, distance=models.Distance.COSINE))
        for source in sources:
            if guard:
                guard.check()
            old = store.rows("SELECT * FROM personal_chunks WHERE source_key=?", (source["source_key"],))
            if not created and old and all(c["signature"] == source["signature"] for c in old):
                continue
            texts = list(retrieval.split_page(source["title"] + "\n" + source["text"]))
            points = []
            for offset in range(0, len(texts), 32):
                if guard:
                    guard.check()
                batch = texts[offset:offset+32]
                vectors = model.encode(["passage: " + t for t in batch], normalize_embeddings=True,
                                       show_progress_bar=False)
                batch_points = []
                for index, (text, vector) in enumerate(zip(batch, vectors), offset):
                    key = str(uuid.uuid5(uuid.NAMESPACE_URL, source["signature"] + f":{index}"))
                    points.append((key, text))
                    batch_points.append(models.PointStruct(
                        id=key, vector=vector.tolist(), payload={"source_key": source["source_key"],
                                                                "signature": source["signature"]}))
                client.upsert(COLLECTION, points=batch_points, wait=True)
            with store.connect() as db:
                for chunk in old:
                    db.execute("DELETE FROM personal_fts WHERE chunk_id=?", (chunk["id"],))
                db.execute("DELETE FROM personal_chunks WHERE source_key=?", (source["source_key"],))
                for key, text in points:
                    db.execute("INSERT INTO personal_chunks VALUES(?,?,?,?)",
                               (key, source["source_key"], source["signature"], text))
                    db.execute("INSERT INTO personal_fts VALUES(?,?)", (key, text))
            replaced_ids = {key for key, _ in points}
            obsolete = [c["id"] for c in old if c["id"] not in replaced_ids]
            if obsolete:
                client.delete(COLLECTION, points_selector=models.PointIdsList(
                    points=obsolete), wait=True)
        # Delete withdrawn versions as well; the SQL recheck below also covers concurrent edits.
        all_active = {s["signature"] for s in candidates(
            [r["id"] for r in store.rows("SELECT id FROM papers")])}
        stale = [c for c in store.rows("SELECT id,signature FROM personal_chunks") if c["signature"] not in all_active]
        if stale:
            client.delete(COLLECTION, points_selector=models.PointIdsList(points=[c["id"] for c in stale]), wait=True)
            with store.connect() as db:
                for c in stale:
                    db.execute("DELETE FROM personal_fts WHERE chunk_id=?", (c["id"],))
                    db.execute("DELETE FROM personal_chunks WHERE id=?", (c["id"],))
        signatures = [s["signature"] for s in sources]
        query_vector = model.encode(["query: " + query], normalize_embeddings=True,
                                    show_progress_bar=False)[0].tolist()
        dense = client.query_points(COLLECTION, query=query_vector, limit=30,
                                    query_filter=models.Filter(must=[models.FieldCondition(
                                        key="signature", match=models.MatchAny(any=signatures))])).points
        terms = re.findall(r"\w+", query)[:32]
        lexical = []
        if terms:
            lexical = store.rows(f"""SELECT c.id FROM personal_fts JOIN personal_chunks c
                ON c.id=personal_fts.chunk_id WHERE personal_fts MATCH ?
                AND c.signature IN ({','.join('?' for _ in signatures)})
                ORDER BY bm25(personal_fts) LIMIT 30""",
                (" OR ".join('"' + t + '"' for t in terms), *signatures))
        scores = {}
        for ranking in ([r["id"] for r in lexical], [str(p.id) for p in dense]):
            for rank, key in enumerate(ranking, 1):
                scores[key] = scores.get(key, 0) + 1 / (60 + rank)
        fresh = {s["source_key"]: s["signature"] for s in candidates(paper_ids, exclude_task)}
        results, counts = [], {}
        for key in sorted(scores, key=scores.get, reverse=True):
            chunk = store.one("SELECT * FROM personal_chunks WHERE id=?", (key,))
            if not chunk or fresh.get(chunk["source_key"]) != chunk["signature"]:
                continue
            # A long note must not occupy the complete personal-material budget.
            if counts.get(chunk["source_key"], 0) >= 2:
                continue
            counts[chunk["source_key"]] = counts.get(chunk["source_key"], 0) + 1
            results.append({**active[chunk["source_key"]], "id": key, "text": chunk["text"]})
            if len(results) == limit:
                break
        return results


def material(sources):
    return store.dumps([{**s, "citation": f"[personal:{s['id']}]"} for s in sources])


def check_citations(answer, sources):
    allowed = {s["id"]: s for s in sources}
    used = list(dict.fromkeys(re.findall(r"\[personal:([^\]]+)\]", answer)))
    if any(key not in allowed for key in used):
        raise ValueError("回答包含未提供的个人笔记引用。")
    return [allowed[key] for key in used]


@router.get("/api/personal/evidence/{task_id}/{chunk_id}")
def evidence(task_id: str, chunk_id: str):
    task = store.get_task(task_id)
    sources = (task or {}).get("result") or {}
    source = next((s for s in sources.get("personal_sources", []) if s["id"] == chunk_id), None)
    if not source:
        raise HTTPException(404, "此回答未使用该个人材料。")
    try:
        validate_sources([source], task["payload"]["paper_ids"])
        current = True
    except ValueError:
        current = False
    pdf_url = None
    if source["kind"] == "annotation":
        document = store.one("SELECT document_id FROM note_sources WHERE task_id=? AND file_hash=?",
                             (task_id, source["file_hash"]))
        if document:
            pdf_url = f"/api/notes/{task_id}/sources/{document['document_id']}/pdf#page={source['page']}"
    return {**source, "current": current, "pdf_url": pdf_url}
