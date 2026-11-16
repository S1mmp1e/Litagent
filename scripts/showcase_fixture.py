"""Owned synthetic documents for public screenshots; never use a real library."""

import io
import json
import os
import time

assert ".notebook-test-showcase-" in os.environ["LIT_DB_PATH"]
assert not os.environ.get("DEEPSEEK_API_KEY") and not os.environ.get("LIT_API_KEY")

from reportlab.lib.colors import HexColor
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas

from lit_agent import category_manager, database, documents, notebook, retrieval, skills, store
from lit_agent.api import ReadingRequest, submit
from lit_agent.runtime import Guard
from tests.personal_server import TestEncoder

PAPERS = (
    ("Graph Policies for Local Coordination", (
        ("Problem and assumptions", [
            "This is an original LitAgent demonstration document, not a published research paper.",
            "The policy uses a graph encoder to aggregate messages from visible neighbors. "
            "Each agent receives local observations and a relative-position message.",
            "Training may use global state for the critic. Deployment uses local observations "
            "and neighbor messages only. The communication radius is an explicit assumption.",
        ]),
        ("Method and implementation", [
            "A shared encoder maps each observation to a feature vector. A relation-aware "
            "message function combines the source feature with relative position.",
            "The actor selects actions after one message-passing step. The critic is used "
            "only during centralized training and is absent from decentralized inference.",
            "A reproduction should record the observation schema, graph construction, "
            "communication radius and the masking rule for unavailable neighbors.",
        ]),
        ("Evaluation plan and limits", [
            "Compare local-only and message-passing policies under identical seeds, "
            "training budgets and evaluation horizons.",
            "Report task success, message volume and inference latency separately. "
            "This demonstration contains no measured benchmark results.",
            "Future checks include observation noise, missing neighbors and larger teams. "
            "Scale transfer is a hypothesis to test, not an established conclusion.",
        ]),
    )),
    ("Memory Policies under Partial Observation", (
        ("Problem and assumptions", [
            "This is an original LitAgent demonstration document, not a published research paper.",
            "The policy uses recurrent memory to summarize an agent's observation history. "
            "Agents do not exchange messages during decentralized inference.",
            "Memory can preserve information after a target becomes temporarily unobserved. "
            "The available history and reset policy are explicit assumptions.",
        ]),
        ("Method and implementation", [
            "An observation encoder feeds a gated recurrent unit. The actor consumes the "
            "updated hidden state, while a centralized critic is used only for training.",
            "Reproduction must specify sequence length, episode masks, hidden-state resets "
            "and the handling of truncated training sequences.",
            "The model does not guarantee a correct belief about unobserved targets.",
        ]),
        ("Evaluation plan and limits", [
            "Compare recurrent and feed-forward policies with matched observations and budgets.",
            "Measure task success across controlled observation gaps and compare latency. "
            "This demonstration contains no measured benchmark results.",
            "Comparisons with communicating policies require explicitly accounting for "
            "the additional information received through neighbor messages.",
        ]),
    )),
)


def make_pdf(title, sections):
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(595, 842), invariant=1)
    pdf.setTitle(title)
    pdf.setAuthor("LitAgent demo fixture")
    for page, (heading, paragraphs) in enumerate(sections, 1):
        pdf.setFillColor(HexColor("#157b70"))
        pdf.rect(44, 792, 32, 4, fill=1, stroke=0)
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawString(86, 790, "LITAGENT  /  DEMONSTRATION SOURCE")
        y = 740
        pdf.setFillColor(HexColor("#18282b"))
        pdf.setFont("Helvetica-Bold", 23)
        for line in simpleSplit(title, "Helvetica-Bold", 23, 495):
            pdf.drawString(44, y, line)
            y -= 29
        y -= 28
        pdf.setFont("Helvetica-Bold", 15)
        pdf.drawString(44, y, f"{page}. {heading}")
        y -= 38
        pdf.setFont("Helvetica", 12)
        for paragraph in paragraphs:
            for line in simpleSplit(paragraph, "Helvetica", 12, 495):
                pdf.drawString(44, y, line)
                y -= 20
            y -= 20
        pdf.setStrokeColor(HexColor("#cfdbd9"))
        pdf.line(44, 65, 550, 65)
        pdf.setFillColor(HexColor("#58706a"))
        pdf.setFont("Helvetica", 9)
        pdf.drawString(44, 45, "Synthetic example. No real publication or measured research results.")
        pdf.drawRightString(550, 45, str(page))
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def main():
    database.init_db()
    retrieval.get_embedder = lambda: TestEncoder()
    skills.save_profile({
        **skills.PROFILE_DEFAULTS, "research_domain": "多智能体强化学习",
        "background": "计算机专业研究生，熟悉 PPO 与图神经网络",
        "purpose": "理解方法的信息需求，设计可复现的对照实验",
        "focus": "局部观测、通信约束、集中训练与分散执行",
        "depth": "advanced", "framework": "PyTorch", "compute_budget": "单卡实验",
    })
    category_manager.create_category("多智能体学习", "局部观测、协同决策与通信", "#167b70")
    category_manager.create_category("复现实验", "实验条件与研究备忘", "#4d67b3")
    category = next(c["id"] for c in category_manager.list_categories() if c["name"] == "多智能体学习")
    papers, docs, sources = [], [], []
    for title, sections in PAPERS:
        paper_id, digest = documents.upload(make_pdf(title, sections), title)
        task = store.enqueue("ingest", {"paper_id": paper_id, "file_hash": digest, "force": False})
        result = documents.process(task, Guard())
        with store.connect() as db:
            db.execute("UPDATE tasks SET status='succeeded',stage='done',result=? WHERE id=?",
                       (store.dumps(result), task["id"]))
            db.execute("UPDATE papers SET authors=?,year=2026,venue=?,abstract=? WHERE id=?",
                       (json.dumps(["LitAgent Demo"]), "演示材料 · 非正式论文",
                        "用于展示阅读、引用、笔记与批注流程；不包含真实实验结论。", paper_id))
        category_manager.manually_categorize(paper_id, category)
        doc = store.one("SELECT id FROM documents WHERE paper_id=? AND status='ready'", (paper_id,))["id"]
        chunks = store.rows("SELECT id,paper_id,page FROM chunks WHERE document_id=? ORDER BY page,ordinal", (doc,))
        papers.append(paper_id)
        docs.append(doc)
        sources.append(chunks)
    first = next(c["id"] for c in sources[0] if c["page"] == 1)
    method = next(c["id"] for c in sources[0] if c["page"] == 2)
    limit = next(c["id"] for c in sources[0] if c["page"] == 3)
    second = next(c["id"] for c in sources[1] if c["page"] == 1)
    answers = [
        ("图策略：信息需求与复现要点", "deep_read", [0],
         "# 图策略：信息需求与复现要点\n\n> 演示笔记 · 基于仓库生成的示例 PDF，未调用线上模型。\n\n"
         "## 一句话结论\n\n这份材料通过邻居消息传递补充局部观测；部署条件必须同时包含可见范围与通信约束。"
         f"[cite:{first}]\n\n"
         "## 核心方法\n\n**局部观测 → 共享编码器 → 邻居消息聚合 → 动作输出**。\n\n"
         f"训练阶段的 critic 可以使用全局状态，执行阶段只保留 actor 与邻居消息。[cite:{method}]\n\n"
         "| 核对项 | 原文信息 | 复现时需要记录 |\n|---|---|---|\n"
         "| 观测 | 局部观测与相对位置 | 特征顺序、维度与归一化 |\n"
         "| 通信 | 可见邻居消息 | 半径、邻接关系与缺失掩码 |\n"
         "| 决策 | 一次消息传递后输出动作 | 推理时的信息来源 |\n\n"
         f"上述流程来自方法说明。[cite:{method}]\n\n"
         "## 实验与边界\n\n材料提出在相同种子、预算和时长下比较局部策略与通信策略，"
         f"分别记录成功率、通信量和时延。**未报告实测结果，不能声称性能提升。**[cite:{limit}]\n\n"
         "## 我的研究启发\n\n先核对信息条件是否与目标环境一致，再设计通信缺失消融；这是读者的验证计划。"),
        ("通信与记忆：两种局部决策方案", "compare", [0, 1],
         "# 通信与记忆：两种局部决策方案\n\n> 演示对比 · 基于两份原创示例材料。\n\n"
         "## 方法对齐\n\n| 维度 | 图通信策略 A | 循环记忆策略 B |\n|---|---|---|\n"
         "| 信息来源 | 当前局部观测与邻居消息 | 当前观测与历史状态 |\n"
         "| 核心模块 | 图编码与消息聚合 | 观测编码与 GRU |\n"
         "| 部署约束 | 需要定义通信范围 | 需要维护与重置隐状态 |\n"
         "| 待验证问题 | 通信缺失时的退化 | 观测间断后的恢复 |\n\n"
         f"两份材料采用不同的信息条件。[cite:{first}] [cite:{second}]\n\n"
         "## 比较是否公平\n\n先统一观测、训练预算、评估时长与种子，并单独报告通信带来的额外信息。"
         "两份材料均没有实测结果，目前不能判断哪一种更优。\n\n"
         "## 下一步实验\n\n设置无通信、受限通信和不同观测间隔，记录成功率与推理时延；"
         "这是建议实验，不是作者已经完成的结果。"),
    ]
    notes = []
    for title, skill, selected, answer in answers:
        task = submit(ReadingRequest(message=title, paper_ids=[papers[i] for i in selected],
                                     skill_id=skill, template_id="paper-reading", verify_evidence=False))
        result = {"answer": answer, "document_ids": [docs[i] for i in selected],
                  "citations": [c for i in selected for c in sources[i]],
                  "model": "offline-showcase-fixture", "usage": {},
                  "template_check": {"status": "complete"}}
        with store.connect() as db:
            db.execute("INSERT INTO notes(task_id,version,body,created_at) VALUES(?,1,?,?)",
                       (task["id"], answer, time.time()))
            db.execute("UPDATE tasks SET status='succeeded',stage='done',result=? WHERE id=?",
                       (store.dumps(result), task["id"]))
            notebook.register(db, task, result)
        notes.append(task["id"])
    print(json.dumps({"papers": papers, "documents": docs, "notes": notes}))


if __name__ == "__main__":
    main()
