import json
from types import SimpleNamespace

import pytest

from conftest import FakeModel
from lit_agent import jobs, runtime, settings, store
from lit_agent.api import ReadingRequest, submit
from lit_agent.runtime import Guard, Model, check_finish_reason, run_reading


def fake_provider(monkeypatch, reason="stop", tokens=3000, transport_error=False):
    calls = []
    usage = SimpleNamespace(prompt_tokens=8445, completion_tokens=tokens)
    message = SimpleNamespace(model_dump=lambda **kwargs: {"role": "assistant", "content": "Complete answer"})

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def __iter__(self):
            yield SimpleNamespace(usage=None, choices=[
                SimpleNamespace(finish_reason=None, delta=SimpleNamespace(content="Partial answer"))])
            if transport_error:
                raise TimeoutError("Synthetic stream timeout")
            yield SimpleNamespace(usage=None, choices=[
                SimpleNamespace(finish_reason=reason, delta=SimpleNamespace(content=""))])
            yield SimpleNamespace(usage=usage, choices=[])

    def create(**kwargs):
        calls.append(kwargs)
        if kwargs.get("stream"):
            return Stream()
        return SimpleNamespace(usage=usage, choices=[SimpleNamespace(finish_reason=reason, message=message)])

    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-local-test-key")
    monkeypatch.setattr(runtime, "OpenAI", lambda **kwargs: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)), close=lambda: None))
    return calls


def test_stream_finishes_with_larger_budget_and_records_actual_usage(monkeypatch):
    calls = fake_provider(monkeypatch)
    task = store.enqueue("reading", {})
    model = Model(Guard(task["id"]))
    pieces = []
    result = model.complete([{"role": "user", "content": "Explain"}], on_delta=pieces.append,
                            max_tokens=8192)
    assert result["content"] == "".join(pieces) == "Partial answer"
    assert calls[0]["max_tokens"] == 8192 and len(calls) == 1
    assert model.usage == {"prompt_tokens": 8445, "completion_tokens": 3000, "model_calls": 1}
    assert model.last_completion["finish_reason"] == "stop"
    event = store.one("SELECT data FROM task_events WHERE task_id=? AND event='model_response'", (task["id"],))
    assert json.loads(event["data"])["max_tokens"] == 8192 and "Explain" not in event["data"]


@pytest.mark.parametrize("streamed", [False, True])
@pytest.mark.parametrize(("reason", "expected"), [
    ("length", "输出预算 2500"),
    ("content_filter", "内容过滤"),
    ("insufficient_system_resource", "算力资源不足"),
    (None, "未收到结束标记"),
])
def test_incomplete_completions_are_distinguished_and_never_retried(monkeypatch, streamed, reason, expected):
    calls = fake_provider(monkeypatch, reason, tokens=2500)
    model = Model(Guard())
    with pytest.raises(ValueError, match=expected):
        model.complete([{"role": "user", "content": "Explain"}],
                       on_delta=(lambda text: None) if streamed else None)
    assert len(calls) == 1 and calls[0]["max_tokens"] == 2500
    assert model.usage["completion_tokens"] == 2500
    assert model.last_completion["finish_reason"] == reason


def test_transport_failure_does_not_invent_usage_or_a_finish_reason(monkeypatch):
    fake_provider(monkeypatch, transport_error=True)
    model = Model(Guard())
    with pytest.raises(TimeoutError, match="Synthetic stream timeout"):
        model.complete([{"role": "user", "content": "Explain"}], on_delta=lambda text: None)
    assert model.last_completion["finish_reason"] is None
    assert model.last_completion["completion_tokens"] is None
    assert model.usage["model_calls"] == 1


def test_tool_call_finish_is_accepted_only_for_tool_enabled_requests(monkeypatch):
    fake_provider(monkeypatch, reason="tool_calls")
    model = Model(Guard())
    assert model.complete([], tools=[{"type": "function"}])["content"] == "Complete answer"
    with pytest.raises(ValueError):
        model.complete([])


def test_final_note_budget_is_separate_from_extraction(indexed):
    class Budgets(FakeModel):
        def __init__(self):
            super().__init__()
            self.budgets = []

        def complete(self, messages, **kwargs):
            self.budgets.append(kwargs.get("max_tokens", 2500))
            return super().complete(messages, **kwargs)

    task = submit(ReadingRequest(message="Explain", paper_ids=[indexed[0]], verify_evidence=False))
    model = Budgets()
    run_reading(task, Guard(), model)
    assert model.budgets[:-1] and set(model.budgets[:-1]) == {2500}
    assert model.budgets[-1] == settings.READING_MAX_OUTPUT_TOKENS
    assert store.get_task(task["id"])["checkpoint"]["generation_max_tokens"] == settings.READING_MAX_OUTPUT_TOKENS


def test_worker_keeps_failed_call_usage_and_diagnostics_without_saving_partial_note(indexed, monkeypatch):
    guards = []

    class Truncated(FakeModel):
        def __init__(self, guard):
            super().__init__(guard)
            guards.append(guard.remaining())

        def complete(self, messages, **kwargs):
            response = super().complete(messages, **kwargs)
            if kwargs.get("on_delta"):
                self.last_completion = {"finish_reason": "length", "max_tokens": kwargs["max_tokens"],
                                        "completion_tokens": kwargs["max_tokens"]}
                check_finish_reason("length", kwargs["max_tokens"])
            return response

    monkeypatch.setattr(jobs, "Model", Truncated)
    task = submit(ReadingRequest(message="Explain", paper_ids=[indexed[0]], verify_evidence=False))
    jobs.Worker().run_once()
    final = store.get_task(task["id"])
    assert final["status"] == "failed" and "输出预算" in final["error"]
    assert final["checkpoint"]["last_completion"]["finish_reason"] == "length"
    assert final["checkpoint"]["usage"]["model_calls"] >= 2
    assert not store.rows("SELECT * FROM notes WHERE task_id=?", (task["id"],))
    assert final["result"] is None and "answer" not in final["checkpoint"]
    assert settings.MAX_READING_SECONDS - 5 < guards[0] <= settings.MAX_READING_SECONDS
