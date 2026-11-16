import json
import time

import pytest

from lit_agent import skills, store
from lit_agent.agent_openai import TOOLS
from lit_agent.chat_history import create_session
from lit_agent.jobs import Worker
from lit_agent.runtime import (Cancelled, Guard, Model, chat_context, check_citations,
                               conversation, run_reading, safe_error, validate_tool)
from conftest import FakeModel


def make_reading(paper_id, action="summary"):
    return store.enqueue("reading", {"paper_ids": [paper_id], "message": "Explain the method",
                         "skill_id": "deep_read", "action": action, "profile": skills.profile()}, create_session())


def test_full_reading_maps_all_chunks_before_synthesis(indexed):
    task = make_reading(indexed[0])
    model = FakeModel()
    result = run_reading(task, Guard(), model)
    expected = {c["id"] for c in store.rows("SELECT id FROM chunks")}
    assert {c["id"] for c in result["citations"]} == expected
    assert len(model.calls) >= 2
    assert store.get_task(task["id"])["checkpoint"]["generation_messages"]


def test_reading_resumes_completed_maps(indexed):
    task = make_reading(indexed[0])
    original = run_reading(task, Guard(), FakeModel())
    resumed = FakeModel()
    result = run_reading(store.get_task(task["id"]), Guard(), resumed)
    assert resumed.calls == []
    assert result["answer"] == original["answer"]
    assert result["usage"] == original["usage"]


def test_question_uses_scoped_retrieval(indexed):
    task = make_reading(indexed[0], "question")
    model = FakeModel()
    result = run_reading(task, Guard(), model)
    assert result["citations"] and result["history_turns"] == 0
    assert len(model.calls) == 2
    assert "论文材料" in model.calls[0][-1]["content"]
    assert "sources" in json.loads(model.calls[1][-1]["content"])
    assert result["evidence_review"]["status"] == "not_checked"


def test_question_abstains_when_retrieval_has_no_evidence(indexed, monkeypatch):
    import lit_agent.runtime as runtime
    task = make_reading(indexed[0], "question")
    monkeypatch.setattr(runtime.retrieval, "search", lambda *args, **kwargs: [])
    model = FakeModel()
    result = run_reading(task, Guard(), model)
    assert "无法据此回答" in result["answer"]
    assert not result["citations"] and not model.calls


def test_invalid_citation_cannot_be_accepted():
    with pytest.raises(ValueError, match="引用"):
        check_citations("claim [cite:invented]", [])


def test_model_key_is_required(monkeypatch):
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        Model(Guard())


def test_model_call_budget_stops_before_network(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-local-test-key")
    model = Model(Guard())
    model.usage["model_calls"] = 32
    try:
        with pytest.raises(ValueError, match="32"):
            model.complete([{"role": "user", "content": "No network call should occur."}])
    finally:
        model.close()


@pytest.mark.parametrize("args", [{"max_results": 100, "query": "x"}, {"query": 1}, {"query": "x", "evil": 1}])
def test_tool_parameters_reject_invalid_input(args):
    with pytest.raises(ValueError):
        validate_tool(TOOLS[0], args)


def test_max_rounds_terminates_tool_loop():
    class Looper(FakeModel):
        def complete(self, *args, **kwargs):
            return {"role": "assistant", "tool_calls": [{"id": "t", "type": "function",
                    "function": {"name": "not_registered", "arguments": "{}"}}]}
    with pytest.raises(ValueError, match="12"):
        conversation([], Looper(), Guard())


def test_timeout_and_cancellation():
    guard = Guard()
    guard.deadline = time.monotonic() - 1
    with pytest.raises(TimeoutError):
        guard.check()
    task = store.enqueue("ingest", {"paper_id": 999})
    with store.connect() as db:
        db.execute("UPDATE tasks SET cancel_requested=1 WHERE id=?", (task["id"],))
    with pytest.raises(Cancelled):
        Guard(task["id"]).check()


def test_profile_change_invalidates_model_context_not_history():
    session_id = create_session()
    profile = skills.profile()
    _, _, signature = chat_context(session_id, profile)
    context = [{"role": "system", "content": "old"}, {"role": "assistant", "tool_calls": []}]
    with store.connect() as db:
        db.execute("UPDATE chat_sessions SET agent_messages=?,context_signature=?,messages=? WHERE id=?",
                   (json.dumps(context), signature, '[{"role":"user","content":"visible"}]', session_id))
    assert chat_context(session_id, profile)[1] == context
    changed = {**profile, "focus": "new"}
    row, messages, _ = chat_context(session_id, changed)
    assert not messages and "visible" in row["messages"]


def test_worker_persists_answer_note_and_events(indexed, monkeypatch):
    import lit_agent.jobs as jobs
    monkeypatch.setattr(jobs, "Model", FakeModel)
    task = make_reading(indexed[0])
    assert Worker().run_once()
    final = store.get_task(task["id"])
    assert final["status"] == "succeeded", final["error"]
    assert store.rows("SELECT * FROM notes WHERE task_id=?", (task["id"],))
    assert store.rows("SELECT * FROM task_events WHERE task_id=? AND event='done'", (task["id"],))


def test_same_session_cannot_race():
    session_id = create_session()
    store.enqueue("chat", {"message": "hello"}, session_id)
    with pytest.raises(ValueError, match="已有任务"):
        store.enqueue("chat", {"message": "world"}, session_id)


def test_error_redaction():
    secret = "sk-" + "a" * 30
    assert secret not in safe_error(ValueError(secret))
    assert secret not in safe_error(RuntimeError(secret))


def test_skill_registry_and_profile_are_used():
    intro = skills.skill_prompt("deep_read", {"depth": "intro", "focus": "intuition"})
    advanced = skills.skill_prompt("deep_read", {"depth": "advanced", "focus": "reproduce"})
    assert intro != advanced
    assert "intuition" in intro and "reproduce" in advanced
    with pytest.raises(ValueError):
        skills.skill_prompt("../../unknown", {})
