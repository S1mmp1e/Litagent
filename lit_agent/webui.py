"""
LitAgent Web UI — FastAPI + vanilla HTML/JS frontend.
Replaces the Gradio version for better performance and responsiveness.
Run: python -m lit_agent.webui
"""

import json
import mimetypes
from pathlib import Path
from fastapi.staticfiles import StaticFiles

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import HTMLResponse
    from pydantic import BaseModel
except ModuleNotFoundError as exc:
    raise SystemExit(
        "Missing web UI dependencies. Run `python -m pip install -r requirements.txt` "
        "and then start the app with `python -m lit_agent.webui`."
    ) from exc

from .api import ReadingRequest, router, submit
from . import store
from .runtime import safe_error
from .category_manager import (
    create_category,
    delete_category,
    get_papers_in_category,
    list_categories,
    manually_categorize,
    remove_from_category,
    rename_category,
)
from .chat_history import (
    create_session,
    delete_session,
    get_messages,
    list_sessions,
    save_messages,
)
from .database import get_session, init_db
from .search import search_papers as arxiv_search
from .tools import add_tag, add_to_library, export_bibtex, get_paper_detail

# ============================================================================
# App setup
# ============================================================================

import re

_TEMPLATE = (Path(__file__).parent / "templates" / "index.html").read_text(encoding="utf-8")

# Windows registry associations can otherwise serve ES modules as text/plain.
mimetypes.add_type("application/javascript", ".mjs")
mimetypes.add_type("application/wasm", ".wasm")

app = FastAPI(title="LitAgent", version="3.0.0")
app.include_router(router)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


@app.middleware("http")
async def local_origin_guard(request, call_next):
    from urllib.parse import urlsplit
    from fastapi.responses import JSONResponse
    origin = request.headers.get("origin")
    if origin and request.method not in ("GET", "HEAD", "OPTIONS"):
        if urlsplit(origin).netloc != request.headers.get("host"):
            return JSONResponse(status_code=403, content={"detail": "拒绝跨站写入请求。"})
    return await call_next(request)


@app.exception_handler(Exception)
async def unexpected_error(request, error):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=500, content={"detail": safe_error(error)})


def _md_to_html(text: str) -> str:
    """Convert simple markdown (bold, code, headers, tables, line breaks) to HTML."""
    if not text:
        return ""
    # Escape HTML entities first
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # Inline code (before other formatting)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    # Bold
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    # Italic (but not inside <strong>)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)
    # Markdown tables — detect consecutive pipe-delimited rows
    lines = text.split("\n")
    result_lines = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if "|" in line and i + 2 < len(lines):
            next_line = lines[i + 1].strip()
            if re.match(r"^[\|\s\-:]+$", next_line) and "---" in next_line:
                table_rows = []
                table_rows.append(line)
                table_rows.append(next_line)
                i += 2
                while i < len(lines) and "|" in lines[i]:
                    table_rows.append(lines[i].strip())
                    i += 1
                html_rows = ["<table>"]
                for ri, row in enumerate(table_rows):
                    if re.match(r"^[\|\s\-:]+$", row):
                        continue
                    cells = [c.strip() for c in row.strip("|").split("|")]
                    tag = "th" if ri == 0 else "td"
                    html_rows.append("<tr>")
                    for cell in cells:
                        html_rows.append(f"<{tag}>{cell}</{tag}>")
                    html_rows.append("</tr>")
                html_rows.append("</table>")
                result_lines.append("".join(html_rows))
                continue
        result_lines.append(line)
        i += 1
    text = "\n".join(result_lines)
    # Headers
    text = re.sub(r"^### (.+)$", r"<h3>\1</h3>", text, flags=re.MULTILINE)
    text = re.sub(r"^## (.+)$", r"<h2>\1</h2>", text, flags=re.MULTILINE)
    # Convert color dot placeholders: !!DOT:RRGGBB!! → colored span
    text = re.sub(
        r"!!DOT:([a-fA-F0-9]{6})!!",
        r'<span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:#\1;margin-right:4px;"></span>',
        text,
    )
    # Protect HTML blocks from <br> injection
    html_blocks = []
    for tag in ("table", "pre", "code", "tr", "td", "th", "thead", "tbody"):
        for m in re.finditer(rf"<{tag}>[\s\S]*?</{tag}>", text):
            html_blocks.append(m.group())
    for idx, block in enumerate(html_blocks):
        text = text.replace(block, f"\x00BLOCK{idx}\x00", 1)
    # Line breaks
    text = text.replace("\n", "<br>")
    # Restore HTML blocks
    for idx, block in enumerate(html_blocks):
        text = text.replace(f"\x00BLOCK{idx}\x00", block)
    return text


def _author_short(authors, max_n: int = 3) -> str:
    if isinstance(authors, str):
        authors = json.loads(authors)
    s = ", ".join(authors[:max_n])
    if len(authors) > max_n:
        s += " 等"
    return s


# ============================================================================
# Data helpers (DB queries, same logic as before)
# ============================================================================


def _get_library_rows() -> list:
    from sqlalchemy.orm import selectinload

    from .database import Paper, PaperCategory, Tag

    session = get_session()
    try:
        papers = (
            session.query(Paper)
            .options(
                selectinload(Paper.tags),
                selectinload(Paper.paper_categories).selectinload(PaperCategory.category),
            )
            .order_by(Paper.created_at.desc())
            .all()
        )
        rows = []
        for p in papers:
            authors = json.loads(p.authors)
            tags_str = ", ".join(t.name for t in p.tags)
            cat_names = [pc.category.name for pc in p.paper_categories if pc.status == "approved"]
            cats_str = ", ".join(cat_names)
            rows.append([
                p.id, p.title[:100], str(p.year),
                _author_short(authors, 2), tags_str, cats_str, p.arxiv_id,
            ])
        return rows
    finally:
        session.close()


def _get_stats_md() -> str:
    from .database import Paper, Tag

    session = get_session()
    try:
        total = session.query(Paper).count()
        tag_objs = session.query(Tag).all()
        tag_list = "、".join(f"`{t.name}`" for t in tag_objs) if tag_objs else "无"
        return f"**论文：** {total}  \n**标签：** {tag_list}"
    finally:
        session.close()


def _get_cat_md() -> str:
    cats = list_categories()
    if not cats:
        return "*暂无分类夹*"
    lines = ["| ID | 分类夹 | 论文数 |", "|---|---:|---:|"]
    for c in cats:
        color = c.get("color", "#6b7280").replace("#", "")
        lines.append(f"| {c['id']} | !!DOT:{color}!!{c['name']} | {c['approved_count']} |")
    lines.append(f"\n*共 {len(cats)} 个分类夹*")
    return "\n".join(lines)


# ============================================================================
# Page route
# ============================================================================


@app.get("/", response_class=HTMLResponse)
def index():
    html = _TEMPLATE.replace("{{ stats_md | safe }}", _md_to_html(_get_stats_md()))
    html = html.replace("{{ cat_md | safe }}", _md_to_html(_get_cat_md()))
    return html


# ============================================================================
# Pydantic models
# ============================================================================


class AddRequest(BaseModel):
    arxiv_id: str


class TagRequest(BaseModel):
    paper_id: int
    tag_name: str


class ExportRequest(BaseModel):
    paper_ids: str


class CategoryCreateRequest(BaseModel):
    name: str
    description: str = ""
    color: str = "#6b7280"


class CategoryRenameRequest(BaseModel):
    name: str


class CategoryAssignRequest(BaseModel):
    paper_id: int
    category_id: int


# ============================================================================
# Library API routes
# ============================================================================


@app.get("/api/library/rows")
def api_library_rows():
    rows = _get_library_rows()
    return {
        "rows": rows,
        "stats_md": _get_stats_md(),
        "cat_md": _get_cat_md(),
    }


@app.get("/api/library/detail/{paper_id}")
def api_library_detail(paper_id: int):
    try:
        result = get_paper_detail(paper_id)
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))
    if not result.startswith("{"):
        return {"md": result, "arxiv_id": "", "pdf_url": "", "bibtex": ""}
    data = json.loads(result)
    authors_list = data.get("authors", [])
    authors = "\n".join(f"- {a}" for a in authors_list)
    md = (
        f"## {data['title']}\n\n"
        f"| 年份 | arXiv | DOI | 期刊/会议 |\n"
        f"|------|-------|-----|----------|\n"
        f"| {data['year']} | `{data['arxiv_id']}` | {data.get('doi') or '—'} | {data.get('venue') or '—'} |\n\n"
        f"### 作者\n{authors}\n\n"
        f"### 摘要\n{data['abstract']}"
    )
    return {
        "md": md,
        "arxiv_id": data.get("arxiv_id", ""),
        "pdf_url": data.get("pdf_url", ""),
        "bibtex": export_bibtex(str(paper_id)),
    }


@app.post("/api/library/add")
def api_library_add(req: AddRequest):
    arxiv_id = req.arxiv_id.strip()
    if not arxiv_id:
        return {"ok": False, "msg": "请输入 arXiv ID。"}
    msg = add_to_library(arxiv_id)
    ok = "错误" not in msg and "失败" not in msg
    return {
        "ok": ok,
        "msg": msg,
        "rows": _get_library_rows(),
        "stats_md": _get_stats_md(),
        "cat_md": _get_cat_md(),
    }


@app.post("/api/library/tag")
def api_library_tag(req: TagRequest):
    if not req.tag_name.strip():
        return {"ok": False, "msg": "请输入标签名称。"}
    msg = add_tag(req.paper_id, req.tag_name.strip())
    ok = "错误" not in msg and "失败" not in msg
    return {
        "ok": ok,
        "msg": msg,
        "rows": _get_library_rows(),
        "stats_md": _get_stats_md(),
    }


@app.delete("/api/library/{paper_id}")
def api_library_delete(paper_id: int):
    from .database import Paper

    session = get_session()
    try:
        p = session.query(Paper).get(paper_id)
        if not p:
            return {"ok": False, "msg": f"未找到编号为 {paper_id} 的论文。"}
        title = p.title
    finally:
        session.close()
    from .documents import delete_paper
    delete_paper(paper_id)
    return {
        "ok": True,
        "msg": f"已删除：{title}",
        "rows": _get_library_rows(),
        "stats_md": _get_stats_md(),
        "cat_md": _get_cat_md(),
    }


@app.post("/api/library/export")
def api_library_export(req: ExportRequest):
    if not req.paper_ids.strip():
        return {"bibtex": "请输入论文编号。"}
    bibtex = export_bibtex(req.paper_ids.strip())
    return {"bibtex": bibtex}


# ============================================================================
# Category API routes
# ============================================================================


@app.get("/api/categories")
def api_categories():
    return list_categories()


@app.post("/api/categories")
def api_categories_create(req: CategoryCreateRequest):
    if not req.name.strip():
        return {"ok": False, "msg": "请输入分类夹名称。"}
    msg = create_category(req.name.strip(), req.description.strip(), req.color)
    ok = "错误" not in msg and "失败" not in msg
    return {"ok": ok, "msg": msg, "cat_md": _get_cat_md()}


@app.put("/api/categories/{cat_id}")
def api_categories_rename(cat_id: int, req: CategoryRenameRequest):
    if not req.name.strip():
        return {"ok": False, "msg": "请输入新名称。"}
    msg = rename_category(cat_id, req.name.strip())
    ok = "错误" not in msg and "失败" not in msg
    return {"ok": ok, "msg": msg, "cat_md": _get_cat_md()}


@app.delete("/api/categories/{cat_id}")
def api_categories_delete(cat_id: int):
    msg = delete_category(cat_id)
    ok = "错误" not in msg and "失败" not in msg
    return {"ok": ok, "msg": msg, "cat_md": _get_cat_md()}


@app.get("/api/categories/{cat_id}/papers")
def api_categories_papers(cat_id: int):
    papers = get_papers_in_category(cat_id, status="approved")
    rows = []
    for p in papers:
        rows.append([
            p["id"], p["title"][:100], str(p["year"]),
            ", ".join(p["authors"][:2]), p["tags"], p["arxiv_id"],
        ])
    cat_name = ""
    for c in list_categories():
        if c["id"] == cat_id:
            cat_name = c["name"]
            break
    return {"rows": rows, "info": f"### {cat_name}（{len(rows)} 篇）"}


@app.post("/api/categories/assign")
def api_categories_assign(req: CategoryAssignRequest):
    if not req.paper_id or not req.category_id:
        return {"ok": False, "msg": "请提供论文编号和目标分类夹。"}
    msg = manually_categorize(req.paper_id, req.category_id)
    ok = "错误" not in msg and "失败" not in msg
    return {"ok": ok, "msg": msg, "cat_md": _get_cat_md()}


@app.post("/api/categories/remove")
def api_categories_remove(req: CategoryAssignRequest):
    if not req.paper_id or not req.category_id:
        return {"ok": False, "msg": "请提供论文编号和目标分类夹。"}
    msg = remove_from_category(req.paper_id, req.category_id)
    ok = "错误" not in msg and "失败" not in msg
    return {"ok": ok, "msg": msg, "cat_md": _get_cat_md()}


# ============================================================================
# Search API route
# ============================================================================


@app.get("/api/search")
def api_search(query: str, max_results: int = 10):
    if not query.strip():
        return {"ok": False, "status": "请输入搜索关键词。", "rows": []}
    papers = arxiv_search(query, max_results)
    if not papers:
        return {"ok": True, "status": "未找到相关论文。", "rows": []}
    rows = []
    for p in papers:
        rows.append([
            p["arxiv_id"], p["title"][:120], str(p["year"]),
            _author_short(p["authors"]),
        ])
    return {"ok": True, "status": f"找到 {len(papers)} 条结果：**{query}**", "rows": rows}


# ============================================================================
# Chat API route
# ============================================================================


@app.post("/api/chat", status_code=202, deprecated=True)
def api_chat(req: ReadingRequest):
    return submit(req.model_copy(update={"action": "chat", "paper_ids": []}))


# ============================================================================
# Session API routes
# ============================================================================


@app.get("/api/sessions")
def api_sessions():
    return list_sessions()


@app.get("/api/sessions/{session_id}")
def api_session_detail(session_id: int):
    value = store.one("SELECT * FROM chat_sessions WHERE id=?", (session_id,))
    if not value:
        raise HTTPException(404, "会话不存在。")
    return {"messages": json.loads(value["messages"] or "[]"),
            "legacy_context": not bool(json.loads(value["agent_messages"] or "[]")),
            "tasks": store.rows("SELECT id,status,stage FROM tasks WHERE session_id=? ORDER BY created_at DESC",
                                (session_id,))}


@app.delete("/api/sessions/{session_id}")
def api_session_delete(session_id: int):
    with store.connect() as db:
        db.execute("UPDATE tasks SET cancel_requested=1 WHERE session_id=?", (session_id,))
    delete_session(session_id)
    return {"ok": True}


# ============================================================================
# Startup
# ============================================================================


from contextlib import asynccontextmanager
from collections.abc import AsyncIterator


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    init_db()
    from .jobs import worker
    from .retrieval import close_vectors
    worker.start()
    try:
        yield
    finally:
        worker.stop()
        close_vectors()


app.router.lifespan_context = lifespan


def main():
    try:
        import uvicorn
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "Missing `uvicorn`. Run `python -m pip install -r requirements.txt` "
            "and then start the app with `python -m lit_agent.webui`."
        ) from exc

    import os
    uvicorn.run(app, host=os.getenv("LIT_HOST", "127.0.0.1"),
                port=int(os.getenv("LIT_PORT", "7860")), log_level="warning")


if __name__ == "__main__":
    main()
