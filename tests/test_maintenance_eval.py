import json
import sqlite3
from contextlib import closing
from io import BytesIO
from pathlib import Path

import pytest

from lit_agent import documents, store
from scripts import evaluate_reading as evaluation
from scripts import maintenance


def labelled_dataset(paper_id):
    doc = store.one("SELECT * FROM documents WHERE paper_id=? AND status='ready'", (paper_id,))
    return {"synthetic": False, "cases": [{
        "id": "reviewed", "paper_id": paper_id, "category": "direct",
        "question": "What representations does the method use?",
        "reviewed": True, "reviewer": "test fixture reviewer",
        "expected_answer": "Graph representations.",
        "evidence": [{"file_hash": doc["file_hash"], "page": 1,
                      "quote": "The method uses graph representations for decision making."}],
    }]}


def retrieval_response(hits):
    def response(url, **kwargs):
        data = {"library_id": evaluation.library_identity()} if url.endswith("/api/health") else hits
        return BytesIO(json.dumps(data).encode())
    return response


def test_scaffold_cannot_be_scored_until_manually_labelled(indexed):
    draft = evaluation.scaffold()
    assert len(draft["cases"]) == 3 and all(not row["reviewed"] for row in draft["cases"])
    with pytest.raises(ValueError, match="尚无已审核"):
        evaluation.validated_cases(draft)


def test_gold_evidence_must_match_pdf_page(indexed):
    dataset = labelled_dataset(indexed[0])
    cases, excluded = evaluation.validated_cases(dataset)
    assert len(cases) == 1 and excluded == []
    dataset["cases"][0]["evidence"][0]["quote"] = "A made up answer that never occurs in the fixture."
    with pytest.raises(ValueError, match="不匹配"):
        evaluation.validated_cases(dataset)


def test_unanswerable_recall_is_not_counted_as_zero(indexed):
    dataset = labelled_dataset(indexed[0])
    row = {**dataset["cases"][0], "id": "no-answer", "category": "unanswerable", "evidence": []}
    dataset["cases"].append(row)
    cases, _ = evaluation.validated_cases(dataset)
    assert evaluation.evidence_recall(cases[1], [], {}) is None
    assert evaluation.evidence_recall(cases[0], [], {}) == 0


def test_evaluation_rejects_another_servers_corpus(indexed, monkeypatch):
    dataset = labelled_dataset(indexed[0])
    chunk = store.one("SELECT * FROM chunks LIMIT 1")
    chunk["text"] = "Different database text."
    monkeypatch.setattr(evaluation.urllib.request, "urlopen", retrieval_response([chunk]))
    with pytest.raises(ValueError, match="不一致"):
        evaluation.retrieve_report(dataset, "http://127.0.0.1:7860")


def test_retrieval_report_counts_only_reviewed_answerable_questions(indexed, monkeypatch):
    dataset = labelled_dataset(indexed[0])
    dataset["cases"].append({**dataset["cases"][0], "id": "no-answer",
                            "category": "unanswerable", "evidence": []})
    dataset["cases"].append({"id": "draft", "reviewed": False})
    hits = store.rows("SELECT * FROM chunks WHERE page=1")
    monkeypatch.setattr(evaluation.urllib.request, "urlopen", retrieval_response(hits))
    report = evaluation.retrieve_report(dataset, "http://127.0.0.1:7860")
    assert report["excluded"] == ["draft"] and report["questions"] == 2
    assert len(report["rows"]) == 6
    assert all(value["answerable_questions"] == 1 for value in report["metrics"].values())
    assert all(value["mean_evidence_recall_at_5"] == 1 for value in report["metrics"].values())


def test_backup_preserves_db_and_pdf_without_env_files(indexed, tmp_path, monkeypatch):
    monkeypatch.setattr(maintenance, "DB_FILE", store.DB_FILE)
    monkeypatch.setattr(maintenance, "DATA", documents.DATA)
    result = maintenance.backup(tmp_path / "backups")
    folder = Path(result["path"])
    assert result["complete"] and result["counts"]["papers"] == 1
    assert result["vectors_included"] is False and len(result["pdf_files"]) == 1
    with closing(sqlite3.connect(folder / "lit_agent.db")) as db:
        assert db.execute("SELECT count(*) FROM papers").fetchone()[0] == 1
    assert not list(folder.rglob(".env"))
    assert json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["complete"]


def test_backup_marks_incomplete_when_pending_work_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(maintenance, "DB_FILE", store.DB_FILE)
    store.enqueue("cleanup", {"document_ids": []})
    with pytest.raises(ValueError, match="停止服务"):
        maintenance.backup(tmp_path / "backups")
    manifests = list((tmp_path / "backups").glob("*/manifest.json"))
    assert len(manifests) == 1
    assert json.loads(manifests[0].read_text(encoding="utf-8"))["complete"] is False


def test_doctor_only_reports_key_presence(indexed, monkeypatch):
    monkeypatch.setattr(maintenance, "DB_FILE", store.DB_FILE)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-secret-do-not-print")
    result = maintenance.doctor()
    assert result["key_present"] and not result["key_validated"]
    assert "sk-test-secret" not in json.dumps(result)


def test_output_refuses_to_overwrite_prior_evaluation(tmp_path):
    path = tmp_path / "result.json"
    evaluation.write_new(path, {"run": 1})
    with pytest.raises(FileExistsError):
        evaluation.write_new(path, {"run": 2})
    assert json.loads(path.read_text()) == {"run": 1}


def test_prompt_pair_dry_run_and_equal_material(indexed, monkeypatch):
    from conftest import FakeModel
    from lit_agent import runtime
    from lit_agent.api import ReadingRequest, submit
    task = submit(ReadingRequest(message="Analyze the method", paper_ids=[indexed[0]], verify_evidence=False))
    result = runtime.run_reading(task, runtime.Guard(), FakeModel())
    with store.connect() as db:
        db.execute("UPDATE tasks SET status='succeeded',result=? WHERE id=?", (store.dumps(result), task["id"]))
    instances = []

    def model(guard):
        instance = FakeModel(guard)
        instances.append(instance)
        return instance

    monkeypatch.setattr(runtime, "Model", model)
    dry = evaluation.compare(task["id"])
    assert not dry["generated"] and instances == []
    assert dry["max_tokens_per_arm"] == store.get_task(task["id"])["checkpoint"]["generation_max_tokens"]
    pair = evaluation.compare(task["id"], allow_model_calls=True)
    assert pair["generated"] and len(instances) == 2
    assert instances[0].calls[0][1:] == instances[1].calls[0][1:]
    assert instances[0].calls[0][0] != instances[1].calls[0][0]
    assert pair["review"]["preferred"] is None
