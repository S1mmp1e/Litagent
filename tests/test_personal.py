import json
import re
import uuid

import pytest
from fastapi.testclient import TestClient

from conftest import FakeModel
from lit_agent import documents, notebook, personal, retrieval, store
from lit_agent.api import ReadingRequest, submit
from lit_agent.jobs import Worker
from lit_agent.runtime import Guard, run_reading
from lit_agent.webui import app


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


def make_annotation(client, indexed, **values):
    doc = store.one("SELECT * FROM documents WHERE paper_id=? ORDER BY id DESC LIMIT 1", (indexed[0],))
    payload = {"paper_id": indexed[0], "file_hash": doc["file_hash"], "page": 1,
               "quote": "The method uses graph representations for decision making.",
               "rects": [[60, 750, 420, 775]], "comment": "My method must use local observations.",
               "enabled": True, **values}
    response = client.post("/api/annotations", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def edit(client, item, **values):
    response = client.put("/api/annotations/" + item["id"], json={
        "expected_revision": item["revision"], "comment": item["comment"], "color": item["color"],
        "enabled": item["enabled"], **values})
    assert response.status_code == 200, response.text
    return response.json()


def completed(indexed, monkeypatch, **values):
    import lit_agent.jobs as jobs
    monkeypatch.setattr(jobs, "Model", FakeModel)
    task = submit(ReadingRequest(message="Explain method", paper_ids=[indexed[0]], verify_evidence=False, **values))
    Worker().run_once()
    assert store.get_task(task["id"])["status"] == "succeeded"
    return task["id"]


def test_annotation_roundtrip_versions_conflicts_and_invalid_location(client, indexed):
    item = make_annotation(client, indexed)
    assert item["anchor_status"] == "matched"
    assert client.get(f"/api/annotations?paper_id={indexed[0]}&file_hash={item['file_hash']}").json() == [item]
    updated = edit(client, item, comment="Changed my mind", color="pink")
    assert updated["revision"] == 2
    detail = client.get("/api/annotations/" + item["id"]).json()
    assert [v["comment"] for v in detail["versions"]] == ["Changed my mind", item["comment"]]
    assert client.put("/api/annotations/" + item["id"], json={
        "expected_revision": 1, "comment": "stale", "color": "yellow", "enabled": True}).status_code == 409
    assert client.delete(f"/api/annotations/{item['id']}?expected_revision=1").status_code == 409
    assert client.delete(f"/api/annotations/{item['id']}?expected_revision=2").status_code == 200
    assert client.get("/api/annotations/" + item["id"]).status_code == 404
    doc = store.one("SELECT * FROM documents WHERE paper_id=?", (indexed[0],))
    for overrides in ({"page": 99}, {"rects": [[0, 0, 9999, 9999]]}, {"rects": [[5, 5, 1, 1]]},
                      {"file_hash": "a"*64}, {"quote": " "}):
        response = client.post("/api/annotations", json={
            "paper_id": indexed[0], "file_hash": doc["file_hash"], "page": 1,
            "quote": "text", "rects": [[60, 750, 420, 775]], **overrides})
        assert response.status_code in (409, 422), response.text


def test_unmatched_quote_is_not_presented_as_verified(client, indexed):
    item = make_annotation(client, indexed, quote="A fabricated number: accuracy is 99 percent")
    assert item["anchor_status"] == "unmatched"
    assert personal.candidates([indexed[0]])[0]["anchor_status"] == "unmatched"


def test_annotations_work_before_fulltext_indexing(client, pdf_bytes):
    paper, _ = documents.upload(pdf_bytes, "Not yet processed")
    item = make_annotation(client, (paper, {}))
    assert item["anchor_status"] == "matched"
    assert personal.candidates([paper]) == []


def test_hybrid_search_edit_delete_scope_and_source_validation(client, indexed):
    item = make_annotation(client, indexed)
    hits = personal.search("method local observations", [indexed[0]])
    assert hits and all(h["source_id"] == item["id"] for h in hits)
    assert personal.search("method", [999]) == []
    updated = edit(client, item, comment="Now I prefer global state")
    with pytest.raises(ValueError, match="修改"):
        personal.validate_sources(hits, [indexed[0]])
    changed = personal.search("method", [indexed[0]])
    assert changed and all(h["version"] == 2 and "local observations" not in h["text"] for h in changed)
    assert not ({h["id"] for h in hits} & {h["id"] for h in changed})
    client.delete(f"/api/annotations/{item['id']}?expected_revision={updated['revision']}")
    assert personal.search("method", [indexed[0]]) == []


def test_hybrid_rechecks_visibility_after_vector_search(client, indexed, monkeypatch):
    item = make_annotation(client, indexed)
    vector_client = retrieval.vector_client()
    original = vector_client.query_points

    def withdraw(*args, **kwargs):
        result = original(*args, **kwargs)
        edit(client, item, enabled=False)
        return result

    monkeypatch.setattr(vector_client, "query_points", withdraw)
    assert personal.search("method", [indexed[0]]) == []


def test_missing_vector_collection_is_rebuilt_without_deleting_new_points(client, indexed):
    make_annotation(client, indexed)
    hits = personal.search("method", [indexed[0]])
    retrieval.vector_client().delete_collection(personal.COLLECTION)
    # This query shares no keyword; only rebuilt semantic points can return a hit.
    rebuilt = personal.search("unseenword", [indexed[0]])
    assert rebuilt and {h["id"] for h in rebuilt} == {h["id"] for h in hits}


def test_note_opt_in_is_specific_to_latest_version_and_excludes_trash(client, indexed, monkeypatch):
    task_id = completed(indexed, monkeypatch)
    assert not personal.candidates([indexed[0]])
    assert client.put(f"/api/personal/notes/{task_id}", json={"version": 1, "enabled": True}).status_code == 200
    assert personal.candidates([indexed[0]])[0]["kind"] == "note"
    assert client.get("/api/notes/" + task_id).json()["retrieval_version"] == 1
    assert personal.search("method", [indexed[0]])
    client.put(f"/api/tasks/{task_id}/notes", json={"body": "My new observations.", "expected_version": 1})
    assert personal.candidates([indexed[0]]) == []
    assert client.get("/api/notes/" + task_id).json()["retrieval_version"] is None
    assert client.put(f"/api/personal/notes/{task_id}", json={"version": 1, "enabled": True}).status_code == 409
    client.put(f"/api/personal/notes/{task_id}", json={"version": 2, "enabled": True})
    hits = personal.search("observations", [indexed[0]])
    assert hits and all(h["version"] == 2 for h in hits)
    client.patch(f"/api/notes/{task_id}", json={"trashed": True})
    assert not personal.search("method", [indexed[0]])
    assert not store.one("SELECT 1 FROM notes WHERE task_id=? AND approved=1", (task_id,))


def test_comparison_note_does_not_leak_other_paper_material(client, indexed, monkeypatch, pdf_bytes):
    second_id, _ = documents.upload(pdf_bytes + b"\n% second test document", "Second")
    with store.connect() as db:
        db.execute("UPDATE documents SET status='ready' WHERE paper_id=?", (second_id,))
    first_doc = store.one("SELECT id FROM documents WHERE paper_id=?", (indexed[0],))
    second_doc = store.one("SELECT id FROM documents WHERE paper_id=?", (second_id,))
    task = submit(ReadingRequest(message="Compare", paper_ids=[indexed[0], second_id], skill_id="compare"))
    result = {"document_ids": [first_doc["id"], second_doc["id"]], "citations": [], "answer": "private"}
    with store.connect() as db:
        db.execute("INSERT INTO notes(task_id,version,body,created_at) VALUES(?,1,'private',1)", (task["id"],))
        notebook.register(db, task, result)
    client.put("/api/personal/notes/" + task["id"], json={"version": 1, "enabled": True})
    assert personal.candidates([indexed[0]]) == []
    assert len(personal.candidates([indexed[0], second_id])) == 1


class PersonalModel(FakeModel):
    def complete(self, messages, **kwargs):
        response = super().complete(messages, **kwargs)
        ids = re.findall(r"\[personal:([a-f0-9-]{36})\]", messages[-1]["content"])
        if ids:
            response["content"] += "\n\nYour personal note requests local observations. [personal:" + ids[0] + "]"
        return response


def test_personal_reading_scoped_citations_snapshots_export(client, indexed, monkeypatch):
    import lit_agent.jobs as jobs
    item = make_annotation(client, indexed)
    monkeypatch.setattr(jobs, "Model", PersonalModel)
    task = submit(ReadingRequest(message="method", paper_ids=[indexed[0]], action="question",
                                 use_personal=True, verify_evidence=False))
    Worker().run_once()
    done = store.get_task(task["id"])
    assert done["status"] == "succeeded", done["error"]
    result = done["result"]
    assert result["citations"] and result["personal_citations"]
    assert all(c["id"] not in {s["id"] for s in result["personal_sources"]} for c in result["citations"])
    source = result["personal_citations"][0]
    url = f"/api/personal/evidence/{task['id']}/{source['id']}"
    assert client.get(url).json()["current"]
    edit(client, item, comment="Revised", enabled=False)
    snapshot = client.get(url).json()
    assert not snapshot["current"] and "local observations" in snapshot["text"]
    markdown = client.get(f"/api/notes/{task['id']}/export").text
    assert "[^personal-1]" in markdown and "[personal:" not in markdown
    assert client.put(f"/api/tasks/{task['id']}/notes", json={
        "body": result["answer"] + f"[personal:{uuid.uuid4()}]"}).status_code == 422


def test_disabling_personal_does_not_search_or_inherit_personal_history(client, indexed, monkeypatch):
    make_annotation(client, indexed)
    monkeypatch.setattr(personal, "search", lambda *a, **kw: pytest.fail("personal search is off"))
    task = submit(ReadingRequest(message="method", paper_ids=[indexed[0]], action="question", verify_evidence=False))
    result = run_reading(task, Guard(), FakeModel())
    assert result["personal_sources"] == []


def test_concurrent_edit_prevents_saving_stale_generated_answer(client, indexed):
    item = make_annotation(client, indexed)

    class EditingModel(PersonalModel):
        def complete(self, messages, **kwargs):
            answer = super().complete(messages, **kwargs)
            if "[personal:" in messages[-1]["content"]:
                edit(client, item, enabled=False)
            return answer

    task = submit(ReadingRequest(message="method", paper_ids=[indexed[0]], action="question",
                                 use_personal=True, verify_evidence=False))
    with pytest.raises(ValueError, match="修改"):
        run_reading(task, Guard(), EditingModel())
    assert not store.one("SELECT 1 FROM notes WHERE task_id=?", (task["id"],))


def test_personal_only_question_and_forged_citations(client, indexed, monkeypatch):
    make_annotation(client, indexed)
    monkeypatch.setattr(retrieval, "search", lambda *a, **kw: [])
    task = submit(ReadingRequest(message="我的批注提到了什么", paper_ids=[indexed[0]], action="question",
                                 use_personal=True, verify_evidence=False))
    result = run_reading(task, Guard(), PersonalModel())
    assert result["personal_citations"] and not result["citations"]
    with pytest.raises(ValueError, match="未提供"):
        personal.check_citations(f"[personal:{uuid.uuid4()}]", result["personal_sources"])


def test_reindex_same_file_preserves_annotation(client, indexed):
    item = make_annotation(client, indexed)
    task = store.enqueue("ingest", {"paper_id": indexed[0], "file_hash": item["file_hash"], "force": True})
    documents.process(task, Guard())
    assert client.get("/api/annotations/" + item["id"]).json()["quote"] == item["quote"]
    assert personal.search("method", [indexed[0]])
