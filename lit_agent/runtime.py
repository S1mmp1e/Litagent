"""Budgeted model access, tool execution, and evidence-grounded reading."""

import copy
import hashlib
import json
import re
import time
from urllib.parse import urlsplit

from openai import OpenAI, AuthenticationError, RateLimitError, APIConnectionError
from pydantic import ConfigDict, Field, create_model

from . import model_config, retrieval, skills, store
from .settings import MAX_ROUNDS, MAX_TASK_SECONDS


class Cancelled(Exception):
    pass


class Guard:
    def __init__(self, task_id=None, timeout=None):
        self.task_id = task_id
        self.deadline = time.monotonic() + (MAX_TASK_SECONDS if timeout is None else timeout)

    def check(self):
        if time.monotonic() >= self.deadline:
            raise TimeoutError("任务超过执行时限，可缩小阅读范围后重试。")
        if self.task_id:
            task = store.get_task(self.task_id)
            if not task or task["cancel_requested"]:
                raise Cancelled("任务已取消。")

    def remaining(self):
        self.check()
        return max(0.1, self.deadline - time.monotonic())


def safe_error(error):
    # SDK exception messages may contain request bodies or credentials.
    if isinstance(error, (ValueError, TimeoutError, Cancelled)):
        return re.sub(r"sk-[A-Za-z0-9_-]{12,}", "[REDACTED]", str(error))[:400]
    if isinstance(error, AuthenticationError):
        return "模型服务认证失败（401）。请在“模型设置”中核对服务地址和 API Key。"
    if isinstance(error, RateLimitError):
        return "模型服务限流或配额不足。请检查账户状态，稍后重试。"
    if isinstance(error, APIConnectionError):
        return "无法连接模型服务。请检查服务地址和网络。"
    return f"{type(error).__name__}：操作失败，请检查网络、服务端配置或依赖；未记录密钥和请求正文。"


class Model:
    def __init__(self, guard):
        config = model_config.current_config()
        key = config.api_key.get_secret_value()
        if not key:
            raise ValueError("尚未配置 API Key，请打开“模型设置”填写，或配置服务端 DEEPSEEK_API_KEY。")
        self.client = OpenAI(api_key=key, base_url=config.base_url,
                             max_retries=0)
        self.model = config.model
        # Current official DeepSeek models default to thinking; retain our bounded non-thinking workflow.
        self.completion_options = (
            {"extra_body": {"thinking": {"type": "disabled"}}}
            if urlsplit(config.base_url).hostname == "api.deepseek.com"
            and (self.model == "deepseek-flash" or self.model.startswith("deepseek-v4-")) else {}
        )
        self.guard = guard
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "model_calls": 0}
        self.last_completion = None

    def close(self):
        self.client.close()

    def complete(self, messages, tools=None, on_delta=None, max_tokens=2500):
        self.guard.check()
        if self.usage["model_calls"] >= 32:
            raise ValueError("达到单次任务 32 次模型调用预算，请缩小阅读范围。")
        if type(max_tokens) is not int or max_tokens < 1:
            raise ValueError("模型输出 Token 预算必须为正整数。")
        kwargs = {"model": self.model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.2,
                  "timeout": min(45, self.guard.remaining())}
        kwargs.update(self.completion_options)
        if tools:
            kwargs.update(tools=tools, tool_choice="auto", parallel_tool_calls=False)
        # No implicit SDK retry: a tool transaction is never replayed by this client.
        self.usage["model_calls"] += 1
        self.last_completion = None
        usage, finish = None, None
        streamed = bool(on_delta and not tools)
        started = time.monotonic()
        try:
            if streamed:
                pieces = []
                with self.client.chat.completions.create(
                    **kwargs, stream=True, stream_options={"include_usage": True},
                ) as stream:
                    for item in stream:
                        self.guard.check()
                        if item.usage:
                            usage = item.usage
                        if item.choices:
                            choice = item.choices[0]
                            finish = choice.finish_reason or finish
                            text = choice.delta.content or ""
                            if text:
                                pieces.append(text)
                                on_delta(text)
                check_finish_reason(finish, max_tokens)
                return {"role": "assistant", "content": "".join(pieces)}
            response = self.client.chat.completions.create(**kwargs)
            usage = response.usage
            finish = response.choices[0].finish_reason
            self.guard.check()
            check_finish_reason(finish, max_tokens, allow_tools=bool(tools))
            return response.choices[0].message.model_dump(exclude_none=True)
        finally:
            if usage:
                self.usage["prompt_tokens"] += usage.prompt_tokens
                self.usage["completion_tokens"] += usage.completion_tokens
            self.last_completion = {
                "finish_reason": finish, "max_tokens": max_tokens, "streamed": streamed,
                "prompt_tokens": usage.prompt_tokens if usage else None,
                "completion_tokens": usage.completion_tokens if usage else None,
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }
            if self.guard.task_id:
                store.emit(self.guard.task_id, "model_response", self.last_completion)


def check_finish_reason(reason, max_tokens, allow_tools=False):
    if reason == "stop" or (allow_tools and reason == "tool_calls"):
        return
    if reason == "length":
        raise ValueError(
            f"生成达到本次长度限制（输出预算 {max_tokens} tokens，finish_reason=length），"
            "内容尚未完成。可提高输出预算或缩短笔记要求后重试；未保存为正式笔记。")
    if reason == "content_filter":
        raise ValueError("模型服务因内容过滤停止生成；未保存为正式笔记。")
    if reason == "insufficient_system_resource":
        raise ValueError("模型服务因算力资源不足中断生成，请稍后重试；未保存为正式笔记。")
    if reason is None:
        raise ValueError("生成流结束但未收到结束标记，可能连接中断；未保存为正式笔记。")
    raise ValueError("模型返回异常结束状态；未保存为正式笔记，请查看任务的 model_response 事件。")


def validate_tool(definition, arguments):
    fields = {}
    spec = definition["function"]["parameters"]
    for key, prop in spec.get("properties", {}).items():
        datatype = int if prop["type"] == "integer" else str
        constraints = {}
        if datatype is str:
            constraints["max_length"] = 2000
        if key in ("paper_id", "category_id"):
            constraints["ge"] = 1
        if key in ("max_results", "limit"):
            constraints.update(ge=1, le=30 if key == "max_results" else 100)
        fields[key] = (datatype if key in spec.get("required", []) else datatype | None,
                       Field(default=... if key in spec.get("required", []) else None, **constraints))
    validator = create_model("ToolArguments", __config__=ConfigDict(extra="forbid", strict=True), **fields)
    return validator.model_validate(arguments).model_dump(exclude_none=True)


def conversation(messages, model, guard, task_id=None):
    from .agent_openai import SYSTEM_PROMPT, TOOLS, TOOL_FUNCTIONS

    messages = copy.deepcopy(messages)
    if not messages or messages[0]["role"] != "system":
        messages.insert(0, {"role": "system", "content": skills.BASE + "\n" + SYSTEM_PROMPT})
    definitions = {t["function"]["name"]: t for t in TOOLS}
    for round_no in range(MAX_ROUNDS):
        guard.check()
        response = model.complete(messages, tools=TOOLS)
        calls = response.get("tool_calls") or []
        if len(calls) > 8:
            raise ValueError("单轮工具请求过多。")
        messages.append(response)
        if not calls:
            return response.get("content") or "", messages
        for call in calls:
            guard.check()
            name = call["function"]["name"]
            try:
                if name not in definitions:
                    raise ValueError("工具不在服务端白名单。")
                arguments = validate_tool(definitions[name], json.loads(call["function"]["arguments"]))
            except (ValueError, TypeError) as error:
                result = store.dumps({"error": safe_error(ValueError("工具参数不合法。"))})
            else:
                if task_id:
                    store.checkpoint(task_id, "executing_tool", {"round": round_no, "tool": name})
                    store.emit(task_id, "tool_start", {"name": name, "round": round_no})
                try:
                    result = TOOL_FUNCTIONS[name](**arguments)
                except Exception as error:
                    result = store.dumps({"error": safe_error(error)})
                guard.check()
                if task_id:
                    store.emit(task_id, "tool_end", {"name": name})
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
        if task_id:
            store.checkpoint(task_id, "tool_round_complete", {"messages": messages})
    raise ValueError("达到 12 轮工具调用上限，已停止执行。")


def evidence_text(chunks):
    return "\n\n".join(
        f"[cite:{c['id']}] paper={c['paper_id']} page={c['page']}\n{c['text']}" for c in chunks
    )


def check_citations(answer, chunks):
    allowed = {c["id"]: c for c in chunks}
    used = list(dict.fromkeys(re.findall(r"\[cite:([^\]]+)\]", answer)))
    if any(key not in allowed for key in used):
        raise ValueError("回答包含不在当前证据中的引用，未保存为正式笔记。")
    # This checks reference existence, not semantic entailment.
    return [{"id": key, "paper_id": allowed[key]["paper_id"], "page": allowed[key]["page"],
             "url": f"/api/chunks/{key}"} for key in used]


def run_reading(task, guard, model):
    from .reading_workflow import run
    return run(task, guard, model)


def chat_context(session_id, profile):
    row = store.one("SELECT * FROM chat_sessions WHERE id=?", (session_id,))
    if not row:
        raise ValueError("会话不存在。")
    signature = hashlib.sha256(store.dumps(profile).encode()).hexdigest()
    previous = json.loads(row["agent_messages"] or "[]")
    # A profile change starts fresh model context; visible conversation is retained.
    if row["context_signature"] != signature:
        previous = []
    return row, previous, signature
