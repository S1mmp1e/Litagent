import json
from copy import deepcopy

import pytest

from conftest import FakeModel
from lit_agent import retrieval, skills, store
from lit_agent.api import ReadingRequest, submit
from lit_agent.evidence_review import claim_units, review
from lit_agent.reading_workflow import reading_history
from lit_agent.runtime import Guard, run_reading


def reading(paper_id, **options):
    return submit(ReadingRequest(message="Explain the method", paper_ids=[paper_id],
                                 verify_evidence=False, **options))


def finish(task, result):
    with store.connect() as db:
        db.execute("UPDATE tasks SET status='succeeded',result=? WHERE id=?",
                   (store.dumps(result), task["id"]))


class Planner(FakeModel):
    def complete(self, messages, **kwargs):
        response = super().complete(messages, **kwargs)
        system = messages[0]["content"]
        if '{"query"' in system:
            response["content"] = '{"query":"How does the graph method work?"}'
        elif '{"missing"' in system:
            response["content"] = '{"missing":[0,1]}'
        return response


def test_skill_snapshot_freezes_evidence_extraction(indexed, monkeypatch):
    task = reading(indexed[0], skill_id="reproduce")
    captured = deepcopy(task["payload"]["skill_snapshot"])
    monkeypatch.setitem(skills.WORKFLOWS, "reproduce", ("changed extraction", [], "changed request"))
    model = Planner()
    run_reading(task, Guard(), model)
    assert captured["map_focus"] in model.calls[0][0]["content"]
    assert "changed extraction" not in model.calls[0][0]["content"]
    assert captured == store.get_task(task["id"])["payload"]["skill_snapshot"]
    for key in skills.REGISTRY:
        assert skills.snapshot(key)["instruction"].strip()


def test_reproduction_plans_at_most_two_scoped_queries(indexed, monkeypatch):
    calls = []
    original = retrieval.search

    def search(query, paper_ids, **kwargs):
        calls.append((query, paper_ids))
        return original(query, paper_ids, **kwargs)

    monkeypatch.setattr(retrieval, "search", search)
    task = reading(indexed[0], skill_id="reproduce")
    result = run_reading(task, Guard(), Planner())
    spec = task["payload"]["skill_snapshot"]
    assert [call[0] for call in calls] == spec["gap_queries"]
    assert all(call[1] == [indexed[0]] for call in calls)
    assert result["workflow"]["supplement_queries"] == 2
    assert "定向补查原文" in store.get_task(task["id"])["checkpoint"]["generation_messages"][1]["content"]


def test_invalid_gap_plan_is_bounded(indexed, monkeypatch):
    calls = []
    monkeypatch.setattr(retrieval, "search", lambda q, ids, **kw: calls.append(q) or [])
    result = run_reading(reading(indexed[0], skill_id="experiment_audit"), Guard(), FakeModel())
    assert len(calls) == result["workflow"]["supplement_queries"] == 2


def test_material_cache_reuses_extraction_but_regenerates_personalized_answer(indexed):
    first = reading(indexed[0])
    result = run_reading(first, Guard(), FakeModel())
    assert result["cache_hits"] == 0
    skills.save_profile({**skills.profile(), "compute_budget": "one CPU"})
    second = reading(indexed[0])
    model = FakeModel()
    result = run_reading(second, Guard(), model)
    assert result["cache_hits"] > 0
    assert len(model.calls) == 1
    assert "one CPU" in model.calls[0][0]["content"]


@pytest.mark.parametrize("change", ["model", "skill", "version", "text", "disabled"])
def test_material_cache_invalidates_relevant_changes(indexed, change):
    run_reading(reading(indexed[0]), Guard(), FakeModel())
    options = {"skill_id": "deep_read"} if change == "skill" else {}
    if change == "disabled":
        options["use_cache"] = False
    task = reading(indexed[0], **options)
    model = FakeModel()
    if change == "model":
        model.model = "another-model"
    elif change == "version":
        task["payload"]["skill_snapshot"]["version"] = "new-version"
    elif change == "text":
        with store.connect() as db:
            db.execute("UPDATE chunks SET text=text || ' revised source'")
    result = run_reading(task, Guard(), model)
    assert result["cache_hits"] == 0 and len(model.calls) >= 2


def history_fixture(paper_id, count=4):
    doc_id = store.one("SELECT id FROM documents WHERE paper_id=?", (paper_id,))["id"]
    session = None
    for index in range(count):
        task = reading(paper_id, session_id=session)
        session = task["session_id"]
        finish(task, {"document_ids": [doc_id], "answer": f"previous {index} [cite:old-marker]"})
    return session, doc_id


def test_followup_rewrites_with_last_three_scoped_turns(indexed):
    session, doc_id = history_fixture(indexed[0])
    task = reading(indexed[0], action="question", session_id=session)
    model = Planner()
    result = run_reading(task, Guard(), model)
    assert result["history_turns"] == 3
    assert result["query"] == "How does the graph method work?"
    context = json.loads(model.calls[0][1]["content"])
    assert [turn["answer"] for turn in context["history"]] == ["previous 1 ", "previous 2 ", "previous 3 "]
    assert "old-marker" not in str(model.calls)
    assert result["document_ids"] == [doc_id]


def test_followup_rejects_other_scope_and_profile(indexed):
    session, doc_id = history_fixture(indexed[0], 1)
    task = reading(indexed[0], action="question", session_id=session)
    assert reading_history(task, [doc_id])
    assert not reading_history(task, [doc_id + 100])
    task["payload"]["profile"]["focus"] = "new focus"
    assert not reading_history(task, [doc_id])


def test_malformed_rewrite_falls_back_without_losing_question(indexed):
    session, _ = history_fixture(indexed[0], 1)
    task = reading(indexed[0], action="question", session_id=session)
    result = run_reading(task, Guard(), FakeModel())
    assert result["query"].endswith(task["payload"]["message"])
    assert result["citations"]


class ReviewModel(FakeModel):
    def __init__(self, output):
        super().__init__()
        self.output = output

    def complete(self, messages, **kwargs):
        response = super().complete(messages, **kwargs)
        response["content"] = self.output
        return response


@pytest.mark.parametrize("variant,expected", [
    ("valid", "supported"), ("forged", "unverified"),
    ("wrong_source", "unverified"), ("no_quote", "unverified"),
    ("malformed", "unverified"), ("duplicate", "unverified"),
])
def test_evidence_review_requires_exact_quote_from_own_citation(variant, expected):
    answer = "The experiment compares a baseline under fixed conditions. [cite:source-a]"
    chunks = [{"id": "source-a", "text": "The experiment compares a baseline under fixed conditions."},
              {"id": "source-b", "text": "A separate claim on another page."}]
    verdict = {"id": 0, "verdict": "supported", "reason": "Matches the source.",
               "quotes": {"source-a": "compares a baseline under fixed conditions."}}
    if variant == "forged":
        verdict["quotes"]["source-a"] = "This fabricated sentence was never in the paper."
    elif variant == "wrong_source":
        verdict["quotes"] = {"source-b": "A separate claim on another page."}
    elif variant == "no_quote":
        verdict["quotes"] = {}
    output = store.dumps({"claims": [verdict, verdict] if variant == "duplicate" else [verdict]})
    if variant == "malformed":
        output = "not JSON"
    result = review(answer, chunks, ReviewModel(output), Guard())
    assert result["automatic"] is True
    assert result["claims"][0]["verdict"] == expected
    assert result["evaluated_units"] == (1 if expected == "supported" else 0)


def test_review_budget_and_heading_do_not_hide_unchecked_text():
    text = "## Method\nThe method supports the observed evaluation result. [cite:a]"
    units = claim_units(text)
    assert len(units) == 1 and units[0]["text"].startswith("The method")
    long_answer = ("Very long claim. " * 400) + " [cite:a]"
    model = ReviewModel('{"claims":[]}')
    result = review(long_answer, [{"id": "a", "text": "Source text."}], model, Guard())
    assert result["claims"][0]["text"] == long_answer
    assert result["evaluated_units"] == 0 and not model.calls
    many = "\n\n".join(f"Claim number {i} has enough text for checking. [cite:a]" for i in range(10))
    review(many, [{"id": "a", "text": "Source text."}], model, Guard())
    assert len(json.loads(model.calls[-1][1]["content"])["claims"]) == 8


def test_low_review_budget_preserves_answer_without_model_call():
    guard = Guard()
    guard.deadline -= 175
    model = ReviewModel('{"claims":[]}')
    result = review("A meaningful factual statement that needs a citation. [cite:a]",
                    [{"id": "a", "text": "Source text."}], model, guard)
    assert result["status"] == "not_checked" and not model.calls


def test_neighbors_respect_scope_and_preserve_primary_ranking(indexed):
    primary = retrieval.search("method", [indexed[0]], limit=2)
    expanded = retrieval.with_neighbors(primary, limit=4)
    assert expanded[:len(primary)] == primary
    assert len({c["id"] for c in expanded}) == len(expanded) <= 4
    assert all(c["paper_id"] == indexed[0] for c in expanded)


def test_additive_migration_preserves_library_and_cached_material(indexed):
    run_reading(reading(indexed[0]), Guard(), FakeModel())
    before = {table: store.rows(f"SELECT * FROM {table}") for table in
              ("papers", "documents", "chat_sessions", "reading_cache")}
    store.migrate()
    assert before == {table: store.rows(f"SELECT * FROM {table}") for table in before}
