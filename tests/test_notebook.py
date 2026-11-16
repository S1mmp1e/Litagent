import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from conftest import FakeModel
from lit_agent import documents, note_templates, notebook, settings, skills, store
from lit_agent.api import ReadingRequest, submit
from lit_agent.jobs import Worker
from lit_agent.runtime import Guard, run_reading
from lit_agent.webui import app


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


def completed(indexed, monkeypatch, **options):
    import lit_agent.jobs as jobs
    monkeypatch.setattr(jobs, "Model", FakeModel)
    task = submit(ReadingRequest(message="Explain the method", paper_ids=[indexed[0]],
                                 verify_evidence=False, **options))
    Worker().run_once()
    assert store.get_task(task["id"])["status"] == "succeeded"
    return task["id"]


def test_existing_notes_backfill_is_idempotent(client, indexed, monkeypatch):
    task_id = completed(indexed, monkeypatch)
    original = store.one("SELECT body FROM notes WHERE task_id=?", (task_id,))["body"]
    with store.connect() as db:
        db.execute("DELETE FROM notebooks WHERE task_id=?", (task_id,))
    store.migrate()
    store.migrate()
    item = client.get(f"/api/notes/{task_id}").json()
    assert item["versions"][0]["body"] == original
    assert len(item["versions"]) == 1
    assert len(item["sources"]) == 1 and item["citations"]
    assert client.get("/api/notes").json()["total"] == 1


def test_catalog_filters_edits_and_trash(client, indexed, monkeypatch):
    first = completed(indexed, monkeypatch)
    second = completed(indexed, monkeypatch)
    assert client.get("/api/notes?limit=1").json()["total"] == 2
    assert len(client.get("/api/notes?limit=1&offset=1").json()["items"]) == 1
    client.patch(f"/api/notes/{first}", json={"title": "My graph analysis", "starred": True})
    assert client.get("/api/notes?query=graph").json()["total"] == 1
    assert client.get("/api/notes?view=starred").json()["items"][0]["task_id"] == first
    assert client.get(f"/api/notes?paper_id={indexed[0]}").json()["total"] == 2
    assert client.get("/api/notes?skill_id=compare").json()["total"] == 0
    assert client.get("/api/notes/paper-counts").json()[0]["count"] == 2
    client.patch(f"/api/notes/{first}", json={"trashed": True})
    assert client.get("/api/notes").json()["items"][0]["task_id"] == second
    assert client.get("/api/notes?view=trash").json()["items"][0]["task_id"] == first
    assert client.put(f"/api/tasks/{first}/notes", json={"body": "edit"}).status_code == 409
    client.patch(f"/api/notes/{first}", json={"trashed": False})
    assert client.get("/api/notes").json()["total"] == 2


def test_latest_note_follows_paper_scope_and_excludes_trash(client, indexed, monkeypatch):
    url = f"/api/notes/latest?paper_id={indexed[0]}"
    assert client.get(url).json() is None
    first = completed(indexed, monkeypatch)
    second = completed(indexed, monkeypatch)
    with store.connect() as db:
        db.execute("UPDATE notebooks SET updated_at=1 WHERE task_id=?", (first,))
        db.execute("UPDATE notebooks SET updated_at=2 WHERE task_id=?", (second,))
    assert client.get(url).json() == {"task_id": second}
    assert client.get("/api/notes/latest?paper_id=999999").json() is None
    client.patch(f"/api/notes/{second}", json={"trashed": True})
    assert client.get(url).json() == {"task_id": first}
    client.patch(f"/api/notes/{first}", json={"trashed": True})
    assert client.get(url).json() is None


@pytest.mark.parametrize("query", ["", "?paper_id=0", "?paper_id=-1", "?paper_id=invalid",
                                   "?paper_id=1&paper_id=2&paper_id=3"])
def test_latest_note_validates_selection(client, query):
    assert client.get("/api/notes/latest" + query).status_code == 422


def test_saved_pdf_and_quotes_survive_library_deletion(client, indexed, monkeypatch):
    task_id = completed(indexed, monkeypatch)
    before = client.get(f"/api/notes/{task_id}").json()
    source = before["sources"][0]
    documents.delete_paper(indexed[0])
    after = client.get(f"/api/notes/{task_id}").json()
    assert not after["sources"][0]["in_library"]
    assert after["citations"] == before["citations"]
    assert client.get(source["pdf_url"]).content.startswith(b"%PDF-")
    assert (settings.DATA / "pdf" / f"{source['file_hash']}.pdf").is_file()
    old_citation = client.get("/api/chunks/" + after["citations"][0]["id"]).json()
    assert f"/api/notes/{task_id}/" in old_citation["pdf_url"]
    response = client.put(f"/api/tasks/{task_id}/notes", json={
        "body": before["versions"][0]["body"] + "\n\nMy annotation.", "expected_version": 1})
    assert response.status_code == 200 and response.json()[0]["version"] == 2


def test_versions_conflict_and_scoped_sources(client, indexed, monkeypatch):
    task_id = completed(indexed, monkeypatch)
    body = client.get(f"/api/notes/{task_id}").json()["versions"][0]["body"]
    edit = {"body": body + "\n\nUpdated", "expected_version": 1}
    assert client.put(f"/api/tasks/{task_id}/notes", json=edit).status_code == 200
    assert client.put(f"/api/tasks/{task_id}/notes", json=edit).status_code == 409
    assert client.get(f"/api/notes/{task_id}/sources/9999/pdf").status_code == 404
    assert client.patch(f"/api/notes/{task_id}", json={"title": "   "}).status_code == 422


def test_reindex_does_not_rewrite_saved_evidence(client, indexed, monkeypatch, pdf_bytes):
    task_id = completed(indexed, monkeypatch)
    before = client.get(f"/api/notes/{task_id}").json()["citations"]
    process = store.enqueue("ingest", {"paper_id": indexed[0], "file_hash": documents.save_pdf(pdf_bytes), "force": True})
    documents.process(process, Guard())
    with store.connect() as db:
        db.execute("UPDATE chunks SET text='A changed extraction'")
    assert client.get(f"/api/notes/{task_id}").json()["citations"] == before


def test_exports_contain_selected_version_and_portable_sources(client, indexed, monkeypatch):
    task_id = completed(indexed, monkeypatch)
    body = client.get(f"/api/notes/{task_id}").json()["versions"][0]["body"]
    client.put(f"/api/tasks/{task_id}/notes", json={"body": body + "\n\nSECOND REVISION"})
    first = client.get(f"/api/notes/{task_id}/export?version=1")
    assert "SECOND REVISION" not in first.text and "[^1]" in first.text
    archive = client.get(f"/api/notes/{task_id}/export?format=zip&version=2")
    with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
        assert "SECOND REVISION" in bundle.read("note.md").decode()
        assert "sources.json" in bundle.namelist()
        pdfs = [name for name in bundle.namelist() if name.endswith(".pdf")]
        assert len(pdfs) == 1 and bundle.read(pdfs[0]).startswith(b"%PDF-")
        assert pdfs[0] in bundle.read("note.md").decode()
    assert client.get(f"/api/notes/{task_id}/export?version=999").status_code == 404


def test_two_builtin_templates_and_personal_version_history(client):
    presets = client.get("/api/note-templates").json()
    assert len(presets) == 2 and all(item["builtin"] for item in presets)
    content = {"name": "My analysis", "body": "## Key idea\nExplain the mechanism", "description": ""}
    custom = client.post("/api/note-templates", json=content).json()
    updated = client.put(f"/api/note-templates/{custom['id']}", json={
        **content, "body": "## New structure\nExplain the experiments", "expected_version": 1}).json()
    assert updated["version"] == 2 and len(updated["versions"]) == 2
    assert updated["versions"][1]["body"] == content["body"]
    assert client.put(f"/api/note-templates/{custom['id']}", json={
        **content, "expected_version": 1}).status_code == 409
    assert client.put("/api/note-templates/paper-reading", json={
        **content, "expected_version": 1}).status_code == 409
    assert client.delete("/api/note-templates/paper-reading").status_code == 409
    assert client.post("/api/note-templates", json={**content, "body": "  "}).status_code == 422


def test_task_freezes_template_and_model_receives_it(client, indexed):
    with store.connect() as db:
        db.execute("UPDATE papers SET authors=?,year=2025 WHERE id=?", ('["Original Author"]', indexed[0]))
    original = {"name": "My methods template", "body":
                "## Original method analysis\nExplain inputs and outputs.\n" +
                "\n".join(f"{field['label']}: {{{{{field['id']}}}}}" for field in note_templates.FIELD_CATALOG)}
    template = client.post("/api/note-templates", json=original).json()
    task = submit(ReadingRequest(message="Explain", paper_ids=[indexed[0]], template_id=template["id"],
                                 template_version=1, verify_evidence=False))
    client.put(f"/api/note-templates/{template['id']}", json={
        **original, "body": "## Changed later", "expected_version": 1})
    client.delete(f"/api/note-templates/{template['id']}")
    frozen = task["payload"]["template_values"]
    with store.connect() as db:
        db.execute("UPDATE papers SET authors=?,year=2030 WHERE id=?", ('["Later Author"]', indexed[0]))
    skills.save_profile({**skills.PROFILE_DEFAULTS, "purpose": "Changed purpose"})
    model = FakeModel()
    result = run_reading(task, Guard(), model)
    expected = "## Original method analysis\nExplain inputs and outputs.\n" + "\n".join(
        f"{field['label']}: {frozen[field['id']]}" for field in note_templates.FIELD_CATALOG)
    assert expected in model.calls[-1][1]["content"]
    assert frozen["paper_authors"] == "Original Author" and frozen["paper_year"] == "2025"
    assert frozen["reading_purpose"] == "理解论文方法与实验"
    for field in note_templates.FIELD_CATALOG:
        assert "{{" + field["id"] + "}}" not in model.calls[-1][1]["content"]
    assert "Changed later" not in str(model.calls)
    assert "禁止为填满模板编造" in model.calls[-1][0]["content"]
    assert task["payload"]["template_snapshot"]["version"] == 1
    assert task["payload"]["template_snapshot"]["body"] == original["body"]
    assert result["template_check"]["missing_sections"] == ["Original method analysis"]
    response = client.post("/api/chat/runs", json={
        "message": "Explain", "paper_ids": [indexed[0]], "template_id": template["id"]})
    assert response.status_code == 404


def test_template_expansion_is_literal_and_not_executable():
    template = {"body": "{{paper_title}} / {{reading_purpose}} / {{7*7}}"}
    values = note_templates.field_values([{"title": "Paper A"}, {"title": "Paper B"}], {"purpose": "Study"})
    result = note_templates.expand(template, values)
    assert result == "Paper A / Paper B / Study / {{7*7}}"
    values["paper_title"] = "$& {{reading_purpose}}"
    assert note_templates.expand(template, values) == "$& {{reading_purpose}} / Study / {{7*7}}"
    assert note_templates.expand({"body": "{{unregistered}}"}, values) == "{{unregistered}}"


def test_template_field_catalog_uses_real_metadata_and_profile(client, indexed):
    with store.connect() as db:
        db.execute("""UPDATE papers SET authors=?,year=2024,venue='Test Conference',
                   doi='10.0000/test',arxiv_id='2401.00001',pdf_url='https://example.org/test.pdf',
                   abstract='Synthetic abstract' WHERE id=?""", ('["Alice", "Bob"]', indexed[0]))
    client.put("/api/profile", json={
        "purpose": "Reproduce", "focus": "Experiments", "background": "CS",
        "research_domain": "Graphs", "known_topics": "GNN", "depth": "advanced",
        "language": "English", "framework": "PyTorch", "compute_budget": "One GPU"})
    data = client.get(f"/api/note-template-fields?paper_ids={indexed[0]}&skill_id=reproduce").json()
    values = data["values"]
    assert len(data["fields"]) == 20
    assert {field["id"] for field in data["fields"]} == set(values)
    assert {field["group"] for field in data["fields"]} == {"论文信息", "阅读档案", "本次阅读"}
    assert values["paper_authors"] == "Alice; Bob"
    assert values["paper_year"] == "2024" and values["paper_venue"] == "Test Conference"
    assert values["paper_doi"] == "10.0000/test" and values["paper_arxiv_id"] == "2401.00001"
    assert values["paper_pdf_url"] == "https://example.org/test.pdf"
    assert values["paper_abstract"] == "Synthetic abstract"
    assert values["reading_purpose"] == "Reproduce" and values["reading_focus"] == "Experiments"
    assert values["knowledge_background"] == "CS" and values["research_domain"] == "Graphs"
    assert values["known_topics"] == "GNN" and values["reading_depth"] == "深入"
    assert values["output_language"] == "English" and values["preferred_framework"] == "PyTorch"
    assert values["compute_budget"] == "One GPU"
    assert values["reading_mode"] == "复现指南" and values["paper_count"] == "1"
    assert len(values["note_date"]) == 10
    assert client.get("/api/note-template-fields?paper_ids=99999").status_code == 404
    assert client.get("/api/note-template-fields?skill_id=unknown").status_code == 422
    assert client.get("/api/note-template-fields?paper_ids=1&paper_ids=2&paper_ids=3").status_code == 422


def test_template_fields_preserve_comparison_order_and_missing_values(client, indexed, pdf_bytes):
    second_id, _ = documents.upload(pdf_bytes + b"\n", "Second paper")
    with store.connect() as db:
        db.execute("UPDATE papers SET title='First paper',authors=?,year=2020 WHERE id=?",
                   ('["First Author"]', indexed[0]))
    data = client.get(
        f"/api/note-template-fields?paper_ids={second_id}&paper_ids={indexed[0]}&skill_id=compare").json()
    values = data["values"]
    assert values["paper_title"] == "Second paper / First paper"
    assert values["paper_authors"] == "论文 A：未提供\n论文 B：First Author"
    assert values["paper_year"] == "论文 A：未提供\n论文 B：2020"
    assert values["research_domain"] == "未设置" and values["paper_count"] == "2"
    empty = client.get("/api/note-template-fields").json()["values"]
    assert empty["paper_title"] == "未选择论文" and empty["paper_count"] == "0"


def test_legacy_template_tasks_resolve_fields(client, indexed):
    template = client.post("/api/note-templates", json={
        "name": "Legacy", "body": "{{paper_title}} / {{reading_purpose}} / {{note_date}}"}).json()
    task = submit(ReadingRequest(message="Explain", paper_ids=[indexed[0]],
                                 template_id=template["id"], verify_evidence=False))
    fields = task["payload"].pop("template_values")
    model = FakeModel()
    run_reading(task, Guard(), model)
    expected = f"{fields['paper_title']} / {fields['reading_purpose']} / {fields['note_date']}"
    assert expected in model.calls[-1][1]["content"]


def test_backup_includes_originals_only_referenced_by_notes(client, indexed, monkeypatch, tmp_path):
    from scripts import maintenance
    task_id = completed(indexed, monkeypatch)
    digest = notebook.sources(task_id)[0]["file_hash"]
    documents.delete_paper(indexed[0])
    monkeypatch.setattr(maintenance, "DATA", settings.DATA)
    report = maintenance.backup(tmp_path / "backups")
    assert report["complete"] and f"{digest}.pdf" in report["pdf_files"]


def test_comparison_has_one_note_under_both_papers(client, indexed, pdf_bytes, monkeypatch):
    other_id, digest = documents.upload(pdf_bytes + b"\n", "Second test paper")
    ingest = store.enqueue("ingest", {"paper_id": other_id, "file_hash": digest})
    documents.process(ingest, Guard())
    with store.connect() as db:
        db.execute("UPDATE tasks SET status='succeeded' WHERE id=?", (ingest["id"],))
    import lit_agent.jobs as jobs
    monkeypatch.setattr(jobs, "Model", FakeModel)
    task = submit(ReadingRequest(message="Compare", paper_ids=[indexed[0], other_id],
                                 skill_id="compare", verify_evidence=False))
    Worker().run_once()
    assert store.get_task(task["id"])["status"] == "succeeded"
    first = client.get(f"/api/notes?paper_id={indexed[0]}").json()["items"]
    second = client.get(f"/api/notes?paper_id={other_id}").json()["items"]
    assert first[0]["task_id"] == second[0]["task_id"] == task["id"]
    assert len(first[0]["sources"]) == 2
    assert client.get(f"/api/notes/latest?paper_id={indexed[0]}").json() is None
    assert client.get(f"/api/notes/latest?paper_id={other_id}").json() is None
    for ids in ((indexed[0], other_id), (other_id, indexed[0])):
        assert client.get(f"/api/notes/latest?paper_id={ids[0]}&paper_id={ids[1]}").json() == {"task_id": task["id"]}
    single = completed(indexed, monkeypatch)
    assert client.get(f"/api/notes/latest?paper_id={indexed[0]}&paper_id={indexed[0]}").json() == {"task_id": single}
    assert client.get(f"/api/notes/latest?paper_id={indexed[0]}&paper_id={other_id}").json() == {"task_id": task["id"]}
