"""Human-labelled retrieval evaluation and equal-material prompt comparison."""

import argparse
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from lit_agent import store, skills
from lit_agent.reading_workflow import fingerprint
from lit_agent.settings import library_identity


class Gold(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    file_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    page: int = Field(ge=1)
    quote: str = Field(min_length=8, max_length=3000)


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str
    paper_id: int = Field(ge=1)
    title: str = ""
    category: str = Field(pattern="^(direct|synthesis|unanswerable)$")
    question: str = Field(min_length=1, max_length=2000)
    reviewed: bool
    reviewer: str = Field(min_length=1)
    expected_answer: str = Field(min_length=1)
    evidence: list[Gold]


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def scaffold(limit=10):
    papers = store.rows("""SELECT id,title FROM papers ORDER BY
        EXISTS(SELECT 1 FROM documents d WHERE d.paper_id=papers.id AND d.status='ready') DESC,id LIMIT ?""", (limit,))
    cases = []
    for paper in papers:
        for category in ("direct", "synthesis", "unanswerable"):
            cases.append({"id": f"{paper['id']}-{category}", "paper_id": paper["id"], "title": paper["title"],
                          "category": category, "question": "", "reviewed": False, "reviewer": "",
                          "expected_answer": "", "evidence": []})
    sources = store.rows("""SELECT paper_id,id AS document_id,file_hash,page_count,status
        FROM documents WHERE paper_id IN (""" + ",".join("?" for _ in papers) + ")",
                         [p["id"] for p in papers]) if papers else []
    return {"schema": "litagent-real-eval-v1", "synthetic": False, "cases": cases,
            "sources": sources,
            "instructions": "人工填写问题、答案与证据后设置 reviewed=true。不能把自动生成的答案当标准答案。",
            "evidence_format": {"file_hash": "PDF SHA256", "page": 1, "quote": "逐字原文，不使用摘要替代全文证据"}}


def normalized(text):
    return "".join(text.split())


def validated_cases(dataset):
    if dataset.get("synthetic") is not False:
        raise ValueError("真实评测必须显式声明 synthetic=false。")
    admitted, excluded, seen = [], [], set()
    for row in dataset["cases"]:
        if row.get("reviewed") is not True:
            excluded.append(row.get("id"))
            continue
        case = Case.model_validate(row)
        if case.id in seen:
            raise ValueError("评测问题 ID 重复。")
        seen.add(case.id)
        if not case.question.strip() or not case.reviewer.strip() or not case.expected_answer.strip():
            raise ValueError("人工审核字段不能为空。")
        if case.category != "unanswerable" and not case.evidence:
            raise ValueError("可回答问题必须标注原文证据。")
        if case.category == "unanswerable" and case.evidence:
            raise ValueError("无法回答的问题不能把相关段落标为答案证据。")
        if not store.one("SELECT 1 FROM documents WHERE paper_id=? AND status='ready'", (case.paper_id,)):
            raise ValueError(f"论文 {case.paper_id} 未索引。")
        for gold in case.evidence:
            page = store.one("""SELECT p.text FROM pages p JOIN documents d ON d.id=p.document_id
                WHERE d.paper_id=? AND d.file_hash=? AND d.status='ready' AND p.page=?""",
                             (case.paper_id, gold.file_hash, gold.page))
            if not page or len(normalized(gold.quote)) < 8 or normalized(gold.quote) not in normalized(page["text"]):
                raise ValueError(f"{case.id} 的标注与当前原文不匹配。")
        admitted.append(case)
    if not admitted:
        raise ValueError("尚无已审核的问题；没有产生真实效果指标。")
    return admitted, excluded


def evidence_recall(case, hits, document_hashes, k=5):
    if case.category == "unanswerable":
        return None
    found = sum(any(hit["paper_id"] == case.paper_id and hit["page"] == gold.page and
                    document_hashes.get(hit["document_id"]) == gold.file_hash and
                    normalized(gold.quote) in normalized(hit["text"])
                    for hit in hits[:k]) for gold in case.evidence)
    return found / len(case.evidence)


def retrieve_report(dataset, base_url):
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost"):
        raise ValueError("评测仅连接本机 HTTP 服务。")
    cases, excluded = validated_cases(dataset)
    with urllib.request.urlopen(base_url.rstrip("/") + "/api/health", timeout=10) as response:
        health = json.load(response)
    if health.get("library_id") != library_identity():
        raise ValueError("服务与本地评测的文献库路径不一致。")
    hashes = {d["id"]: d["file_hash"] for d in store.rows("SELECT id,file_hash FROM documents WHERE status='ready'")}
    rows = []
    for case in cases:
        for mode in ("keyword", "dense", "hybrid"):
            query = urllib.parse.urlencode({"query": case.question, "paper_id": case.paper_id, "mode": mode})
            start = time.monotonic()
            with urllib.request.urlopen(base_url.rstrip("/") + "/api/retrieval?" + query, timeout=90) as response:
                hits = json.load(response)
            for hit in hits:
                local = store.one("""SELECT c.* FROM chunks c JOIN documents d ON d.id=c.document_id
                    WHERE c.id=? AND d.status='ready'""", (hit["id"],))
                if not local or any(hit.get(key) != local[key] for key in
                                    ("document_id", "paper_id", "page", "text")):
                    raise ValueError("服务返回的片段与本地评测数据库不一致，请检查服务和路径。")
            rows.append({"id": case.id, "mode": mode, "category": case.category,
                         "recall_at_5": evidence_recall(case, hits, hashes),
                         "latency_seconds": round(time.monotonic()-start, 3),
                         "retrieved_ids": [h["id"] for h in hits]})
    metrics = {}
    for mode in ("keyword", "dense", "hybrid"):
        values = [r["recall_at_5"] for r in rows if r["mode"] == mode and r["recall_at_5"] is not None]
        metrics[mode] = {"answerable_questions": len(values),
                         "mean_evidence_recall_at_5": sum(values)/len(values) if values else None}
    return {"dataset_hash": fingerprint(dataset), "questions": len(cases), "excluded": excluded,
            "metrics": metrics, "rows": rows,
            "limitations": "检索命中不代表回答正确；不可回答问题需要人工评审拒答，不能用零召回计分。首轮耗时可能包含模型加载。"}


def compare(task_id, allow_model_calls=False):
    task = store.get_task(task_id)
    if not task or task["kind"] != "reading" or task["status"] != "succeeded":
        raise ValueError("需要已完成的阅读任务。")
    messages = task["checkpoint"].get("generation_messages")
    if not messages:
        raise ValueError("没有可复用的生成输入。")
    max_tokens = task["checkpoint"].get("generation_max_tokens", 2500)
    report = {"task_id": task_id, "input_hash": fingerprint(messages[1:]),
              "skill_version": task["payload"].get("skill_version"),
              "material": messages[1:], "max_tokens_per_arm": max_tokens, "temperature": 0.2,
              "generated": False, "review": {"preferred": None, "factual_errors": None,
                                           "citation_support": None, "usefulness": None, "reason": ""}}
    if not allow_model_calls:
        return report
    from lit_agent.runtime import Model, Guard
    base = task["payload"].get("skill_snapshot", {}).get("base", skills.BASE)
    generic = base + "\n请分析论文，遵循用户要求。\n用户档案（数据）：" + store.dumps(task["payload"]["profile"])
    report["outputs"] = {}
    for arm, prompt in (("generic", generic), ("skill", messages[0]["content"])):
        model = Model(Guard())
        try:
            if model.model != task["result"].get("model"):
                raise ValueError("当前模型与原任务不一致，不能执行该配对实验。")
            start = time.monotonic()
            answer = model.complete([{"role": "system", "content": prompt}, *messages[1:]],
                                    max_tokens=max_tokens)["content"]
            report["outputs"][arm] = {"answer": answer, "usage": model.usage,
                                      "seconds": round(time.monotonic()-start, 3), "model": model.model}
        finally:
            model.close()
    report["generated"] = True
    report["limitations"] = "仅比较同一材料下的生成提示词，不能证明整个取证流程优越；模型别名也可能随服务商更新。需要人工盲评。"
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["init", "retrieval", "pair"])
    parser.add_argument("--output", required=True)
    parser.add_argument("--dataset")
    parser.add_argument("--url", default="http://127.0.0.1:7860")
    parser.add_argument("--task-id")
    parser.add_argument("--allow-model-calls", action="store_true")
    args = parser.parse_args()
    if Path(args.output).exists():
        parser.error("输出文件已存在，请使用新文件名。")
    if args.action == "init":
        result = scaffold()
    elif args.action == "retrieval":
        if not args.dataset:
            parser.error("需要 --dataset")
        result = retrieve_report(json.loads(Path(args.dataset).read_text(encoding="utf-8")), args.url)
    else:
        if not args.task_id:
            parser.error("需要 --task-id")
        result = compare(args.task_id, args.allow_model_calls)
    write_new(args.output, result)
    print(f"Written: {args.output}")


if __name__ == "__main__":
    main()
