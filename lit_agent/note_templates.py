"""Versioned, user-owned output templates; skills retain the evidence rules."""

import json
import re
import time
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import skills, store

router = APIRouter()

PAPER_FIELDS = {
    "paper_title": ("论文标题", "title"),
    "paper_authors": ("作者", "authors"),
    "paper_year": ("发表年份", "year"),
    "paper_venue": ("期刊 / 会议", "venue"),
    "paper_doi": ("DOI", "doi"),
    "paper_arxiv_id": ("arXiv 编号", "arxiv_id"),
    "paper_pdf_url": ("PDF 来源链接", "pdf_url"),
    "paper_abstract": ("论文摘要", "abstract"),
}
PROFILE_FIELDS = {
    "reading_purpose": ("阅读目的", "purpose"),
    "reading_focus": ("关注内容", "focus"),
    "knowledge_background": ("知识背景", "background"),
    "research_domain": ("研究方向", "research_domain"),
    "known_topics": ("已掌握的知识", "known_topics"),
    "reading_depth": ("讲解深度", "depth"),
    "output_language": ("输出语言", "language"),
    "preferred_framework": ("计划采用的框架", "framework"),
    "compute_budget": ("可用算力", "compute_budget"),
}
FIELD_CATALOG = [
    *({"id": key, "label": label, "group": "论文信息"} for key, (label, _) in PAPER_FIELDS.items()),
    *({"id": key, "label": label, "group": "阅读档案"} for key, (label, _) in PROFILE_FIELDS.items()),
    {"id": "note_date", "label": "笔记日期", "group": "本次阅读"},
    {"id": "reading_mode", "label": "阅读模式", "group": "本次阅读"},
    {"id": "paper_count", "label": "论文数量", "group": "本次阅读"},
]

PRESETS = {
    "paper-reading": {
        "name": "论文精读",
        "description": "问题、方法、实验与局限的完整阅读记录",
        "body": """# {{paper_title}}

## 一句话结论
概括论文解决的问题、主要方法和适用边界。

## 研究问题与动机
说明已有方法的不足、研究假设，以及这项工作为什么值得研究。

## 核心方法
先解释直觉，再拆解输入输出、关键模块、算法步骤与必要的数学机制。
区分原文定义和帮助理解的解释。

## 实验与证据
整理数据集、对照方法、指标、主要结果和消融实验。
用表格呈现原文明确报告的数字，重要结论附原文引用。

## 局限与待核实问题
分别记录作者承认的局限和读者推断；证据不充分的内容明确标注。

## 我的研究启发
结合阅读目的 {{reading_purpose}}，给出可借鉴的思路与下一步验证问题。
建议必须与论文已有结果区分。
""",
    },
    "paper-reproduction": {
        "name": "复现分析",
        "description": "面向实现、实验配置与验证路径的研究笔记",
        "body": """# {{paper_title}}

## 目标与适用条件
说明任务、输入输出、问题假设，以及复现希望验证的结论。

## 方法与实现流程
按数据流解释模块、训练目标和推理步骤；必要时提供伪代码。
伪代码属于辅助解释，不冒充作者公开代码。

## 原文实验配置
以表格整理数据划分、预处理、模型规模、优化器、学习率、
批大小、训练步数、种子与硬件。缺失字段填写“原文未报告”。

## 结果与对照
列出关键指标、基线、消融和对应证据，检查比较条件是否一致。

## 复现清单
列出数据准备、环境依赖、最小实现、训练和评估步骤。
将论文配置、读者建议和待确认事项分开。

## 风险与验证计划
结合阅读目的 {{reading_purpose}}，说明实现难点、可能的失败原因、
需要补查的材料以及可以执行的验证实验。
""",
    },
}


class TemplateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=300)
    body: str = Field(min_length=1, max_length=12000)
    expected_version: int | None = Field(default=None, ge=1)

    @field_validator("name", "body")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("名称和模板内容不能为空。")
        return value.strip()


def initialize(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS note_templates(
          id TEXT PRIMARY KEY, version INTEGER NOT NULL, builtin INTEGER NOT NULL DEFAULT 0,
          archived INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS note_template_versions(
          template_id TEXT NOT NULL REFERENCES note_templates(id) ON DELETE CASCADE,
          version INTEGER NOT NULL, name TEXT NOT NULL, description TEXT NOT NULL,
          body TEXT NOT NULL, created_at REAL NOT NULL, PRIMARY KEY(template_id,version));
    """)
    now = time.time()
    for key, value in PRESETS.items():
        db.execute("INSERT OR IGNORE INTO note_templates VALUES(?,1,1,0,?,?)", (key, now, now))
        db.execute("INSERT OR IGNORE INTO note_template_versions VALUES(?,1,?,?,?,?)",
                   (key, value["name"], value["description"], value["body"], now))


def snapshot(template_id, version=None):
    if not template_id:
        return None
    value = store.one("""SELECT t.id,t.builtin,t.archived,v.version,v.name,v.description,v.body
        FROM note_templates t JOIN note_template_versions v ON v.template_id=t.id
        AND v.version=coalesce(?,t.version) WHERE t.id=?""", (version, template_id))
    if not value or value["archived"]:
        raise HTTPException(404, "笔记模板已删除或不存在，请重新选择。")
    return value


def paper_records(paper_ids):
    papers = []
    for paper_id in dict.fromkeys(paper_ids):
        paper = store.one("SELECT * FROM papers WHERE id=?", (paper_id,))
        if not paper:
            raise HTTPException(404, f"论文 {paper_id} 不存在。")
        papers.append(paper)
    return papers


def field_values(papers, profile, *, skill_name="", created_at=None):
    def text(value, missing):
        return str(value).strip() if value is not None and str(value).strip() else missing

    def paper_text(paper, key):
        value = paper.get(key)
        if key == "authors":
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    pass
            if isinstance(value, list):
                value = "; ".join(author.strip() for author in value if isinstance(author, str) and author.strip())
            elif not isinstance(value, str):
                value = None
        return text(value, "未提供")

    values = {}
    for field, (_, key) in PAPER_FIELDS.items():
        entries = [paper_text(paper, key) for paper in papers]
        if not entries:
            values[field] = "未选择论文"
        elif key == "title":
            values[field] = " / ".join(entries)
        elif len(entries) == 1:
            values[field] = entries[0]
        else:
            values[field] = "\n".join(f"论文 {chr(65 + i)}：{entry}" for i, entry in enumerate(entries))
    for field, (_, key) in PROFILE_FIELDS.items():
        value = profile.get(key)
        if key == "depth":
            value = {"intro": "入门", "standard": "标准", "advanced": "深入"}.get(value, value)
        values[field] = text(value, "未设置")
    stamp = time.time() if created_at is None else created_at
    values.update(note_date=datetime.fromtimestamp(stamp).date().isoformat(),
                  reading_mode=text(skill_name, "未设置"), paper_count=str(len(papers)))
    return values


def expand(template, values):
    # Substitute once: metadata containing another placeholder is still literal data.
    return re.sub(r"\{\{([a-z][a-z0-9_]*)\}\}",
                  lambda match: values.get(match[1], match[0]), template["body"])


@router.get("/api/note-template-fields")
def template_fields(paper_ids: list[int] = Query(default=[], max_length=2), skill_id: str = "overview"):
    spec = skills.REGISTRY.get(skill_id)
    if not spec:
        raise HTTPException(422, "未注册的阅读模式。")
    return {"fields": FIELD_CATALOG, "values": field_values(
        paper_records(paper_ids), skills.profile(), skill_name=spec["name"])}


def headings_report(answer, template):
    if not template:
        return None
    expected = re.findall(r"^##\s+(.+?)\s*$", template["body"], re.MULTILINE)
    actual = set(re.findall(r"^##\s+(.+?)\s*$", answer, re.MULTILINE))
    missing = [heading for heading in expected if heading not in actual]
    return {"status": "partial" if missing else "complete", "missing_sections": missing}


@router.get("/api/note-templates")
def catalog():
    return store.rows("""SELECT t.id,t.builtin,t.version,t.updated_at,v.name,v.description,v.body
        FROM note_templates t JOIN note_template_versions v ON v.template_id=t.id AND v.version=t.version
        WHERE t.archived=0 ORDER BY t.builtin DESC,t.updated_at DESC,t.id""")


@router.get("/api/note-templates/{template_id}")
def detail(template_id: str):
    return {**snapshot(template_id), "versions": store.rows(
        "SELECT * FROM note_template_versions WHERE template_id=? ORDER BY version DESC", (template_id,))}


@router.post("/api/note-templates", status_code=201)
def create(req: TemplateRequest):
    key, now = uuid.uuid4().hex, time.time()
    with store.connect() as db:
        db.execute("INSERT INTO note_templates VALUES(?,1,0,0,?,?)", (key, now, now))
        db.execute("INSERT INTO note_template_versions VALUES(?,1,?,?,?,?)",
                   (key, req.name, req.description, req.body, now))
    return detail(key)


@router.put("/api/note-templates/{template_id}")
def update(template_id: str, req: TemplateRequest):
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT * FROM note_templates WHERE id=? AND archived=0", (template_id,)).fetchone()
        if not old:
            raise HTTPException(404, "模板不存在。")
        if old["builtin"]:
            raise HTTPException(409, "内置模板请另存为个人模板。")
        if req.expected_version != old["version"]:
            raise HTTPException(409, "模板已更新，请重新加载后再保存。")
        version, now = old["version"] + 1, time.time()
        db.execute("INSERT INTO note_template_versions VALUES(?,?,?,?,?,?)",
                   (template_id, version, req.name, req.description, req.body, now))
        db.execute("UPDATE note_templates SET version=?,updated_at=? WHERE id=?", (version, now, template_id))
    return detail(template_id)


@router.delete("/api/note-templates/{template_id}")
def remove(template_id: str):
    value = snapshot(template_id)
    if value["builtin"]:
        raise HTTPException(409, "内置模板保留作为初始模板。")
    with store.connect() as db:
        db.execute("UPDATE note_templates SET archived=1,updated_at=? WHERE id=?", (time.time(), template_id))
    return {"ok": True}
