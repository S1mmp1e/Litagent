"""Offline UI fixtures; the caller must provide an isolated database and data folder."""

import io
import json
import os
import time
import uuid

assert ".notebook-test-" in os.environ["LIT_DB_PATH"]

from reportlab.pdfgen import canvas

from lit_agent import database, documents, notebook, skills, store
from lit_agent.api import ReadingRequest, submit


def main():
    database.init_db()
    skills.save_profile({**skills.PROFILE_DEFAULTS, "research_domain": "Graph Learning",
                        "focus": "Reproducibility", "framework": "PyTorch"})
    papers, docs, citations = [], [], []
    for name in ("Graph Learning Study", "Multi-Agent Policy Study"):
        buffer = io.BytesIO()
        pdf = canvas.Canvas(buffer, invariant=1)
        for number in (1, 2, 3):
            pdf.setFont("Helvetica-Bold", 22)
            pdf.drawString(50, 760, name)
            pdf.setFont("Helvetica", 14)
            pdf.drawString(50, 710, f"PAGE {number} - NOTEBOOK SOURCE FIXTURE")
            pdf.drawString(50, 675, "The method uses a graph encoder and fixed evaluation conditions.")
            pdf.showPage()
        pdf.save()
        paper_id, digest = documents.upload(buffer.getvalue(), name)
        doc = store.one("SELECT id FROM documents WHERE paper_id=?", (paper_id,))["id"]
        chunk_id = str(uuid.uuid4())
        with store.connect() as db:
            db.execute("UPDATE papers SET authors=?,year=2025,venue='Test Conference' WHERE id=?",
                       ('["Test Author"]', paper_id))
            db.execute("UPDATE documents SET status='ready',page_count=3 WHERE id=?", (doc,))
            db.execute("INSERT INTO chunks VALUES(?,?,?,2,0,?)",
                       (chunk_id, doc, paper_id, "The method uses a graph encoder and fixed evaluation conditions."))
        papers.append(paper_id); docs.append(doc); citations.append(chunk_id)
    results = []
    for name, skill, selected in [("方法与实验阅读笔记", "deep_read", [0]), ("两篇论文方法对比", "compare", [0, 1])]:
        task = submit(ReadingRequest(message=name, paper_ids=[papers[i] for i in selected],
                                     skill_id=skill, template_id="paper-reading", verify_evidence=False))
        answer = ("# 图学习论文阅读\n\n## 核心方法\n\n"
                  "方法使用图编码器，在固定评估条件下验证。\n\n"
                  + " ".join(f"[cite:{citations[i]}]" for i in selected) +
                  "\n\n## 实验分析\n\n| 项目 | 记录 |\n|---|---|\n| 数据 | 测试文档 |\n| 结论 | 仅验证软件流程 |\n\n"
                  "## 我的思考\n\n进一步核对方法的适用条件与实验设计。")
        result = {"answer": answer, "document_ids": [docs[i] for i in selected],
                  "citations": [{"id": citations[i], "paper_id": papers[i], "page": 2} for i in selected],
                  "model": "offline-ui-fixture", "usage": {}, "template_check": {"status": "complete"}}
        with store.connect() as db:
            db.execute("INSERT INTO notes(task_id,version,body,created_at) VALUES(?,1,?,?)",
                       (task["id"], answer, time.time()))
            db.execute("UPDATE tasks SET status='succeeded',stage='done',result=? WHERE id=?",
                       (store.dumps(result), task["id"]))
            notebook.register(db, task, result)
        results.append(task["id"])
    print(json.dumps({"papers": papers, "documents": docs, "notes": results}))


if __name__ == "__main__":
    main()
