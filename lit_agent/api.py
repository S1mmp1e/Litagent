import asyncio
import json
import re
import time
import uuid

import httpx
from fastapi import APIRouter, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from pypdf.errors import PdfReadError

from . import documents, model_config, note_templates, notebook, personal, retrieval, skills, store
from .chat_history import create_session
from .runtime import Guard, check_citations
from .settings import DATA, MAX_PDF_BYTES, MODEL_NAME, library_identity

router = APIRouter()
router.include_router(model_config.router)
router.include_router(note_templates.router)
router.include_router(notebook.router)
router.include_router(personal.router)


class ProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    background: str = Field(default="", max_length=1000)
    purpose: str = Field(default="理解论文方法与实验", max_length=1000)
    focus: str = Field(default="", max_length=1000)
    depth: str = Field(default="standard", pattern="^(intro|standard|advanced)$")
    language: str = Field(default="中文", max_length=30)
    research_domain: str = Field(default="", max_length=500)
    compute_budget: str = Field(default="", max_length=500)
    framework: str = Field(default="", max_length=500)
    known_topics: str = Field(default="", max_length=1000)


class ReadingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=6000)
    session_id: int | None = Field(default=None, ge=1)
    paper_ids: list[int] = Field(default_factory=list, max_length=2)
    skill_id: str = "overview"
    action: str = Field(default="summary", pattern="^(summary|question|chat)$")
    verify_evidence: bool = True
    use_cache: bool = True
    use_personal: bool = False
    template_id: str | None = Field(default=None, max_length=80)
    template_version: int | None = Field(default=None, ge=1)


class ProcessRequest(BaseModel):
    force: bool = False


class NoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(min_length=1, max_length=50000)
    feedback: str = Field(default="", max_length=2000)
    approved: bool = False
    expected_version: int | None = Field(default=None, ge=1)


def required_task(task_id):
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在。")
    return task


def submit(req):
    if not req.message.strip():
        raise HTTPException(422, "消息不能为空。")
    paper_ids = list(dict.fromkeys(req.paper_ids))
    if req.action != "chat":
        spec = skills.REGISTRY.get(req.skill_id)
        if not spec:
            raise HTTPException(422, "未注册的 Skill。")
        if not spec["min_papers"] <= len(paper_ids) <= spec["max_papers"]:
            raise HTTPException(422, f"{spec['name']}需要 {spec['min_papers']} 篇论文。")
        for paper_id in paper_ids:
            if not store.one("SELECT 1 FROM documents WHERE paper_id=? AND status='ready'", (paper_id,)):
                raise HTTPException(409, f"论文 {paper_id} 尚未完成全文索引。")
    elif paper_ids:
        raise HTTPException(422, "文献管理对话不接受论文选择，请切换为阅读模式。")
    if req.session_id and not store.one("SELECT 1 FROM chat_sessions WHERE id=?", (req.session_id,)):
        raise HTTPException(404, "会话不存在。")
    payload = req.model_dump()
    payload["paper_ids"] = paper_ids
    payload["profile"] = skills.profile()
    payload["skill_version"] = skills.REGISTRY.get(req.skill_id, {}).get("version")
    if req.action != "chat":
        payload["skill_snapshot"] = skills.snapshot(req.skill_id)
        payload["skill_version"] = payload["skill_snapshot"]["version"]
        payload["personal_scope"] = personal.scope_signature(paper_ids) if req.use_personal else None
        template = note_templates.snapshot(req.template_id)
        if template and req.template_version is not None and template["version"] != req.template_version:
            raise HTTPException(409, "笔记模板已更新，请刷新模板列表后重新提交。")
        payload["template_snapshot"] = template
        if template:
            payload["template_values"] = note_templates.field_values(
                note_templates.paper_records(paper_ids), payload["profile"],
                skill_name=payload["skill_snapshot"]["name"])
    elif req.template_id:
        raise HTTPException(422, "笔记模板仅用于论文阅读。")
    session_id = req.session_id or create_session()
    try:
        return store.enqueue("chat" if req.action == "chat" else "reading", payload, session_id)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@router.get("/api/health")
def health():
    config = model_config.public_config()
    return {"ok": bool(store.one("SELECT 1 AS ok")), "application": "litagent", "version": "4.0.0",
            "library_id": library_identity(),
            "model": config["model"],
            "api_configured": config["api_configured"],
            "embedding_model": MODEL_NAME, "single_user": True}


@router.get("/api/profile")
def get_profile():
    return skills.profile()


@router.put("/api/profile")
def update_profile(req: ProfileRequest):
    return skills.save_profile(req.model_dump())


@router.post("/api/profile/reset")
def reset_profile():
    return skills.save_profile(ProfileRequest().model_dump())


@router.get("/api/skills")
def get_skills():
    return [{key: value for key, value in skills.snapshot(skill_id).items()
             if key not in ("instruction", "base", "map_focus", "gap_queries")}
            for skill_id in skills.REGISTRY]


@router.get("/api/profile/presets")
def profile_presets():
    return [{"id": key, **value} for key, value in skills.PRESETS.items()]


@router.post("/api/papers/upload")
async def upload(file: UploadFile = File(...)):
    content = await file.read(MAX_PDF_BYTES + 1)
    await file.close()
    if len(content) > MAX_PDF_BYTES or not content.startswith(b"%PDF-"):
        raise HTTPException(422, "仅支持不超过 25 MB 的 PDF。")
    try:
        title = (file.filename or "Untitled").replace("\\", "/").rsplit("/", 1)[-1]
        paper_id, digest = documents.upload(content, title.removesuffix(".pdf"))
        task = store.enqueue("ingest", {"paper_id": paper_id, "file_hash": digest})
        return {"paper_id": paper_id, "task": task}
    except (ValueError, PdfReadError) as error:
        raise HTTPException(422, str(error)) from error


@router.post("/api/papers/{paper_id}/process")
def process(paper_id: int, req: ProcessRequest):
    if not store.one("SELECT 1 FROM papers WHERE id=?", (paper_id,)):
        raise HTTPException(404, "论文不存在。")
    doc = store.one("SELECT * FROM documents WHERE paper_id=? ORDER BY id DESC LIMIT 1", (paper_id,))
    payload = {"paper_id": paper_id, "force": req.force}
    if doc:
        payload["file_hash"] = doc["file_hash"]
    return store.enqueue("ingest", payload)


@router.get("/api/papers/{paper_id}/documents")
def document_status(paper_id: int):
    return store.rows("SELECT * FROM documents WHERE paper_id=? ORDER BY id DESC", (paper_id,))


@router.get("/api/papers/{paper_id}/preview")
def paper_preview(paper_id: int):
    try:
        return documents.preview_info(paper_id)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error


@router.post("/api/papers/{paper_id}/preview")
def prepare_preview(paper_id: int):
    guard = Guard()
    guard.deadline = time.monotonic() + 45
    try:
        return documents.cache_preview(paper_id, guard)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except (TimeoutError, httpx.TimeoutException) as error:
        raise HTTPException(504, "PDF 下载超时，请重试或打开原文链接。") from error
    except httpx.HTTPError as error:
        raise HTTPException(502, "无法从 arXiv 下载 PDF，请重试或打开原文链接。") from error
    except (ValueError, PdfReadError) as error:
        raise HTTPException(422, "PDF 无法预览：文件缺失、无效、加密或超出大小/页数限制。") from error


@router.get("/api/documents/{document_id}/pdf")
def document_pdf(document_id: int):
    doc = store.one("SELECT * FROM documents WHERE id=?", (document_id,))
    if not doc:
        raise HTTPException(404, "文档不存在。")
    path = DATA / "pdf" / f"{doc['file_hash']}.pdf"
    if not path.exists():
        raise HTTPException(404, "PDF 文件不存在。")
    return FileResponse(path, media_type="application/pdf", filename=f"paper-{doc['paper_id']}.pdf",
                        content_disposition_type="inline")


@router.get("/api/chunks/{chunk_id}")
def chunk_detail(chunk_id: uuid.UUID):
    chunk = store.one("SELECT * FROM chunks WHERE id=?", (str(chunk_id),))
    if not chunk:
        saved = store.one("SELECT * FROM note_evidence WHERE chunk_id=? LIMIT 1", (str(chunk_id),))
        if saved:
            return {**saved, "id": saved["chunk_id"],
                    "pdf_url": f"/api/notes/{saved['task_id']}/sources/{saved['document_id']}/pdf#page={saved['page']}"}
        raise HTTPException(404, "原文片段已不存在。")
    return {**chunk, "pdf_url": f"/api/documents/{chunk['document_id']}/pdf#page={chunk['page']}"}


@router.get("/api/retrieval")
def retrieve(query: str = Query(min_length=1, max_length=2000),
             paper_id: list[int] = Query(default=[]), mode: str = "hybrid"):
    if mode not in ("keyword", "dense", "hybrid"):
        raise HTTPException(422, "未知检索模式。")
    return retrieval.search(query, paper_id, mode=mode)


@router.post("/api/chat/runs", status_code=202)
def create_run(req: ReadingRequest):
    return submit(req)


@router.get("/api/tasks")
def list_tasks(session_id: int | None = None):
    where, args = ("WHERE session_id=?", (session_id,)) if session_id else ("", ())
    return store.rows(f"""SELECT id,kind,status,stage,error,session_id,created_at,updated_at
                          FROM tasks {where} ORDER BY created_at DESC LIMIT 50""", args)


@router.get("/api/tasks/{task_id}")
def task_detail(task_id: str):
    return required_task(task_id)


@router.post("/api/tasks/{task_id}/cancel")
def cancel(task_id: str):
    required_task(task_id)
    with store.connect() as db:
        db.execute("""UPDATE tasks SET cancel_requested=1,
                      status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END
                      WHERE id=? AND status IN ('queued','running')""", (task_id,))
    return required_task(task_id)


@router.post("/api/tasks/{task_id}/retry", status_code=202)
def retry(task_id: str):
    task = required_task(task_id)
    if task["status"] not in ("failed", "cancelled") or task["kind"] == "chat":
        raise HTTPException(409, "只能重试失败或取消的读取/处理任务，文献管理写操作请核对后重新发起。")
    try:
        new = store.enqueue(task["kind"], task["payload"], task["session_id"], task["checkpoint"])
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return new


@router.get("/api/tasks/{task_id}/events")
async def events(task_id: str, after: int = Query(default=0, ge=0),
                 last_event_id: int | None = Header(default=None, ge=0)):
    required_task(task_id)

    async def stream():
        cursor = max(after, last_event_id or 0)
        while True:
            batch = store.rows("SELECT * FROM task_events WHERE task_id=? AND id>? ORDER BY id LIMIT 100",
                               (task_id, cursor))
            for event in batch:
                cursor = event["id"]
                yield f"id: {cursor}\nevent: {event['event']}\ndata: {event['data']}\n\n"
            task = store.get_task(task_id)
            if not task:
                return
            if task["status"] in ("succeeded", "failed", "cancelled") and not batch:
                yield f"event: terminal\ndata: {store.dumps({'status': task['status']})}\n\n"
                return
            if not batch:
                yield ": heartbeat\n\n"
            await asyncio.sleep(0.4)
    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/api/tasks/{task_id}/notes")
def notes(task_id: str):
    required_task(task_id)
    return store.rows("SELECT * FROM notes WHERE task_id=? ORDER BY version DESC", (task_id,))


@router.put("/api/tasks/{task_id}/notes")
def save_note(task_id: str, req: NoteRequest):
    task = required_task(task_id)
    if task["kind"] != "reading" or task["status"] != "succeeded":
        raise HTTPException(409, "只能修改已完成的论文阅读笔记。")
    if not req.body.strip():
        raise HTTPException(422, "笔记正文不能为空。")
    entry = store.one("SELECT * FROM notebooks WHERE task_id=?", (task_id,))
    if entry and entry["trashed_at"] is not None:
        raise HTTPException(409, "请先从回收站恢复笔记。")
    allowed = [c["id"] for c in task["result"].get("citations", [])]
    chunks = [store.one("SELECT * FROM chunks WHERE id=?", (key,)) for key in allowed]
    chunks = [c for c in chunks if c]
    document_ids = task["result"].get("document_ids", [])
    active_documents = set()
    if document_ids:
        active_documents = {d["id"] for d in store.rows(
            f"SELECT id FROM documents WHERE status='ready' AND id IN "
            f"({','.join('?' for _ in document_ids)})", document_ids)}
    chunks = [c for c in chunks if c["document_id"] in active_documents]
    # A saved note continues to refer to its original evidence after reindex/deletion.
    snapshots = notebook.evidence(task_id)
    chunks = list({c["id"]: c for c in [*chunks, *snapshots]}.values())
    try:
        cites = check_citations(req.body, chunks)
        personal.check_citations(req.body, task["result"].get("personal_sources", []))
        if req.approved and (not cites or not req.feedback.strip()):
            raise ValueError("审核通过前请保留引用并填写审核说明。")
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        version = db.execute("SELECT coalesce(max(version),0)+1 FROM notes WHERE task_id=?", (task_id,)).fetchone()[0]
        if req.expected_version is not None and req.expected_version != version - 1:
            raise HTTPException(409, "笔记已被更新，请重新加载后再保存。")
        db.execute("INSERT INTO notes(task_id,version,body,feedback,approved,created_at) VALUES(?,?,?,?,?,?)",
                   (task_id, version, req.body, req.feedback, int(req.approved), time.time()))
        notebook.register(db, task, task["result"])
        db.execute("UPDATE notebooks SET updated_at=? WHERE task_id=?", (time.time(), task_id))
    return notes(task_id)


@router.get("/api/tasks/{task_id}/notes/download")
def download_note(task_id: str):
    values = notes(task_id)
    if not values:
        raise HTTPException(404, "没有笔记。")
    return Response(values[0]["body"], media_type="text/markdown",
                    headers={"Content-Disposition": f'attachment; filename="note-{task_id}.md"'})


@router.get("/api/tasks/{task_id}/review-input")
def review_input(task_id: str):
    """Return reviewed prompt material with private API credentials excluded."""
    task = required_task(task_id)
    if task["kind"] != "reading" or task["status"] != "succeeded":
        raise HTTPException(409, "仅已完成的阅读任务可审核。")
    messages = (task["checkpoint"] or {}).get("generation_messages")
    if not messages:
        raise HTTPException(404, "任务没有可审核的模型输入。")
    return {"messages": messages, "skill_id": task["payload"]["skill_id"],
            "skill_version": task["payload"]["skill_version"], "profile": task["payload"]["profile"],
            "paper_ids": task["payload"]["paper_ids"]}


@router.get("/api/exports/sft")
def export_sft():
    output = []
    for note in store.rows("""SELECT n.* FROM notes n WHERE approved=1 AND version=(
                              SELECT max(version) FROM notes WHERE task_id=n.task_id)
                              AND NOT EXISTS(SELECT 1 FROM notebooks b WHERE b.task_id=n.task_id AND b.trashed_at IS NOT NULL)"""):
        task = required_task(note["task_id"])
        messages = task["checkpoint"].get("generation_messages")
        if not messages:
            continue
        documents_ = notebook.sources(note["task_id"]) or [
            store.one("SELECT * FROM documents WHERE id=?", (i,))
            for i in task["result"].get("document_ids", [])]
        if not documents_ or any(d is None for d in documents_):
            continue
        output.append(store.dumps({
            "sample_id": f"{note['task_id']}-v{note['version']}",
            "paper_hashes": sorted(d["file_hash"] for d in documents_),
            "model": task["result"].get("model"),
            "created_at": note["created_at"],
            "skill_id": task["payload"]["skill_id"], "skill_version": task["payload"]["skill_version"],
            "profile": task["payload"]["profile"], "review": note["feedback"],
            "messages": task["checkpoint"]["generation_messages"] + [{"role": "assistant", "content": note["body"]}],
            "synthetic": False, "reviewed": True,
        }))
    return Response("\n".join(output) + ("\n" if output else ""), media_type="application/x-ndjson",
                    headers={"Content-Disposition": 'attachment; filename="reviewed-paper-sft.jsonl"'})


@router.get("/api/eval/manifest")
def evaluation_manifest():
    return {"benchmark": "synthetic-software-smoke-v1", "synthetic": True,
            "real_paper_claims": False,
            "real_evaluation": {"script": "scripts.evaluate_reading", "requires_human_labels": True,
                                "quality_improvement_measured": False},
            "metrics": [
                "keyword_recall_at_5", "dense_recall_at_5", "hybrid_recall_at_5",
                "citation_page_accuracy", "empty_scope_returns_no_evidence",
                "resume_deduplication", "latency_seconds", "model_token_usage",
            ]}
