"""Notebook catalog and immutable source snapshots independent of the task list."""

import io
import json
import re
import time
import zipfile
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import settings, store

router = APIRouter()


def initialize(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS notebooks(
          task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
          title TEXT NOT NULL, starred INTEGER NOT NULL DEFAULT 0, trashed_at REAL,
          created_at REAL NOT NULL, updated_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS note_sources(
          task_id TEXT NOT NULL REFERENCES notebooks(task_id) ON DELETE CASCADE,
          document_id INTEGER NOT NULL, paper_id INTEGER NOT NULL, title TEXT NOT NULL,
          file_hash TEXT NOT NULL, page_count INTEGER NOT NULL, position INTEGER NOT NULL,
          PRIMARY KEY(task_id,document_id));
        CREATE INDEX IF NOT EXISTS note_sources_paper ON note_sources(paper_id);
        CREATE INDEX IF NOT EXISTS note_sources_hash ON note_sources(file_hash);
        CREATE TABLE IF NOT EXISTS note_evidence(
          task_id TEXT NOT NULL REFERENCES notebooks(task_id) ON DELETE CASCADE,
          chunk_id TEXT NOT NULL, document_id INTEGER NOT NULL, paper_id INTEGER NOT NULL,
          page INTEGER NOT NULL, text TEXT NOT NULL, PRIMARY KEY(task_id,chunk_id));
    """)
    # Backfill once per existing reading, without changing its text or revisions.
    rows = db.execute("""SELECT t.* FROM tasks t WHERE t.kind='reading'
        AND EXISTS(SELECT 1 FROM notes n WHERE n.task_id=t.id)
        AND NOT EXISTS(SELECT 1 FROM notebooks b WHERE b.task_id=t.id)""").fetchall()
    for row in rows:
        task = dict(row)
        task["payload"] = json.loads(task["payload"])
        register(db, task, json.loads(task["result"] or "{}"))


def register(db, task, result):
    task_id = task["id"]
    now = time.time()
    title = str(task["payload"].get("message") or "阅读笔记").strip().splitlines()[0][:160]
    db.execute("INSERT OR IGNORE INTO notebooks VALUES(?,?,0,NULL,?,?)",
               (task_id, title, task["created_at"], now))
    for position, document_id in enumerate(result.get("document_ids", [])):
        doc = db.execute("""SELECT d.*,p.title FROM documents d JOIN papers p ON p.id=d.paper_id
                            WHERE d.id=?""", (document_id,)).fetchone()
        if doc:
            db.execute("INSERT OR IGNORE INTO note_sources VALUES(?,?,?,?,?,?,?)",
                       (task_id, document_id, doc["paper_id"], doc["title"], doc["file_hash"],
                        doc["page_count"], position))
    for citation in result.get("citations", []):
        chunk = db.execute("SELECT * FROM chunks WHERE id=?", (citation["id"],)).fetchone()
        if chunk:
            db.execute("INSERT OR IGNORE INTO note_evidence VALUES(?,?,?,?,?,?)",
                       (task_id, chunk["id"], chunk["document_id"], chunk["paper_id"], chunk["page"], chunk["text"]))


def required(task_id):
    value = store.one("SELECT * FROM notebooks WHERE task_id=?", (task_id,))
    if not value:
        raise HTTPException(404, "笔记不存在。")
    return value


def sources(task_id):
    rows = store.rows("SELECT * FROM note_sources WHERE task_id=? ORDER BY position", (task_id,))
    for row in rows:
        row["pdf_url"] = f"/api/notes/{task_id}/sources/{row['document_id']}/pdf"
        row["available"] = (settings.DATA / "pdf" / f"{row['file_hash']}.pdf").is_file()
        row["in_library"] = bool(store.one(
            "SELECT 1 FROM documents WHERE paper_id=? AND file_hash=?", (row["paper_id"], row["file_hash"])))
    return rows


def evidence(task_id):
    return store.rows("""SELECT chunk_id AS id,document_id,paper_id,page,text FROM note_evidence
                         WHERE task_id=? ORDER BY document_id,page,chunk_id""", (task_id,))


class MetadataRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=160)
    starred: bool | None = None
    trashed: bool | None = None

    @field_validator("title")
    @classmethod
    def title_nonempty(cls, value):
        if value is not None and not value.strip():
            raise ValueError("标题不能为空。")
        return value.strip() if value else value


@router.get("/api/notes")
def catalog(query: str = Query(default="", max_length=300), paper_id: int | None = None,
            category_id: int | None = None, skill_id: str | None = None,
            view: str = Query(default="all", pattern="^(all|starred|trash)$"),
            limit: int = Query(default=40, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    clauses = ["b.trashed_at IS NOT NULL" if view == "trash" else "b.trashed_at IS NULL"]
    args = []
    if view == "starred":
        clauses.append("b.starred=1")
    if query.strip():
        clauses.append("""(instr(lower(b.title),lower(?))>0 OR instr(lower(n.body),lower(?))>0
            OR EXISTS(SELECT 1 FROM note_sources s WHERE s.task_id=b.task_id AND instr(lower(s.title),lower(?))>0))""")
        args.extend([query.strip()] * 3)
    if paper_id is not None:
        clauses.append("EXISTS(SELECT 1 FROM note_sources s WHERE s.task_id=b.task_id AND s.paper_id=?)")
        args.append(paper_id)
    if category_id is not None:
        clauses.append("""EXISTS(SELECT 1 FROM note_sources s JOIN paper_categories pc ON pc.paper_id=s.paper_id
            JOIN documents d ON d.paper_id=s.paper_id AND d.file_hash=s.file_hash
            WHERE s.task_id=b.task_id AND pc.category_id=? AND pc.status='approved')""")
        args.append(category_id)
    if skill_id:
        clauses.append("json_extract(t.payload,'$.skill_id')=?")
        args.append(skill_id)
    base = """FROM notebooks b JOIN tasks t ON t.id=b.task_id JOIN notes n ON n.id=(
        SELECT id FROM notes WHERE task_id=b.task_id ORDER BY version DESC LIMIT 1) WHERE """ + " AND ".join(clauses)
    total = store.one("SELECT count(*) AS total " + base, args)["total"]
    rows = store.rows("""SELECT b.*,n.version,n.approved,substr(n.body,1,180) AS excerpt,
        json_extract(t.payload,'$.skill_id') AS skill_id,json_extract(t.payload,'$.action') AS action,
        json_extract(t.payload,'$.template_snapshot.name') AS template_name,
        t.session_id """ + base + " ORDER BY b.starred DESC,b.updated_at DESC,b.task_id LIMIT ? OFFSET ?",
                      (*args, limit, offset))
    for row in rows:
        row["sources"] = sources(row["task_id"])
    return {"items": rows, "total": total, "offset": offset, "limit": limit}


@router.get("/api/notes/paper-counts")
def paper_counts():
    return store.rows("""SELECT s.paper_id,count(DISTINCT s.task_id) AS count FROM note_sources s
        JOIN notebooks b ON b.task_id=s.task_id WHERE b.trashed_at IS NULL
        AND EXISTS(SELECT 1 FROM documents d WHERE d.paper_id=s.paper_id AND d.file_hash=s.file_hash)
        GROUP BY s.paper_id""")


@router.get("/api/notes/latest")
def latest_for_papers(paper_id: list[int] = Query(min_length=1, max_length=2)):
    ids = sorted(set(paper_id))
    if any(value < 1 for value in ids):
        raise HTTPException(422, "论文 ID 必须为正整数。")
    placeholders = ",".join("?" for _ in ids)
    # Exact scope: a comparison note must not appear as a single-paper note.
    return store.one(f"""SELECT b.task_id FROM notebooks b JOIN tasks t ON t.id=b.task_id
        WHERE b.trashed_at IS NULL AND t.kind='reading' AND t.status='succeeded'
        AND EXISTS(SELECT 1 FROM notes n WHERE n.task_id=b.task_id)
        AND (SELECT count(DISTINCT s.paper_id) FROM note_sources s WHERE s.task_id=b.task_id)=?
        AND NOT EXISTS(SELECT 1 FROM note_sources s WHERE s.task_id=b.task_id
                       AND s.paper_id NOT IN ({placeholders}))
        ORDER BY b.updated_at DESC,b.created_at DESC,b.task_id LIMIT 1""", (len(ids), *ids))


@router.get("/api/notes/{task_id}")
def detail(task_id: str):
    from .personal import note_selection
    note = required(task_id)
    task = store.get_task(task_id)
    payload = task["payload"]
    return {**note, "sources": sources(task_id), "citations": evidence(task_id),
            "retrieval_version": note_selection(task_id),
            "versions": store.rows("SELECT * FROM notes WHERE task_id=? ORDER BY version DESC", (task_id,)),
            "skill_id": payload.get("skill_id"), "action": payload.get("action"),
            "question": payload.get("message"), "session_id": task["session_id"],
            "template": payload.get("template_snapshot"),
            "template_check": (task["result"] or {}).get("template_check"),
            "model": (task["result"] or {}).get("model")}


@router.patch("/api/notes/{task_id}")
def metadata(task_id: str, req: MetadataRequest):
    required(task_id)
    values = req.model_dump(exclude_none=True)
    if values:
        updates, args = [], []
        for key, value in values.items():
            if key == "trashed":
                updates.append("trashed_at=?")
                args.append(time.time() if value else None)
            else:
                updates.append(key + "=?")
                args.append(value)
        with store.connect() as db:
            db.execute("UPDATE notebooks SET " + ",".join(updates) + ",updated_at=? WHERE task_id=?",
                       (*args, time.time(), task_id))
    return detail(task_id)


@router.get("/api/notes/{task_id}/sources/{document_id}/pdf")
def source_pdf(task_id: str, document_id: int):
    required(task_id)
    source = store.one("SELECT * FROM note_sources WHERE task_id=? AND document_id=?", (task_id, document_id))
    if not source:
        raise HTTPException(404, "这篇笔记未关联该原文。")
    path = settings.DATA / "pdf" / f"{source['file_hash']}.pdf"
    if not path.is_file():
        raise HTTPException(404, "原始 PDF 已缺失，请从备份恢复。")
    return FileResponse(path, media_type="application/pdf", filename=f"paper-{source['paper_id']}.pdf",
                        content_disposition_type="inline")


def markdown_export(note, version, portable=False, base_url=""):
    evidence_by_id = {c["id"]: c for c in note["citations"]}
    sources_by_id = {s["document_id"]: s for s in note["sources"]}
    used = []

    def replace(match):
        key = match[1]
        if key not in evidence_by_id:
            return match[0]
        if key not in used:
            used.append(key)
        return f"[^{used.index(key)+1}]"

    body = re.sub(r"\[cite:([a-f0-9-]{36})\]", replace, version["body"])
    task = store.get_task(note["task_id"])
    personal = {s["id"]: s for s in (task["result"] or {}).get("personal_sources", [])}
    personal_used = []

    def replace_personal(match):
        if match[1] not in personal:
            return match[0]
        if match[1] not in personal_used:
            personal_used.append(match[1])
        return f"[^personal-{personal_used.index(match[1])+1}]"

    body = re.sub(r"\[personal:([a-f0-9-]{36})\]", replace_personal, body)
    stamp = datetime.fromtimestamp(version["created_at"], timezone.utc).isoformat()
    header = f"# {note['title']}\n\n版本：{version['version']}  \n保存时间：{stamp}  \n"
    if note["template"]:
        header += f"笔记模板：{note['template']['name']} v{note['template']['version']}  \n"
    header += "\n## 关联论文\n\n"
    for source in note["sources"]:
        target = f"pdf/{source['file_hash']}.pdf" if portable else base_url + source["pdf_url"]
        header += f"- [{source['title']}]({target})\n"
    result = header + "\n---\n\n" + body + "\n\n"
    for number, key in enumerate(used, 1):
        citation = evidence_by_id[key]
        source = sources_by_id.get(citation["document_id"])
        if not source:
            continue
        target = f"pdf/{source['file_hash']}.pdf" if portable else base_url + source["pdf_url"]
        quote = citation["text"].replace("\n", "\n    ")
        result += f"[^{number}]: {source['title']}，PDF 第 {citation['page']} 页。\n"
        result += f"    [查看原文]({target}#page={citation['page']})\n\n    {quote}\n\n"
    for number, key in enumerate(personal_used, 1):
        source = personal[key]
        text = source["text"].replace("\n", "\n    ")
        result += f"[^personal-{number}]: 个人材料：{source['title']}，v{source['version']}（生成时快照）。\n\n    {text}\n\n"
    return result


@router.get("/api/notes/{task_id}/export")
def export(task_id: str, request: Request, format: str = Query(default="markdown", pattern="^(markdown|zip)$"),
           version: int | None = Query(default=None, ge=1)):
    note = detail(task_id)
    selected = next((v for v in note["versions"] if version is None or v["version"] == version), None)
    if not selected:
        raise HTTPException(404, "笔记版本不存在。")
    text = markdown_export(note, selected, portable=format == "zip", base_url=str(request.base_url).rstrip("/"))
    filename = f"note-{task_id}-v{selected['version']}"
    if format == "markdown":
        return Response(text, media_type="text/markdown",
                        headers={"Content-Disposition": f'attachment; filename="{filename}.md"'})
    stream = io.BytesIO()
    missing, written = [], set()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("note.md", text)
        for source in note["sources"]:
            digest = source["file_hash"]
            path = settings.DATA / "pdf" / f"{digest}.pdf"
            if not path.is_file():
                missing.append(source["title"])
            elif digest not in written:
                archive.write(path, f"pdf/{digest}.pdf")
                written.add(digest)
        archive.writestr("sources.json", json.dumps({
            "title": note["title"], "version": selected["version"], "sources": note["sources"],
            "citations": note["citations"], "missing_pdfs": missing, "template": note["template"],
        }, ensure_ascii=False, indent=2))
        if missing:
            archive.writestr("MISSING-PDFS.txt", "\n".join(missing))
    return Response(stream.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{filename}.zip"'})
